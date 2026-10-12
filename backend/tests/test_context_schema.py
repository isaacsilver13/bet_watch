import asyncio
from datetime import datetime, timezone

import pytest
from app.storage.models import Game, Injury, Team, TeamGameStat, Weather
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.matchup_helpers import new_session_factory

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


async def _game(session):
    a = Team(sport="nfl", abbr="A", name="A")
    b = Team(sport="nfl", abbr="B", name="B")
    session.add_all([a, b])
    await session.flush()
    game = Game(
        sport="nfl", season=2026, season_type="REG", week=6, start_time=NOW,
        home_team_id=a.id, away_team_id=b.id, neutral=False, status="scheduled",
        espn_id="1", updated_at=NOW, home_qb="Home QB", away_qb="Away QB",
    )
    session.add(game)
    await session.flush()
    return game, a, b


def test_context_rows_round_trip(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            game, a, _ = await _game(session)
            session.add(TeamGameStat(game_id=game.id, team_id=a.id, stats={"attempts": 30.0},
                                     fetched_at=NOW))
            session.add(Injury(team_id=a.id, player="P One", position="QB", status="Out",
                               detail="Knee, Sprain", source="espn", reported_at=NOW,
                               observed_at=NOW))
            session.add(Weather(game_id=game.id, fetched_at=NOW, temp_f=61.0, wind_mph=12.5,
                                precip_pct=10.0, conditions="Overcast"))
        async with factory() as session:
            stat = await session.scalar(select(TeamGameStat))
            game = await session.scalar(select(Game))
            weather = await session.scalar(select(Weather))
        assert stat.stats == {"attempts": 30.0}
        assert (game.home_qb, game.away_qb) == ("Home QB", "Away QB")
        assert (weather.temp_f, weather.conditions) == (61.0, "Overcast")

    asyncio.run(scenario())


def test_one_stats_row_per_game_team_and_one_weather_row_per_game(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            game, a, _ = await _game(session)
            session.add(TeamGameStat(game_id=game.id, team_id=a.id, stats={}, fetched_at=NOW))
            session.add(Weather(game_id=game.id, fetched_at=NOW))
            ids = (game.id, a.id)
        with pytest.raises(IntegrityError):
            async with factory() as session, session.begin():
                session.add(TeamGameStat(game_id=ids[0], team_id=ids[1], stats={}, fetched_at=NOW))
        with pytest.raises(IntegrityError):
            async with factory() as session, session.begin():
                session.add(Weather(game_id=ids[0], fetched_at=NOW))

    asyncio.run(scenario())
