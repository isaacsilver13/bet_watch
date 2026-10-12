import asyncio
from datetime import datetime, timezone

from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_pickcenter, parse_scoreboard
from app.ingest.open_meteo import WeatherRecord
from app.ingest.seed import seed_teams
from app.main import app
from app.storage.context_repository import ContextRepository
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Game
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)


def _client(tmp_path, now=NOW, load=True):
    async def setup():
        factory = await new_session_factory(tmp_path)
        if load:
            await seed_teams(factory)
            repo = GamesRepository(factory)
            await repo.upsert_games("nfl", "espn", parse_scoreboard(
                load_json("espn/scoreboard_week6.json")), NOW)
            await repo.save_lines("401872964", "espn",
                                  parse_pickcenter(load_json("espn/summary_final.json")), NOW)

    asyncio.run(setup())
    # A fresh engine: pooled connections from setup's event loop must not leak into TestClient's.
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: now
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_explicit_week_returns_games_lines_and_movement(tmp_path) -> None:
    body = _client(tmp_path).get(
        "/api/v1/slate", params={"sport": "nfl", "season": 2026, "week": 6}
    ).json()
    assert (body["season"], body["season_type"], body["week"]) == (2026, "REG", 6)
    assert {"season_type": "REG", "week": 4} in body["weeks"]
    assert {"season_type": "REG", "week": 6} in body["weeks"]
    games = {g["espn_id"]: g for g in body["games"]}
    assert set(games) == {"401872981", "401872995", "401872999"}
    den = games["401872995"]
    assert (den["home"]["abbr"], den["away"]["abbr"]) == ("DEN", "SEA")
    assert den["lines"]["open"]["spread_home"] == 2.5
    assert den["lines"]["current"]["spread_home"] == 1.5
    assert "close" not in den["lines"]
    assert den["spread_move"] == -1.0 and den["total_move"] == -1.0
    assert den["start_time"].startswith("2026-10-16T00:15:00")
    assert den["venue"] == "Empower Field at Mile High"
    # Games come back in kickoff order.
    assert [g["espn_id"] for g in body["games"]][0] == "401872981"


def test_default_week_is_next_upcoming(tmp_path) -> None:
    body = _client(tmp_path).get("/api/v1/slate", params={"sport": "nfl"}).json()
    assert body["week"] == 6


def test_final_game_prefers_close_for_movement(tmp_path) -> None:
    body = _client(tmp_path).get(
        "/api/v1/slate", params={"sport": "nfl", "season": 2026, "week": 4}
    ).json()
    [game] = body["games"]
    assert game["status"] == "final" and game["home_score"] == 27
    assert game["lines"]["close"]["total"] == 38.5
    assert game["total_move"] == -2.0  # 38.5 close - 40.5 open


def test_no_games_loaded_is_404_with_guidance(tmp_path) -> None:
    response = _client(tmp_path, load=False).get("/api/v1/slate", params={"sport": "nfl"})
    assert response.status_code == 404
    assert "python -m app.jobs" in response.json()["detail"]


def test_persistence_off_is_503() -> None:
    app.dependency_overrides.clear()
    response = TestClient(app).get("/api/v1/slate", params={"sport": "nfl"})
    assert response.status_code == 503


def test_unsupported_sport_rejected(tmp_path) -> None:
    assert _client(tmp_path).get("/api/v1/slate", params={"sport": "nba"}).status_code == 422


def test_preseason_week_is_accepted(tmp_path) -> None:
    response = _client(tmp_path).get(
        "/api/v1/slate", params={"sport": "nfl", "season": 2026, "season_type": "PRE", "week": 3}
    )
    assert response.status_code == 200 and response.json()["games"] == []


def test_slate_carries_the_venue_roof_and_the_latest_weather(tmp_path) -> None:
    client = _client(tmp_path)

    async def add_weather() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            game_id = await session.scalar(select(Game.id).where(Game.espn_id == "401872995"))
        await ContextRepository(factory).save_weather(
            game_id, WeatherRecord(55.4, 11.8, 60, "Overcast"), NOW
        )

    asyncio.run(add_weather())
    body = client.get("/api/v1/slate", params={"sport": "nfl", "season": 2026, "week": 6}).json()
    games = {g["espn_id"]: g for g in body["games"]}
    den = games["401872995"]
    assert den["roof"] == "outdoors"
    assert (den["weather"]["temp_f"], den["weather"]["conditions"]) == (55.4, "Overcast")
    assert games["401872981"]["weather"] is None  # no forecast stored for the London game
