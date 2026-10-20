"""The paid odds snapshot jobs, each snapshot saved raw before it is parsed.

One bulk call returns every game of a sport for 3 credits, so slots are per moment, not per game.
NFL: Tuesday's opening lines, a morning snapshot on Thursday, Saturday and Sunday, and one shortly
before each distinct kickoff time (that last one is the closing-line reference for CLV).
NBA: a 10am snapshot every day, and one shortly before the first tip and one before the last tip
of each night (games in between use ESPN's close, so the sport stays inside the free credits).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app.domain.models import Sport
from app.ingest.odds_snapshot import PROVIDER, fetch_snapshot, ingest_snapshot
from app.jobs.runner import JobContext, get_slot_info, update_slot
from app.jobs.slots import CENTRAL, Slot
from app.storage.games_repository import GamesRepository, aware
from app.storage.models import JobSlot
from app.storage.repository import OddsRepository

logger = logging.getLogger(__name__)
NAME = "odds_snapshot"
PRE_KICK_LEAD = timedelta(minutes=30)
DAILY_START_HOUR = 10
DAILY_RETRY = timedelta(hours=1)
PRE_KICK_RETRY = timedelta(minutes=10)
NBA_SLATE_END_HOUR = 6  # a Central day's NBA slate runs to 06:00 the next morning
NBA_OPEN_LENGTH = timedelta(hours=2)
NBA_CLUSTER_GAP = timedelta(minutes=90)  # tips closer than this share one pre-tip snapshot
# weekday (Mon=0) -> (label, how long the window stays open after 10:00 CT)
DAILY = {
    1: ("open", timedelta(hours=12)),
    3: ("thu", timedelta(hours=2)),
    5: ("sat", timedelta(hours=2)),
    6: ("sun", timedelta(minutes=90)),
}


def week_end(day: date) -> datetime:
    """The Tuesday that closes the NFL week `day` belongs to. Next week's lines are not posted
    yet on a Saturday, so a snapshot is only judged on the games of its own week."""
    ahead = (1 - day.weekday()) % 7 or 7
    return datetime.combine(day + timedelta(days=ahead), time(0), CENTRAL)


@dataclass(frozen=True)
class OddsSlot(Slot):
    targets: tuple[datetime, datetime] = (datetime.min, datetime.min)  # kickoff window to score
    retry_after: timedelta = DAILY_RETRY


def _now() -> datetime:
    return datetime.now(timezone.utc)


def daily_odds_slots(now: datetime, name: str = NAME) -> list[OddsSlot]:
    """Today's and yesterday's scheduled snapshots (yesterday's so a leftover is closed out)."""
    today = now.astimezone(CENTRAL).date()
    slots = []
    for day in (today - timedelta(days=1), today):
        if day.weekday() not in DAILY:
            continue
        label, length = DAILY[day.weekday()]
        start = datetime.combine(day, time(DAILY_START_HOUR), CENTRAL)
        slots.append(
            OddsSlot(
                f"{name}:nfl:{day.isoformat()}:{label}",
                start,
                start + length,
                targets=(start, week_end(day)),
                retry_after=DAILY_RETRY,
            )
        )
    return slots


def pre_kick_slots(
    kickoffs: set[datetime], name: str = NAME, sport: str = "nfl"
) -> list[OddsSlot]:
    """One slot per distinct kickoff: the last 30 minutes before it."""
    slots = []
    for kickoff in sorted(kickoffs):
        local = kickoff.astimezone(CENTRAL)
        slots.append(
            OddsSlot(
                f"{name}:{sport}:{local.date().isoformat()}:pre_kick:{local:%H%M}",
                kickoff - PRE_KICK_LEAD,
                kickoff,
                targets=(kickoff, kickoff + timedelta(minutes=1)),
                retry_after=PRE_KICK_RETRY,
            )
        )
    return slots


def nba_open_slots(now: datetime, name: str) -> list[OddsSlot]:
    """Every day at 10:00 Central; judged on that day's slate. Yesterday's is kept so a leftover
    is closed out."""
    today = now.astimezone(CENTRAL).date()
    slots = []
    for day in (today - timedelta(days=1), today):
        start = datetime.combine(day, time(DAILY_START_HOUR), CENTRAL)
        slate_end = datetime.combine(day + timedelta(days=1), time(NBA_SLATE_END_HOUR), CENTRAL)
        slots.append(
            OddsSlot(
                f"{name}:nba:{day.isoformat()}:open",
                start,
                start + NBA_OPEN_LENGTH,
                targets=(start, slate_end),
                retry_after=DAILY_RETRY,
            )
        )
    return slots


def nba_pre_tip_slots(tips: set[datetime], name: str) -> list[OddsSlot]:
    """Per Central date: a snapshot before the first tip, and one before the last tip when it is
    at least 90 minutes after the first. The games between get ESPN's close instead."""
    by_date: dict = {}
    for tip in tips:
        by_date.setdefault(tip.astimezone(CENTRAL).date(), []).append(tip)
    chosen: set[datetime] = set()
    for night in by_date.values():
        first, last = min(night), max(night)
        chosen.add(first)
        if last - first >= NBA_CLUSTER_GAP:
            chosen.add(last)
    return pre_kick_slots(chosen, name, "nba")


class OddsSnapshotJob:
    max_attempts = 2  # a partial result is retried once; each call costs 3 credits

    def __init__(self, sport: Sport = Sport.NFL, name: str = NAME) -> None:
        self.sport, self.name = sport, name

    def slots(self, now: datetime) -> list[Slot]:
        return []  # which slots exist depends on the schedule in the database: plan_slots

    async def plan_slots(self, ctx: JobContext, now: datetime) -> list[Slot]:
        if (ctx.odds is None and not ctx.odds_planning_enabled) or ctx.settings is None:
            return []  # fixture mode, or no key: nothing to spend
        nba = self.sport is Sport.NBA
        if nba:
            # A finished game still anchors its night: the first and last tip are judged on the
            # whole Central date (yesterday's too), or a matinee going final would promote the
            # evening game to "first tip" and buy another snapshot.
            midnight = datetime.combine(
                now.astimezone(CENTRAL).date() - timedelta(days=1), time(0), CENTRAL
            )
            nearby = await GamesRepository(ctx.session_factory).scheduled_games(
                self.sport.value, midnight, now + timedelta(hours=24),
                statuses=("scheduled", "in_progress", "final"),
            )
        else:
            nearby = await GamesRepository(ctx.session_factory).scheduled_games(
                self.sport.value, now - timedelta(hours=12), now + timedelta(hours=12)
            )
        tips = {g.start_time for g in nearby}
        if nba:
            planned = [*nba_open_slots(now, self.name), *nba_pre_tip_slots(tips, self.name)]
        else:
            planned = [*daily_odds_slots(now, self.name), *pre_kick_slots(tips, self.name)]
        async with ctx.session_factory() as session:
            rows = (
                await session.scalars(select(JobSlot).where(JobSlot.job == self.name))
            ).all()
        by_key = {row.slot_key: row for row in rows}
        slots: list[Slot] = []
        for slot in planned:
            row = by_key.get(slot.key)
            if row and row.state == "partial":
                if now - aware(row.updated_at) < slot.retry_after:
                    continue  # let the books post their lines before paying again
            slots.append(slot)
        planned_keys = {slot.key for slot in planned}
        for row in rows:  # a paid response that was never parsed must not be forgotten
            if row.state == "raw_saved" and row.slot_key not in planned_keys:
                when = aware(row.updated_at)
                end = when + timedelta(days=1) if nba else week_end(when.astimezone(CENTRAL).date())
                slots.append(
                    OddsSlot(row.slot_key, when, when, targets=(when - timedelta(days=1), end))
                )
        return slots

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        if ctx.odds is None or ctx.settings is None:
            # Only reachable by a manual `python -m app.jobs odds_snapshot`: run-due plans no slots.
            raise RuntimeError(f"{self.name} needs PROVIDER_MODE=live and a THE_ODDS_API_KEY")
        now = _now()
        manual_end = (
            now + timedelta(hours=36)
            if self.sport is Sport.NBA
            else week_end(now.astimezone(CENTRAL).date())
        )
        targets = getattr(slot, "targets", None) or (now, manual_end)  # a manual run
        info = await get_slot_info(ctx, self.name, slot.key)
        quota: Optional[int] = None
        if info.state == "raw_saved" and info.raw_path:
            path = Path(info.raw_path)  # we already paid: parse what is on disk, no new call
        else:
            if not await GamesRepository(ctx.session_factory).scheduled_games(
                self.sport.value, *targets
            ):
                return "empty"
            known = await OddsRepository(ctx.session_factory).latest_quota(PROVIDER)
            if known is not None and known <= ctx.settings.provider_min_quota_remaining:
                logger.warning("odds_snapshot skipped: %s credits left", known)
                return "skipped_quota"
            path, quota = await fetch_snapshot(
                ctx.odds, ctx.raw_dir, slot.key, now, self.sport
            )
            await update_slot(
                ctx, self.name, slot.key, state="raw_saved", raw_path=str(path), at=now
            )
        result = await ingest_snapshot(ctx.session_factory, path, targets, quota, self.sport)
        await update_slot(ctx, self.name, slot.key, coverage=result.coverage, at=now)
        return result.state
