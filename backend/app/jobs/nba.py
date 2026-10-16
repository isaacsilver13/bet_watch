"""NBA schedule jobs. ESPN is free and has no weeks, so games are read one Eastern day at a time."""

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ingest.espn import fetch_scoreboard_day, parse_scoreboard
from app.ingest.nba_injury_report import fetch_report, parse_report
from app.ingest.nba_stats import (
    SEASON_TYPES,
    fetch_player_stats,
    fetch_team_game_logs,
    parse_player_stats,
    parse_team_game_logs,
)
from app.jobs.context import InjuriesJob
from app.jobs.nfl import EspnClosesJob, LinkEventsJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot, daily_slot, hourly_slots
from app.storage.context_repository import ContextRepository
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


class NbaTeamStatsJob:
    """Advanced and four-factor game logs for the current season, refreshed daily."""

    name = "nba_team_stats"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return daily_slot(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        season = nba_season_year(_now())
        records = []
        for season_type in SEASON_TYPES:
            advanced = await fetch_team_game_logs(ctx.http, season, season_type, "Advanced")
            factors = await fetch_team_game_logs(ctx.http, season, season_type, "Four Factors")
            records.extend(parse_team_game_logs(advanced, factors))
        if not records:
            return "empty"  # the season has no games yet
        result = await ContextRepository(ctx.session_factory).save_game_log_stats(
            NBA, records, _now()
        )
        if result.unmatched:
            logger.warning("nba_team_stats: %s rows had no game yet", len(result.unmatched))
        return "done" if result.saved else "empty"


class NbaOfficialInjuriesJob:
    """The league's injury-report PDF, hourly from 10:00 to 22:00 Central. When no report
    answers, or the file cannot be read, the job raises: the runner retries and then fails the
    slot, and the last good snapshot stays. An all-clear report writes nothing."""

    name = "nba_injuries_official"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now, first_hour=10, last_hour=22)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        now = _now()
        found = await fetch_report(ctx.http, now)
        if found is None:
            raise RuntimeError("no NBA injury report answered for the last four quarter hours")
        pdf, moment = found
        reported_at = moment.astimezone(timezone.utc)  # stored naive-UTC; read back as UTC
        records = [replace(record, reported_at=reported_at) for record in parse_report(pdf)]
        if not records:
            return "empty"
        result = await ContextRepository(ctx.session_factory).save_official_injuries(
            NBA, "nba_official", records, now
        )
        if result.unknown_teams:
            logger.warning("nba_injuries_official: unknown teams %s", result.unknown_teams)
        return "done" if result.saved else "empty"


class NbaPlayerStatsJob:
    """Minutes per game for this season and last, to pick each team's three stars."""

    name = "nba_player_stats"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return daily_slot(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        season = nba_season_year(_now())
        repo = ContextRepository(ctx.session_factory)
        saved = 0
        for year in (season, season - 1):
            records = parse_player_stats(await fetch_player_stats(ctx.http, year))
            if not records:
                continue  # the new season has no games yet
            result = await repo.save_player_stats(NBA, year, records, _now())
            if result.unmatched:
                logger.warning("nba_player_stats %s: unknown teams %s", year, result.unmatched)
            saved += result.saved
        return "done" if saved else "empty"


NBA_JOBS = [
    NbaScoreboardJob(),
    EspnClosesJob(NBA, "nba_closes"),
    NbaTeamStatsJob(),
    InjuriesJob(NBA, "nba_injuries", step=6),
    NbaOfficialInjuriesJob(),
    NbaPlayerStatsJob(),
    LinkEventsJob(NBA, "nba_link_events"),
    NbaHistoryJob(),
]
