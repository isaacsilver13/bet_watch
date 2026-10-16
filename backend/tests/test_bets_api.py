import asyncio
from datetime import datetime, timezone

import pytest
from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_scoreboard
from app.ingest.seed import seed_teams
from app.main import app
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Game
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)


def _factory(tmp_path):
    return create_session_factory(create_async_engine(db_url(tmp_path)))


def _client(tmp_path) -> TestClient:
    async def setup():
        factory = await new_session_factory(tmp_path)
        await seed_teams(factory)
        await GamesRepository(factory).upsert_games(
            "nfl", "espn", parse_scoreboard(load_json("espn/scoreboard_week6.json")), NOW
        )

    asyncio.run(setup())
    factory = _factory(tmp_path)
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def _game_id(tmp_path) -> int:
    async def lookup() -> int:
        async with _factory(tmp_path)() as session:
            return await session.scalar(select(Game.id).where(Game.espn_id == "401872995"))

    return asyncio.run(lookup())


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _bet(game_id, **changes):
    body = {"game_id": game_id, "venue": "fanduel", "market": "spread", "selection": "home",
            "line": 1.5, "price_american": -110, "stake": 10.0}
    body.update(changes)
    return body


def test_a_bet_is_logged_open_with_the_fee_its_book_charges(tmp_path) -> None:
    client = _client(tmp_path)
    game_id = _game_id(tmp_path)
    created = client.post("/api/v1/bets", json=_bet(game_id))
    assert created.status_code == 201
    bet = created.json()
    assert (bet["status"], bet["fee"], bet["profit"], bet["payout"]) == ("open", 0.5, None, None)
    assert bet["game"]["away"] == "SEA" and bet["game"]["home"] == "DEN"
    assert bet["placed_at"].startswith("2026-10-15T16:00:00")
    # $25 at FanDuel and $50 at DraftKings clear their fees; BetMGM never charged one.
    assert client.post("/api/v1/bets", json=_bet(game_id, stake=25.0)).json()["fee"] == 0.0
    dk = client.post("/api/v1/bets", json=_bet(game_id, venue="draftkings", stake=49.0))
    assert dk.json()["fee"] == 0.5
    assert client.post("/api/v1/bets", json=_bet(game_id, venue="betmgm")).json()["fee"] == 0.0
    known = client.post("/api/v1/bets", json=_bet(game_id, fee=0.25)).json()
    assert known["fee"] == 0.25  # what the book actually charged wins over the rule


@pytest.mark.parametrize(
    "changes",
    [
        {"line": None},  # a spread needs its line
        {"market": "moneyline"},  # a moneyline has none
        {"market": "total", "selection": "home", "line": 44.5},  # totals are over or under
        {"selection": "over"},  # spreads are home or away
        {"price_american": 50},  # American odds start at 100
        {"stake": 0},
        {"venue": "caesars"},  # not tracked
    ],
)
def test_bad_bets_are_rejected(tmp_path, changes) -> None:
    client = _client(tmp_path)
    assert client.post("/api/v1/bets", json=_bet(_game_id(tmp_path), **changes)).status_code == 422


def test_a_bet_on_an_unknown_game_is_a_404(tmp_path) -> None:
    assert _client(tmp_path).post("/api/v1/bets", json=_bet(9999)).status_code == 404


def test_open_bets_can_be_edited_and_the_fee_follows_the_stake_and_venue(tmp_path) -> None:
    client = _client(tmp_path)
    bet_id = client.post("/api/v1/bets", json=_bet(_game_id(tmp_path))).json()["id"]
    edited = client.patch(f"/api/v1/bets/{bet_id}", json={"stake": 30.0, "price_american": -105})
    assert (edited.json()["stake"], edited.json()["price_american"], edited.json()["fee"]) == (
        30.0, -105, 0.0)  # $30 clears FanDuel's $25 waiver
    moved = client.patch(f"/api/v1/bets/{bet_id}", json={"venue": "draftkings"}).json()
    assert (moved["venue"], moved["fee"]) == ("draftkings", 0.5)  # $30 is under DraftKings' $50
    override = client.patch(f"/api/v1/bets/{bet_id}", json={"fee": 0.0, "note": "free bet"}).json()
    assert (override["fee"], override["note"]) == (0.0, "free bet")
    voided = client.patch(f"/api/v1/bets/{bet_id}", json={"status": "void"}).json()
    assert (voided["status"], voided["payout"], voided["profit"]) == ("void", 30.0, 0.0)
    again = client.patch(f"/api/v1/bets/{bet_id}", json={"stake": 5.0})
    assert again.status_code == 409  # only open bets change
    assert client.patch("/api/v1/bets/9999", json={"stake": 5.0}).status_code == 404
    assert client.patch(f"/api/v1/bets/{bet_id}", json={"price_american": 20}).status_code == 422


def test_listing_filters_by_status_and_game_newest_first(tmp_path) -> None:
    client = _client(tmp_path)
    game_id = _game_id(tmp_path)
    first = client.post("/api/v1/bets", json=_bet(game_id)).json()["id"]
    second = client.post("/api/v1/bets", json=_bet(game_id, market="total", selection="over",
                                                   line=42.5)).json()["id"]
    client.patch(f"/api/v1/bets/{first}", json={"status": "void"})
    assert [b["id"] for b in client.get("/api/v1/bets").json()] == [second, first]
    assert [b["id"] for b in client.get("/api/v1/bets", params={"status": "open"}).json()] == [
        second]
    assert [b["id"] for b in client.get("/api/v1/bets", params={"status": "settled"}).json()] == [
        first]
    assert client.get("/api/v1/bets", params={"game_id": game_id + 1}).json() == []


def test_the_summary_groups_by_venue_sport_and_market(tmp_path) -> None:
    client = _client(tmp_path)
    game_id = _game_id(tmp_path)
    client.post("/api/v1/bets", json=_bet(game_id))
    client.post("/api/v1/bets", json=_bet(game_id, venue="betmgm", market="moneyline",
                                          selection="away", line=None, price_american=120))
    by_venue = client.get("/api/v1/bets/summary", params={"group_by": "venue"}).json()
    assert by_venue["group_by"] == "venue"
    assert [g["key"] for g in by_venue["groups"]] == ["betmgm", "fanduel"]
    assert (by_venue["total"]["bets"], by_venue["total"]["open"], by_venue["total"]["roi"]) == (
        2, 2, None)
    assert [g["key"] for g in client.get("/api/v1/bets/summary", params={"group_by": "market"}
                                         ).json()["groups"]] == ["moneyline", "spread"]
    assert client.get("/api/v1/bets/summary", params={"group_by": "color"}).status_code == 422
