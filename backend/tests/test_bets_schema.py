import asyncio
from datetime import datetime, timezone

import pytest
from app.storage.models import Bet, Game, Team
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.matchup_helpers import new_session_factory

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


async def _game(session) -> int:
    a = Team(sport="nfl", abbr="A", name="A")
    b = Team(sport="nfl", abbr="B", name="B")
    session.add_all([a, b])
    await session.flush()
    game = Game(
        sport="nfl", season=2026, season_type="REG", week=6, start_time=NOW,
        home_team_id=a.id, away_team_id=b.id, neutral=False, status="scheduled",
        espn_id="1", updated_at=NOW,
    )
    session.add(game)
    await session.flush()
    return game.id


def test_a_bet_round_trips_and_starts_open(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            game_id = await _game(session)
            session.add(Bet(game_id=game_id, placed_at=NOW, venue="fanduel", market="spread",
                            selection="home", line=-2.5, price_american=-110, stake=20.0, fee=0.5))
        async with factory() as session:
            bet = await session.scalar(select(Bet))
        assert (bet.status, bet.payout, bet.clv_points, bet.close_source) == (
            "open", None, None, None)
        assert (bet.venue, bet.selection, bet.line, bet.stake, bet.fee) == (
            "fanduel", "home", -2.5, 20.0, 0.5)

    asyncio.run(scenario())


def test_a_bet_must_belong_to_a_game(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        with pytest.raises(IntegrityError):
            async with factory() as session, session.begin():
                session.add(Bet(game_id=None, placed_at=NOW, venue="fanduel", market="total",
                                selection="over", line=44.5, price_american=-110, stake=5.0,
                                fee=0.0))

    asyncio.run(scenario())
