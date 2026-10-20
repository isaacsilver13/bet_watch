"""Phase 1b jobs: team stats, injuries and weather. All free sources, so no raw-first slots."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.ingest.espn import fetch_injuries, parse_injuries
from app.ingest.nflverse_stats import TEAM_STATS_URL, TeamStatRecord, parse_team_stats_csv
from app.ingest.open_meteo import fetch_forecast, parse_forecast
from app.jobs.runner import JobContext
from app.jobs.slots import Slot, daily_slot, hourly_slots
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import aware

logger = logging.getLogger(__name__)
NFL = "nfl"
FIRST_SEASON = 2015
FORECAST_HORIZON = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def nfl_season_year(now: datetime) -> int:
    """January and February still belong to the season that just ended."""
    return now.year if now.month >= 3 else now.year - 1


async def _team_stats(ctx: JobContext, season: int) -> Optional[list[TeamStatRecord]]:
    response = await ctx.http.get(TEAM_STATS_URL.format(season=season))
    if response.status_code == 404:
        return None  # the season's file is not published yet
    response.raise_for_status()
    raw = ctx.raw_dir / "nflverse" / f"stats_team_week_{season}.csv"  # latest copy only
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(response.text, encoding="utf-8")
    return parse_team_stats_csv(response.text)


async def _save_stats(ctx: JobContext, season: int) -> int:
    records = await _team_stats(ctx, season)
    if not records:
        return 0
    result = await ContextRepository(ctx.session_factory).save_team_stats(NFL, records, _now())
    if result.unmatched:
        logger.warning("team_stats %s: %s rows had no game yet", season, len(result.unmatched))
    return result.saved


class TeamStatsJob:
    name = "team_stats"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        # Daily rather than Tuesday-only: it is one small file, and a PC that was off on Tuesday
        # would otherwise leave the whole week stale.
        return daily_slot(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        return "done" if await _save_stats(ctx, nfl_season_year(_now())) else "empty"


class TeamStatsHistoryJob:
    name = "team_stats_history"
    max_attempts = 1

    def slots(self, now: datetime) -> list[Slot]:
        return []  # manual only: python -m app.jobs team_stats_history

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        saved = 0
        for season in range(FIRST_SEASON, nfl_season_year(_now()) + 1):
            saved += await _save_stats(ctx, season)
        return "done" if saved else "empty"


class InjuriesJob:
    """ESPN's league injury report. Daily by default; `step` hours apart for a sport whose
    report changes through the day (the NBA, three times: 10:00, 16:00 and 22:00 Central)."""

    max_attempts = 3

    def __init__(
        self, sport: str = NFL, name: str = "injuries", step: Optional[int] = None
    ) -> None:
        self.sport, self.name, self.step = sport, name, step

    def slots(self, now: datetime) -> list[Slot]:
        if self.step is None:
            return daily_slot(self.name, now)
        return hourly_slots(self.name, now, first_hour=10, last_hour=22, step=self.step)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        records = parse_injuries(await fetch_injuries(ctx.http, self.sport))
        result = await ContextRepository(ctx.session_factory).save_injuries(
            self.sport, records, _now()
        )
        if result.unknown_teams:
            logger.warning("injuries: unknown ESPN team ids %s", result.unknown_teams)
            return "partial"
        return "done"  # an empty successful poll is an explicit all-clear snapshot


class WeatherJob:
    name = "weather"
    max_attempts = 2

    def slots(self, now: datetime) -> list[Slot]:
        # ponytail: every 3 hours daily, not only on game days; it is a free API.
        return hourly_slots(self.name, now, last_hour=22, step=3)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        repo = ContextRepository(ctx.session_factory)
        now = _now()
        targets = await repo.weather_targets(NFL, now, FORECAST_HORIZON)
        if not targets:
            return "empty"
        saved = 0
        for target in targets:
            forecast = await fetch_forecast(ctx.http, target.lat, target.lon)
            record = parse_forecast(forecast, aware(target.start_time))
            if record is not None:
                await repo.save_weather(target.game_id, record, now)
                saved += 1
        return "done" if saved == len(targets) else "partial"


CONTEXT_JOBS = [TeamStatsJob(), TeamStatsHistoryJob(), InjuriesJob(), WeatherJob()]
