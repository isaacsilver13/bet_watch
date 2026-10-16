"""Who each team's stars are right now: queries over player_season_stats and finished games."""

from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.stars import PlayerMinutes, pick_stars
from app.storage.models import Game, PlayerSeasonStat


async def _minutes(session: AsyncSession, sport: str, season: int) -> list[PlayerMinutes]:
    rows = await session.execute(
        select(
            PlayerSeasonStat.team_id, PlayerSeasonStat.player,
            PlayerSeasonStat.games, PlayerSeasonStat.minutes_pg,
        ).where(PlayerSeasonStat.sport == sport, PlayerSeasonStat.season == season)
    )
    return [PlayerMinutes(*row) for row in rows.all()]


async def _games_played(
    session: AsyncSession, sport: str, season: int, before: datetime
) -> Counter:
    played: Counter = Counter()
    rows = await session.execute(
        select(Game.home_team_id, Game.away_team_id).where(
            Game.sport == sport,
            Game.season == season,
            Game.season_type != "PRE",
            Game.status == "final",
            Game.start_time < before,
        )
    )
    for home_id, away_id in rows.all():
        played[home_id] += 1
        played[away_id] += 1
    return played


async def season_stars(
    session: AsyncSession, sport: str, season: int, team_ids: set[int], before: datetime
) -> dict[int, set[str]]:
    """Name keys of each team's stars as of `before`. Empty when no minutes are on file."""
    current = await _minutes(session, sport, season)
    previous = await _minutes(session, sport, season - 1)
    played = await _games_played(session, sport, season, before)
    return {
        team_id: pick_stars(team_id, current, previous, played[team_id]) for team_id in team_ids
    }
