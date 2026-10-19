import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from app.api.lines import Runtime, get_runtime
from app.api.slate import get_now, get_session_factory
from app.domain.models import Sport
from app.ingest.espn import parse_scoreboard
from app.ingest.odds_raw import write_raw
from app.ingest.odds_snapshot import ingest_snapshot
from app.ingest.seed import SEEDS, seed_teams
from app.main import app
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, OfferObservation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.local_client import TestClient
from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 1, 15, 16, 30, tzinfo=timezone.utc)
SEEDED = datetime(2026, 1, 14, 15, tzinfo=timezone.utc)
CALLS: list = []


def _nba_body(shift: float = 0.0) -> str:
    payload = load_json("the_odds_api/nba_jan15.json")
    for book in payload[0]["bookmakers"]:
        for market in book["markets"]:
            if market["key"] == "spreads":
                for outcome in market["outcomes"]:
                    home = outcome["name"] == "Golden State Warriors"
                    outcome["point"] += shift if home else -shift
    return json.dumps(payload)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _client(tmp_path, snapshots=(), now=NOW) -> TestClient:
    CALLS.clear()

    async def setup():
        factory = await new_session_factory(tmp_path)
        for sport, path in SEEDS.items():
            await seed_teams(factory, sport, path)
        records = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
        await GamesRepository(factory).upsert_games("nba", "espn", records, SEEDED)
        window = (datetime(2026, 1, 16, 3, tzinfo=timezone.utc),) * 2
        for taken, body in snapshots:
            path = write_raw(tmp_path / "raw", "the_odds_api", f"seed-{taken}", taken, body)
            await ingest_snapshot(factory, path, window, 450, Sport.NBA)

    asyncio.run(setup())

    def handler(request: httpx.Request) -> httpx.Response:
        CALLS.append(request.url)
        return httpx.Response(200, text=_nba_body(), headers={"x-requests-remaining": "447"})

    runtime = Runtime(
        TheOddsApiClient("test-key", transport=httpx.MockTransport(handler)), tmp_path / "raw", 5
    )
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: now
    app.dependency_overrides[get_runtime] = lambda: runtime
    return TestClient(app)


def _game_id(tmp_path, espn_id="401810439") -> int:
    async def lookup() -> int:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            return await session.scalar(select(Game.id).where(Game.espn_id == espn_id))

    return asyncio.run(lookup())


def test_an_nba_games_lines_follow_its_snapshots(tmp_path) -> None:
    morning = datetime(2026, 1, 14, 16, 0, tzinfo=timezone.utc)
    today = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    client = _client(tmp_path, [(morning, _nba_body(shift=1.0)), (today, _nba_body())])
    body = client.get(f"/api/v1/games/{_game_id(tmp_path)}/lines").json()
    assert [b["venue"] for b in body["books"]] == [
        "betmgm", "betrivers", "draftkings", "fanduel", "espnbet"]
    assert body["consensus"]["spread_home"] == -7.5 and body["consensus"]["books"] == 5
    assert [p["spread_home"] for p in body["history"]] == [-6.5, -7.5]  # the Warriors grew
    assert body["reference"]["current"]["spread_home"] == -7.5  # ESPN's own line
    assert body["best"]["spread_home"]["price"] == -105  # BetRivers, at the consensus number


def test_refreshing_an_nba_game_asks_for_nba_prices_and_has_its_own_cooldown(tmp_path) -> None:
    just_now = NOW - timedelta(minutes=2)
    # An NFL snapshot two minutes ago must not stop an NBA refresh: the cooldown is per sport.
    client = _client(tmp_path)

    async def an_nfl_snapshot() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        body = json.dumps(load_json("the_odds_api/nfl_week6.json"))
        path = write_raw(tmp_path / "raw", "the_odds_api", "nfl", just_now, body)
        await ingest_snapshot(factory, path, (just_now, just_now), 450, Sport.NFL)

    asyncio.run(an_nfl_snapshot())
    game_id = _game_id(tmp_path)
    first = client.post(f"/api/v1/games/{game_id}/refresh").json()
    assert (first["refreshed"], first["state"], first["quota_remaining"]) == (True, "done", 447)
    assert len(CALLS) == 1 and "basketball_nba" in CALLS[0].path
    assert client.post(f"/api/v1/games/{game_id}/refresh").json()["reason"] == "recent"
    assert len(CALLS) == 1

    async def count() -> int:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            game = await session.scalar(select(Game).where(Game.id == game_id))
            assert game.event_id is not None  # linked by the refresh itself
            return await session.scalar(select(func.count(OfferObservation.id)))

    assert asyncio.run(count()) == 60  # thirty NFL rows and thirty NBA rows
