import asyncio
from datetime import datetime, timezone

import pytest
from app.storage.models import Game, GameLine, JobSlot, Team, TeamAlias, Venue
from sqlalchemy.exc import IntegrityError

from tests.matchup_helpers import new_session_factory

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def test_game_with_lines_round_trips(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            venue = Venue(espn_id="3937", name="Empower Field", roof="outdoors")
            session.add(venue)
            await session.flush()
            den = Team(sport="nfl", abbr="DEN", name="Denver Broncos", venue_id=venue.id)
            sea = Team(sport="nfl", abbr="SEA", name="Seattle Seahawks")
            session.add_all([den, sea])
            await session.flush()
            session.add(TeamAlias(team_id=den.id, sport="nfl", alias="den", source="seed"))
            game = Game(
                sport="nfl", season=2026, season_type="REG", week=6, start_time=NOW,
                home_team_id=den.id, away_team_id=sea.id, venue_id=venue.id, neutral=False,
                status="scheduled", espn_id="401872995", updated_at=NOW,
            )
            session.add(game)
            await session.flush()
            session.add(GameLine(game_id=game.id, source="espn", kind="open", spread_home=2.5,
                                 total=43.5, ml_home=110, ml_away=-130, captured_at=NOW))
            session.add(JobSlot(job="espn_scoreboard", slot_key="k1", state="pending",
                                attempts=0, coverage={"DraftKings": {"spread": 1}}, updated_at=NOW))
        async with factory() as session:
            line = (await session.execute(GameLine.__table__.select())).first()
            assert line.spread_home == 2.5
            slot = (await session.execute(JobSlot.__table__.select())).first()
            assert slot.coverage == {"DraftKings": {"spread": 1}}

    asyncio.run(scenario())


def test_one_line_per_game_source_kind(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            a = Team(sport="nfl", abbr="A", name="A")
            b = Team(sport="nfl", abbr="B", name="B")
            session.add_all([a, b])
            await session.flush()
            game = Game(sport="nfl", season=2026, season_type="REG", week=1, start_time=NOW,
                        home_team_id=a.id, away_team_id=b.id, neutral=False,
                        status="scheduled", espn_id="1", updated_at=NOW)
            session.add(game)
            await session.flush()
            session.add(GameLine(game_id=game.id, source="espn", kind="open", captured_at=NOW))
        with pytest.raises(IntegrityError):
            async with factory() as session, session.begin():
                session.add(GameLine(game_id=1, source="espn", kind="open", captured_at=NOW))

    asyncio.run(scenario())
