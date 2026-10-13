"""NBA schedule jobs. ESPN is free and has no weeks, so games are read one Eastern day at a time."""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ingest.espn import fetch_scoreboard_day, parse_scoreboard
from app.jobs.nfl import EspnClosesJob, LinkEventsJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot, hourly_slots
from app.storage.games_repository import GamesRepository

logger = logging.getLogger(__name__)
NBA = "nba"
EASTERN = ZoneInfo("America/New_York")  # ESPN's "day" is the US Eastern date
DAYS_BACK = 1  # yesterday too, so a late game still gets its final after a PC-off gap
DAYS_AHEAD = 2
SEASON_LENGTH = timedelta(days=300)  # October through the Finals, with room to spare


def _now() -> datetime:
    return datetime.now(timezone.utc)


def nba_season_year(now: datetime) -> int:
    """ESPN names the 2026-27 season 2027; October is already that season's preseason."""
    return now.year + 1 if now.month >= 10 else now.year


def season_start(season_year: int) -> date:
    return date(season_year - 1, 10, 1)


async def _load_days(ctx: JobContext, days: list[date]) -> int:
    records = []
    for day in days:
        records.extend(parse_scoreboard(await fetch_scoreboard_day(ctx.http, NBA, day)))
    if not records:
        return 0
    result = await GamesRepository(ctx.session_factory).upsert_games(NBA, "espn", records, _now())
    if result.skipped:
        logger.warning("nba scoreboard skipped unresolved games: %s", result.skipped)
    return result.games


class NbaScoreboardJob:
    name = "nba_scoreboard"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        today = _now().astimezone(EASTERN).date()
        days = [today + timedelta(days=n) for n in range(-DAYS_BACK, DAYS_AHEAD + 1)]
        return "done" if await _load_days(ctx, days) else "empty"


class NbaHistoryJob:
    """The season so far: every day's scoreboard, then the closing line of each final game."""

    name = "nba_history"
    max_attempts = 1

    def slots(self, now: datetime) -> list[Slot]:
        return []  # manual only: python -m app.jobs nba_history

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        now = _now()
        today = now.astimezone(EASTERN).date()
        day = season_start(nba_season_year(now))
        days = []
        while day <= today + timedelta(days=DAYS_AHEAD):
            days.append(day)
            day += timedelta(days=1)
        if not await _load_days(ctx, days):
            return "empty"
        closes = EspnClosesJob(NBA, "nba_closes", lookback=SEASON_LENGTH)
        return await closes.run(ctx, slot)


NBA_JOBS = [
    NbaScoreboardJob(),
    EspnClosesJob(NBA, "nba_closes"),
    LinkEventsJob(NBA, "nba_link_events"),
    NbaHistoryJob(),
]
