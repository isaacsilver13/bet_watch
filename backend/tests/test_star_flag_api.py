"""BOS at MIA, Thursday 2026-01-15 7:30pm ET, with two more games the same night.

MIA's three top-minutes players last season: Adebayo (34.5), Herro (33.0), Powell (31.0); Bench
Guy plays 12. BOS: Tatum, Brown, White. The league report at 9am ET has Adebayo Out, Herro
Questionable, Bench Guy Out and Tatum Probable. Bets on the BOS game: open, won (+10), lost (-5.5
with the fee) and void; the LAL game has one void bet; the CLE game has none.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_scoreboard
from app.ingest.nba_stats import PlayerStatRecord
from app.ingest.records import GameRecord, NamedInjury
from app.ingest.seed import SEEDS, seed_teams
from app.main import app
from app.storage.context_repository import ContextRepository
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Bet, Game
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
TIP = datetime(2026, 1, 16, 0, 30, tzinfo=timezone.utc)  # 7:30pm Eastern on the 15th
REPORT_AT = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def game(espn_id, home, away, minutes=0):
    return GameRecord(
        espn_id=espn_id, season=2026, season_type="REG", week=None,
        start_time=TIP + timedelta(minutes=minutes), status="scheduled", home_name=home,
        away_name=away, home_score=None, away_score=None, neutral=False,
    )


def minutes(team, *rows):
    return [PlayerStatRecord(team, name, 60, mpg) for name, mpg in rows]


def bet(game_id, status, stake=10.0, fee=0.0, payout=None):
    return Bet(
        game_id=game_id, placed_at=NOW, venue="fanduel", market="spread", selection="home",
        line=-2.5, price_american=-110, stake=stake, fee=fee, status=status, payout=payout,
    )


def _client(tmp_path) -> TestClient:
    async def setup():
        factory = await new_session_factory(tmp_path)
        for sport, path in SEEDS.items():
            await seed_teams(factory, sport, path)
        await GamesRepository(factory).upsert_games("nba", "espn", [
            game("4", "MIA", "BOS"), game("5", "DEN", "LAL", 15), game("6", "ORL", "CLE", 30),
        ], NOW)
        await GamesRepository(factory).upsert_games(
            "nfl", "espn", parse_scoreboard(load_json("espn/scoreboard_week6.json")), NOW)
        repo = ContextRepository(factory)
        await repo.save_player_stats("nba", 2025, [
            *minutes("MIA", ("Bam Adebayo", 34.5), ("Tyler Herro", 33.0),
                     ("Norman Powell", 31.0), ("Bench Guy", 12.0)),
            *minutes("BOS", ("Jayson Tatum", 36.0), ("Jaylen Brown", 34.0),
                     ("Derrick White", 32.0)),
        ], NOW)
        await repo.save_official_injuries("nba", "nba_official", [
            NamedInjury("Miami Heat", "Bam Adebayo", None, "Out", "Toe"),
            NamedInjury("Miami Heat", "Tyler Herro", None, "Questionable", "Ankle"),
            NamedInjury("Miami Heat", "Bench Guy", None, "Out", "Knee"),
            NamedInjury("Boston Celtics", "Jayson Tatum", None, "Probable", "Achilles"),
        ], REPORT_AT)
        async with factory() as session, session.begin():
            ids = {g.espn_id: g.id for g in (await session.scalars(select(Game))).all()}
            session.add_all([
                bet(ids["4"], "open"), bet(ids["4"], "won", payout=20.0),
                bet(ids["4"], "lost", stake=5.0, fee=0.5, payout=0.0),
                bet(ids["4"], "void", stake=5.0, payout=5.0),
                bet(ids["5"], "void", stake=5.0, payout=5.0),
            ])

    asyncio.run(setup())
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def _games(client) -> dict:
    response = client.get("/api/v1/slate/days", params={"sport": "nba", "date": "2026-01-15"})
    assert response.status_code == 200, response.text
    return {f"{g['away']['abbr']}@{g['home']['abbr']}": g for g in response.json()["games"]}


def _id(tmp_path, espn_id):
    async def lookup():
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            return await session.scalar(select(Game.id).where(Game.espn_id == espn_id))

    return asyncio.run(lookup())


def test_the_slate_flags_a_team_whose_star_is_out_or_questionable(tmp_path) -> None:
    games = _games(_client(tmp_path))
    assert games["BOS@MIA"]["star"] == {
        "home": {"out": ["Bam Adebayo"], "questionable": ["Tyler Herro"]},
        "away": {"out": [], "questionable": []},  # Tatum is only Probable
    }


def test_a_game_without_bets_or_flagged_stars_has_neither_badge(tmp_path) -> None:
    quiet = _games(_client(tmp_path))["CLE@ORL"]
    assert quiet["star"] is None and quiet["bets"] is None


def test_bet_badges_count_open_and_net_settled_bets_only(tmp_path) -> None:
    games = _games(_client(tmp_path))
    assert games["BOS@MIA"]["bets"] == {"total": 4, "open": 1, "net": 4.5}  # +10 and -5.5
    assert games["LAL@DEN"]["bets"] == {"total": 1, "open": 0, "net": None}  # a void bet only


def test_the_context_marks_star_rows_and_summarizes_each_team(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.get(f"/api/v1/games/{_id(tmp_path, '4')}/context")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["home"]["star_flag"] == {"out": ["Bam Adebayo"], "questionable": ["Tyler Herro"]}
    assert body["away"]["star_flag"] == {"out": [], "questionable": []}
    stars = {row["player"]: row["star"] for row in body["home"]["injuries"]}
    assert stars == {"Bam Adebayo": True, "Bench Guy": False, "Tyler Herro": True}
    assert body["away"]["injuries"][0]["star"] is True  # Tatum is a star, just not flagged


def test_the_matchup_header_carries_the_same_badges(tmp_path) -> None:
    client = _client(tmp_path)
    body = client.get(f"/api/v1/games/{_id(tmp_path, '4')}").json()
    assert body["bets"]["total"] == 4 and body["star"]["home"]["out"] == ["Bam Adebayo"]


def test_a_football_game_has_no_star_flag(tmp_path) -> None:
    client = _client(tmp_path)

    async def nfl_game_id():
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            return await session.scalar(select(Game.id).where(Game.sport == "nfl").limit(1))

    game_id = asyncio.run(nfl_game_id())
    body = client.get(f"/api/v1/games/{game_id}/context").json()
    assert body["home"]["star_flag"] is None and body["away"]["star_flag"] is None
    header = client.get(f"/api/v1/games/{game_id}").json()
    assert header["star"] is None and header["bets"] is None
