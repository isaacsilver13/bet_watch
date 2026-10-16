import asyncio
from datetime import datetime, timezone

import pytest
from app.storage.models import PlayerSeasonStat, Team
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.matchup_helpers import new_session_factory

NOW = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)


def _stat(team_id, **overrides):
    values = dict(sport="nba", season=2026, team_id=team_id, player="trae young", games=40,
                  minutes_pg=34.5, fetched_at=NOW)
    return PlayerSeasonStat(**{**values, **overrides})


def test_player_minutes_round_trip_and_are_unique_per_team_and_season(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            team = Team(sport="nba", abbr="WAS", name="Washington Wizards")
            session.add(team)
            await session.flush()
            session.add(_stat(team.id))
            team_id = team.id
        async with factory() as session:
            row = await session.scalar(select(PlayerSeasonStat))
        assert (row.player, row.games, row.minutes_pg) == ("trae young", 40, 34.5)
        # the same player on another season, or on another team, is a different row
        async with factory() as session, session.begin():
            session.add(_stat(team_id, season=2025))
        with pytest.raises(IntegrityError):
            async with factory() as session, session.begin():
                session.add(_stat(team_id))

    asyncio.run(scenario())
