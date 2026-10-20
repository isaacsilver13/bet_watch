"""NBA schedule jobs. ESPN is free and has no weeks, so games are read one Eastern day at a time."""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ingest.espn import (
    fetch_scoreboard_day,
    fetch_summary,
    parse_nba_box_score,
    parse_nba_completed_games,
    parse_scoreboard,
)
from app.jobs.context import InjuriesJob
from app.jobs.nfl import EspnClosesJob, LinkEventsJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot, hourly_slots
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository

logger = logging.getLogger(__name__)
NBA = "nba"
EASTERN = ZoneInfo("America/New_York")  # ESPN's "day" is the US Eastern date
DAYS_BACK = 1  # yesterday too, so a late game still gets its final after a PC-off gap
DAYS_AHEAD = 2
SEASON_LENGTH = timedelta(days=300)  # October through the Finals, with room to spare
MAX_CONTEXT_GAMES_PER_RUN = 4
MAX_CONTEXT_REQUESTS_PER_RUN = 5  # one scoreboard day plus at most four summaries


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
    """Incremental completed-game context from ESPN's existing zero-key feed."""

    name = "nba_team_stats"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now, first_hour=8, last_hour=11, step=1)

    async def plan_slots(self, ctx: JobContext, now: datetime) -> list[Slot]:
        settings = ctx.settings
        if settings is None or settings.nba_context_provider != "espn":
            return []
        return self.slots(now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        settings = ctx.settings
        if settings is None or settings.nba_context_provider != "espn":
            return "empty"
        observed_at = _now()
        repo = ContextRepository(ctx.session_factory)
        yesterday = observed_at.astimezone(EASTERN).date() - timedelta(days=1)
        through = datetime.combine(yesterday, datetime.max.time(), EASTERN).astimezone(timezone.utc)
        manual_recheck = ":manual:" in slot.key
        days = [yesterday] if manual_recheck else await repo.pending_nba_dates(through)
        saved = 0
        processed = 0
        requests = 0
        had_partial = False
        for day in days:
            if requests >= MAX_CONTEXT_REQUESTS_PER_RUN:
                break
            await repo.record_nba_request_attempt("scoreboard", day.isoformat(), day, observed_at)
            requests += 1
            payload = await fetch_scoreboard_day(ctx.http, NBA, day)
            await repo.record_nba_date_lookup(
                datetime.combine(day, datetime.min.time(), timezone.utc), observed_at, None
            )
            games = parse_nba_completed_games(payload)
            if manual_recheck:
                games = await repo.order_nba_games_for_recheck(games)
            for game in games:
                if processed == MAX_CONTEXT_GAMES_PER_RUN:
                    break
                if not manual_recheck and await repo.nba_game_complete(game.provider_game_id):
                    continue
                if requests + 1 > MAX_CONTEXT_REQUESTS_PER_RUN:
                    break
                await repo.record_nba_request_attempt(
                    "summary", game.provider_game_id, game.game_date, observed_at
                )
                requests += 1
                summary = await fetch_summary(ctx.http, NBA, game.provider_game_id)
                team_records, player_records = parse_nba_box_score(game, summary)
                result = await repo.save_nba_box_scores(
                    game,
                    team_records,
                    player_records,
                    observed_at,
                    None,
                )
                saved += result.team_rows + result.player_rows
                processed += 1
                if result.unmatched:
                    had_partial = True
                    logger.warning("nba_team_stats: unmatched %s", result.unmatched)
            if processed == MAX_CONTEXT_GAMES_PER_RUN:
                break
        if had_partial:
            return "partial"
        return "done" if saved or (manual_recheck and processed) else "empty"


NBA_JOBS = [
    NbaScoreboardJob(),
    EspnClosesJob(NBA, "nba_closes"),
    NbaTeamStatsJob(),
    InjuriesJob(NBA, "nba_injuries", step=6),
    LinkEventsJob(NBA, "nba_link_events"),
    NbaHistoryJob(),
]
