"""The paid odds_snapshot job: a few snapshots a week, each saved raw before it is parsed.

One bulk call returns every NFL game for 3 credits, so slots are per moment, not per game:
Tuesday's opening lines, a morning snapshot on Thursday, Saturday and Sunday, and one shortly
before each distinct kickoff time (that last one is the closing-line reference for CLV).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app.ingest.odds_snapshot import PROVIDER, SPORT, fetch_snapshot, ingest_snapshot
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


def daily_odds_slots(now: datetime) -> list[OddsSlot]:
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
                f"{NAME}:nfl:{day.isoformat()}:{label}",
                start,
                start + length,
                targets=(start, week_end(day)),
                retry_after=DAILY_RETRY,
            )
        )
    return slots


def pre_kick_slots(kickoffs: set[datetime]) -> list[OddsSlot]:
    """One slot per distinct kickoff: the last 30 minutes before it."""
    slots = []
    for kickoff in sorted(kickoffs):
        local = kickoff.astimezone(CENTRAL)
        slots.append(
            OddsSlot(
                f"{NAME}:nfl:{local.date().isoformat()}:pre_kick:{local:%H%M}",
                kickoff - PRE_KICK_LEAD,
                kickoff,
                targets=(kickoff, kickoff + timedelta(minutes=1)),
                retry_after=PRE_KICK_RETRY,
            )
        )
    return slots


class OddsSnapshotJob:
    name = NAME
    max_attempts = 2  # a partial result is retried once; each call costs 3 credits

    def slots(self, now: datetime) -> list[Slot]:
        return []  # which slots exist depends on the schedule in the database: plan_slots

    async def plan_slots(self, ctx: JobContext, now: datetime) -> list[Slot]:
        if ctx.odds is None or ctx.settings is None:
            return []  # fixture mode, or no key: nothing to spend
        nearby = await GamesRepository(ctx.session_factory).scheduled_games(
            SPORT.value, now - timedelta(hours=12), now + timedelta(hours=12)
        )
        planned = [*daily_odds_slots(now), *pre_kick_slots({g.start_time for g in nearby})]
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
                targets = (when - timedelta(days=1), week_end(when.astimezone(CENTRAL).date()))
                slots.append(OddsSlot(row.slot_key, when, when, targets=targets))
        return slots

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        if ctx.odds is None or ctx.settings is None:
            # Only reachable by a manual `python -m app.jobs odds_snapshot`: run-due plans no slots.
            raise RuntimeError("odds_snapshot needs PROVIDER_MODE=live and a THE_ODDS_API_KEY")
        now = _now()
        today = now.astimezone(CENTRAL).date()
        targets = getattr(slot, "targets", None) or (now, week_end(today))  # a manual run
        info = await get_slot_info(ctx, self.name, slot.key)
        quota: Optional[int] = None
        if info.state == "raw_saved" and info.raw_path:
            path = Path(info.raw_path)  # we already paid: parse what is on disk, no new call
        else:
            if not await GamesRepository(ctx.session_factory).scheduled_games(
                SPORT.value, *targets
            ):
                return "empty"
            known = await OddsRepository(ctx.session_factory).latest_quota(PROVIDER)
            if known is not None and known <= ctx.settings.provider_min_quota_remaining:
                logger.warning("odds_snapshot skipped: %s credits left", known)
                return "skipped_quota"
            path, quota = await fetch_snapshot(ctx.odds, ctx.raw_dir, slot.key, now)
            await update_slot(
                ctx, self.name, slot.key, state="raw_saved", raw_path=str(path), at=now
            )
        result = await ingest_snapshot(ctx.session_factory, path, targets, quota)
        await update_slot(ctx, self.name, slot.key, coverage=result.coverage, at=now)
        return result.state
