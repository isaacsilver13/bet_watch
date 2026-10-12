"""Phase 1a NFL jobs. ESPN and nflverse are free, so these do not use raw-first slots."""

import logging
from datetime import datetime, timedelta, timezone

from app.ingest.espn import fetch_scoreboard, fetch_summary, parse_pickcenter, parse_scoreboard
from app.ingest.nflverse import GAMES_CSV_URL, parse_games_csv
from app.ingest.seed import seed_teams
from app.jobs.runner import JobContext
from app.jobs.slots import Slot, daily_slot, hourly_slots
from app.storage.games_repository import GamesRepository

logger = logging.getLogger(__name__)
NFL = "nfl"
CLOSE_LOOKBACK = timedelta(days=14)
TUESDAY = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SeedJob:
    name = "seed"
    max_attempts = 1

    def slots(self, now: datetime) -> list[Slot]:
        return []  # manual only: python -m app.jobs seed

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        await seed_teams(ctx.session_factory, NFL)
        return "done"


class NflHistoryJob:
    name = "nfl_history"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return daily_slot(self.name, now, weekday=TUESDAY)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        response = await ctx.http.get(GAMES_CSV_URL)
        response.raise_for_status()
        raw = ctx.raw_dir / "nflverse" / "games.csv"  # latest copy only; the source keeps history
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(response.text, encoding="utf-8")
        records = parse_games_csv(response.text)
        if not records:
            return "empty"
        result = await GamesRepository(ctx.session_factory).upsert_games(
            NFL, "nflverse", records, _now()
        )
        logger.info("nfl_history: %s games, %s skipped", result.games, len(result.skipped))
        return "done"


class EspnScoreboardJob:
    name = "espn_scoreboard"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        current = await fetch_scoreboard(ctx.http, NFL)
        payloads = [current]
        season = current.get("season") or {}
        week = (current.get("week") or {}).get("number")
        if season.get("year") and week:
            # Previous week too, so a game last seen live still gets its final after a PC-off gap.
            # ponytail: not across season types (REG 18 -> POST 1); Tuesday's nflverse run covers it
            for other in (week - 1, week + 1):
                if other >= 1:
                    payloads.append(
                        await fetch_scoreboard(
                            ctx.http, NFL, season["year"], season.get("type"), other
                        )
                    )
        records = [game for payload in payloads for game in parse_scoreboard(payload)]
        if not records:
            return "empty"
        result = await GamesRepository(ctx.session_factory).upsert_games(
            NFL, "espn", records, _now()
        )
        if result.skipped:
            logger.warning("espn_scoreboard skipped unresolved games: %s", result.skipped)
        return "done"


class EspnClosesJob:
    name = "espn_closes"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return daily_slot(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        repo = GamesRepository(ctx.session_factory)
        now = _now()
        missing = await repo.finals_missing_close(NFL, since=now - CLOSE_LOOKBACK)
        saved = 0
        for espn_id in missing:
            lines = parse_pickcenter(await fetch_summary(ctx.http, NFL, espn_id))
            if lines and await repo.save_lines(espn_id, "espn", lines, now):
                saved += 1
        return "done" if saved == len(missing) else "partial"


class LinkEventsJob:
    name = "link_events"
    max_attempts = 2

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        result = await GamesRepository(ctx.session_factory).link_events(NFL)
        if result.unlinked:
            logger.warning("link_events: unlinked odds events %s", result.unlinked)
        return "done"


JOBS = [SeedJob(), NflHistoryJob(), EspnScoreboardJob(), EspnClosesJob(), LinkEventsJob()]
