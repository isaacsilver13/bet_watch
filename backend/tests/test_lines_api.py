import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.api import routes
from app.api.lines import Runtime, get_runtime
from app.api.slate import get_now, get_session_factory
from app.config import ProviderMode
from app.domain.models import Sport
from app.ingest.espn import parse_scoreboard
from app.ingest.odds_raw import write_raw
from app.ingest.odds_snapshot import ingest_snapshot
from app.ingest.seed import seed_teams
from app.main import app
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, ProviderRefreshRun
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.local_client import TestClient
from tests.matchup_helpers import db_url, load_json, new_session_factory

FIRST = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)  # Tuesday morning
SECOND = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)  # Thursday morning
NOW = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
SEEDED = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)
KICKOFF = "2026-10-16T00:15:00Z"
ODDS_CALLS: list = []


def _body(spread_shift: float = 0.0, commence: str = KICKOFF) -> str:
    payload = load_json("the_odds_api/nfl_week6.json")
    payload[0]["commence_time"] = commence
    for book in payload[0]["bookmakers"]:
        for market in book["markets"]:
            if market["key"] == "spreads":
                for outcome in market["outcomes"]:
                    home = outcome["name"] == "Denver Broncos"
                    outcome["point"] += spread_shift if home else -spread_shift
    return json.dumps(payload)


def _runtime(tmp_path, live=True, quota="450") -> Runtime:
    def handler(request: httpx.Request) -> httpx.Response:
        ODDS_CALLS.append(request.url)
        return httpx.Response(200, text=_body(), headers={"x-requests-remaining": quota})

    client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))
    return Runtime(client if live else None, tmp_path / "raw", 5)


def _client(tmp_path, snapshots=(), runtime=None, now=NOW, client=None) -> TestClient:
    ODDS_CALLS.clear()

    async def setup():
        factory = await new_session_factory(tmp_path)
        await seed_teams(factory)
        await GamesRepository(factory).upsert_games(
            "nfl", "espn", parse_scoreboard(load_json("espn/scoreboard_week6.json")), SEEDED
        )
        for taken, body in snapshots:
            path = write_raw(tmp_path / "raw", "the_odds_api", f"seed-{taken}", taken, body)
            await ingest_snapshot(factory, path, (FIRST, FIRST + timedelta(days=7)), 450, Sport.NFL)

    asyncio.run(setup())
    factory = _factory(tmp_path)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: now
    app.dependency_overrides[get_runtime] = lambda: runtime or _runtime(tmp_path)
    return TestClient(app, client=client) if client else TestClient(app)


def _factory(tmp_path):
    # A fresh engine: pooled connections from setup's event loop must not leak into the test.
    return create_session_factory(create_async_engine(db_url(tmp_path)))


def _den_id(tmp_path) -> int:
    async def lookup() -> int:
        async with _factory(tmp_path)() as session:
            return await session.scalar(select(Game.id).where(Game.espn_id == "401872995"))

    return asyncio.run(lookup())


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_lines_show_the_latest_snapshot_per_book_with_best_prices_and_consensus(tmp_path) -> None:
    client = _client(tmp_path, [(FIRST, _body()), (SECOND, _body(spread_shift=1.0))])
    body = client.get(f"/api/v1/games/{_den_id(tmp_path)}/lines").json()
    assert body["snapshot_at"].startswith("2026-10-15T15:00:00")
    assert [b["book"] for b in body["books"]] == [
        "BetMGM", "BetRivers", "DraftKings", "FanDuel", "theScore Bet"
    ]
    assert [b["venue"] for b in body["books"]] == [
        "betmgm", "betrivers", "draftkings", "fanduel", "espnbet"
    ]
    dk = next(b for b in body["books"] if b["venue"] == "draftkings")
    assert (dk["spread_home"], dk["spread_home_price"], dk["total"], dk["ml_home"]) == (
        2.5, -110, 42.5, 102
    )
    assert body["consensus"]["spread_home"] == 2.5 and body["consensus"]["books"] == 5
    best = body["best"]
    assert (best["spread_home"]["venue"], best["spread_home"]["price"]) == ("betrivers", -105)
    assert best["spread_home"]["line"] == 2.5 and best["spread_away"]["line"] == -2.5
    assert (best["ml_home"]["venue"], best["ml_home"]["price"]) == ("betmgm", 110)
    # Two snapshots, oldest first: the consensus moved a point toward Denver.
    assert [(p["spread_home"], p["total"]) for p in body["history"]] == [(1.5, 42.5), (2.5, 42.5)]
    assert body["reference"]["current"]["spread_home"] == 1.5  # ESPN, the slate's own reference
    assert body["can_refresh"] is True and body["quota_remaining"] == 450


def test_a_game_with_no_odds_yet_has_empty_lines_and_still_offers_a_refresh(tmp_path) -> None:
    client = _client(tmp_path)
    body = client.get(f"/api/v1/games/{_den_id(tmp_path)}/lines").json()
    assert (body["books"], body["consensus"], body["history"], body["snapshot_at"]) == (
        [], None, [], None
    )
    assert body["reference"]["open"]["spread_home"] == 2.5
    assert client.get("/api/v1/games/9999/lines").status_code == 404


def test_a_flexed_game_keeps_one_history_across_its_two_odds_events(tmp_path) -> None:
    flexed = "2026-10-16T03:15:00Z"
    client = _client(tmp_path, [(FIRST, _body()), (SECOND, _body(commence=flexed))])
    body = client.get(f"/api/v1/games/{_den_id(tmp_path)}/lines").json()
    assert len(body["history"]) == 2 and len(body["books"]) == 5
    assert body["event_id"] is not None


def test_refresh_makes_one_paid_call_saves_it_and_then_waits_out_the_cooldown(tmp_path) -> None:
    client = _client(tmp_path)
    game_id = _den_id(tmp_path)
    first = client.post(f"/api/v1/games/{game_id}/refresh").json()
    assert (first["refreshed"], first["state"], first["quota_remaining"]) == (True, "done", 450)
    assert len(ODDS_CALLS) == 1
    again = client.post(f"/api/v1/games/{game_id}/refresh").json()
    assert (again["refreshed"], again["reason"]) == (False, "recent")
    assert len(ODDS_CALLS) == 1  # no second payment
    lines = client.get(f"/api/v1/games/{game_id}/lines").json()
    assert len(lines["books"]) == 5
    assert (tmp_path / "raw" / "the_odds_api").exists()  # saved before it was parsed


def test_refresh_stops_at_the_quota_floor(tmp_path) -> None:
    client = _client(tmp_path, [(FIRST, _body())])

    async def drain() -> None:
        async with _factory(tmp_path)() as session, session.begin():
            session.add(ProviderRefreshRun(
                provider="the_odds_api", sport="nfl", status="succeeded", started_at=FIRST,
                completed_at=FIRST + timedelta(minutes=1), quota_remaining=5,
            ))

    asyncio.run(drain())
    body = client.post(f"/api/v1/games/{_den_id(tmp_path)}/refresh").json()
    assert (body["refreshed"], body["reason"], body["quota_remaining"]) == (False, "quota", 5)
    assert ODDS_CALLS == []


def test_refresh_needs_live_mode_and_an_admin_and_a_known_game(tmp_path) -> None:
    client = _client(tmp_path, runtime=_runtime(tmp_path, live=False))
    game_id = _den_id(tmp_path)
    fixture = client.post(f"/api/v1/games/{game_id}/refresh")
    assert fixture.status_code == 409 and "PROVIDER_MODE=live" in fixture.json()["detail"]
    assert client.post("/api/v1/games/9999/refresh").status_code == 404
    assert client.get(f"/api/v1/games/{game_id}/lines").json()["can_refresh"] is False


def test_refresh_is_refused_without_the_admin_token_in_live_mode(tmp_path, monkeypatch) -> None:
    live = replace(routes.settings, provider_mode=ProviderMode.LIVE, admin_refresh_secret="s3cret")
    monkeypatch.setattr(routes, "settings", live)
    client = _client(tmp_path)
    game_id = _den_id(tmp_path)
    assert client.post(f"/api/v1/games/{game_id}/refresh").status_code == 403
    assert ODDS_CALLS == []
    ok = client.post(f"/api/v1/games/{game_id}/refresh", headers={"x-admin-token": "s3cret"})
    assert ok.json()["refreshed"] is True and len(ODDS_CALLS) == 1


def test_a_failed_upstream_call_is_a_502_and_is_logged(tmp_path) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    runtime = Runtime(
        TheOddsApiClient("k", transport=httpx.MockTransport(broken), max_retries=0),
        tmp_path / "raw", 5,
    )
    client = _client(tmp_path, runtime=runtime)
    response = client.post(f"/api/v1/games/{_den_id(tmp_path)}/refresh")
    assert response.status_code == 502
    assert "auth_failed" in response.json()["detail"]

    async def failures() -> int:
        async with _factory(tmp_path)() as session:
            return await session.scalar(select(func.count(ProviderRefreshRun.id)).where(
                ProviderRefreshRun.status == "failed"))

    assert asyncio.run(failures()) == 1


def test_after_kickoff_in_game_prices_do_not_become_the_latest_lines(tmp_path) -> None:
    """The odds feed also returns games in progress, so a later snapshot holds in-game prices."""
    kickoff = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)
    during = kickoff + timedelta(minutes=40)
    pregame = kickoff - timedelta(minutes=20)
    client = _client(
        tmp_path, [(pregame, _body()), (during, _body(spread_shift=9.0))],
        now=kickoff + timedelta(hours=2),
    )
    body = client.get(f"/api/v1/games/{_den_id(tmp_path)}/lines").json()
    assert body["snapshot_at"].startswith("2026-10-15T23:55:00")  # the pre-kick snapshot
    assert body["consensus"]["spread_home"] == 1.5
    assert [p["spread_home"] for p in body["history"]] == [1.5]


def _live_without_a_secret(monkeypatch) -> None:
    live = replace(routes.settings, provider_mode=ProviderMode.LIVE, admin_refresh_secret=None)
    monkeypatch.setattr(routes, "settings", live)


def test_the_owners_own_machine_can_refresh_live_prices_without_a_token(
    tmp_path, monkeypatch
) -> None:
    """The browser never holds an admin token, so a local-only app must trust its own host."""
    _live_without_a_secret(monkeypatch)
    client = _client(tmp_path, client=("127.0.0.1", 5000))
    game_id = _den_id(tmp_path)
    lines = client.get(f"/api/v1/games/{game_id}/lines").json()
    assert lines["can_refresh"] is True
    assert client.post(f"/api/v1/games/{game_id}/refresh").json()["refreshed"] is True
    assert len(ODDS_CALLS) == 1


@pytest.mark.parametrize(
    ("host", "headers"),
    [("203.0.113.9", {}), ("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})],
)
def test_other_hosts_and_forwarded_requests_still_cannot_spend(
    tmp_path, monkeypatch, host, headers
) -> None:
    _live_without_a_secret(monkeypatch)
    client = _client(tmp_path, client=(host, 5000))
    game_id = _den_id(tmp_path)
    assert client.get(f"/api/v1/games/{game_id}/lines", headers=headers).status_code == 403
    assert client.post(f"/api/v1/games/{game_id}/refresh", headers=headers).status_code == 403
    assert ODDS_CALLS == []
