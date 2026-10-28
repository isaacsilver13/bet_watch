import asyncio
import dataclasses
from datetime import datetime, timezone

import httpx
from app.config import load_settings
from app.domain.models import MarketType, Sport
from app.jobs.runner import get_slot_info, run_due
from app.providers.sgo.adapter import normalize_events
from app.providers.sgo.client import SgoClient
from app.storage.models import OfferObservation
from sqlalchemy import select

from tests.test_nba_odds_job import NAME, NOW, OPEN, YESTERDAY, _job, _setup

OBSERVED = datetime(2026, 1, 15, 16, 30, tzinfo=timezone.utc)


def _quote(odds, spread=None, over_under=None, **extra):
    return {"odds": odds, "spread": spread, "overUnder": over_under,
            "available": True, "lastUpdatedAt": "2026-01-15T16:25:00.000Z", **extra}


PAGE = {"success": True, "nextCursor": None, "data": [
    {
        "eventID": "EVT1",
        "leagueID": "NBA",
        "teams": {"home": {"names": {"long": "Golden State Warriors"}},
                  "away": {"names": {"long": "New York Knicks"}}},
        "status": {"startsAt": "2026-01-16T03:00:00.000Z"},
        "odds": {
            "points-home-game-ml-home": {"byBookmaker": {
                "draftkings": _quote("-150"),
                "caesars": _quote("-155"),  # not a tracked book
                "betmgm": {**_quote("-148"), "available": False},  # pulled
            }},
            "points-home-game-sp-home": {"byBookmaker": {
                "fanduel": _quote("-110", spread="-3.5"),
                "espnbet": _quote("N/A", spread="-3.5"),  # no price
            }},
            "points-all-game-ou-over": {"byBookmaker": {
                "draftkings": _quote("-108", over_under="224.5")}},
            "points-home-1h-sp-home": {"byBookmaker": {"fanduel": _quote("-110", spread="-1.5")}},
            "points+rebounds-STEPH_CURRY_1_NBA-game-ou-over": {"byBookmaker": {
                "fanduel": _quote("-115", over_under="40.5")}},
        },
    },
    {"eventID": "EVT2"},  # no teams, start or odds
]}


def test_only_tracked_available_full_game_prices_become_offers() -> None:
    [comparison] = normalize_events([PAGE], Sport.NBA, OBSERVED)
    assert comparison.event.id == "sgo:EVT1"
    assert comparison.event.home_team == "Golden State Warriors"
    got = {(o.bookmaker, o.market_type, o.selection, o.line, o.price_american)
           for o in comparison.offers}
    assert got == {
        ("DraftKings", MarketType.MONEYLINE, "Golden State Warriors", None, -150),
        ("FanDuel", MarketType.SPREAD, "Golden State Warriors", -3.5, -110),
        ("DraftKings", MarketType.TOTAL, "Over", 224.5, -108),
    }
    assert {o.observed_at for o in comparison.offers} == {OBSERVED}
    assert {o.provider for o in comparison.offers} == {"sgo"}


def test_only_plain_http_links_are_kept() -> None:
    def link(url):
        page = {"data": [{**PAGE["data"][0], "odds": {"points-home-game-ml-home": {"byBookmaker": {
            "draftkings": _quote("-150", deeplink=url)}}}}]}
        return normalize_events([page], Sport.NBA, OBSERVED)[0].offers[0].deep_link

    assert link("https://sportsbook.draftkings.com/x") == "https://sportsbook.draftkings.com/x"
    assert link("javascript:alert(1)") is None and link("data:text/html,x") is None
    assert link("https://a.com/" + "x" * 500) is None and link(None) is None


def _sgo(calls, left=2000, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sgo-key" and "sgo-key" not in str(request.url)
        if request.url.path.endswith("/account/usage"):
            return httpx.Response(200, json={"data": {"rateLimits": {"per-month": {
                "max-entities": 2500, "current-entities": 2500 - left}}}})
        calls.append(request.url)
        return httpx.Response(status, json=PAGE)

    return SgoClient("sgo-key", transport=httpx.MockTransport(handler))


def test_with_the_odds_api_out_of_credits_the_snapshot_falls_back_to_sgo(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx, odds_calls = await _setup(
            tmp_path, monkeypatch, quota=load_settings().provider_min_quota_remaining
        )
        sgo_calls: list = []
        ctx = dataclasses.replace(ctx, sgo=_sgo(sgo_calls))
        assert await run_due([_job()], ctx, NOW) == {YESTERDAY: "missed", OPEN: "partial"}
        assert odds_calls == [] and len(sgo_calls) == 1
        assert sgo_calls[0].params["leagueID"] == "NBA"
        assert (await get_slot_info(ctx, NAME, OPEN)).raw_path.endswith(".json.gz")
        assert "/sgo/" in (await get_slot_info(ctx, NAME, OPEN)).raw_path.replace("\\", "/")
        async with ctx.session_factory() as session:
            rows = (await session.scalars(select(OfferObservation))).all()
        assert {row.provider for row in rows} == {"sgo"} and len(rows) == 3

    asyncio.run(scenario())


def test_sgo_is_not_called_once_its_own_credits_are_at_the_floor(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        floor = load_settings().provider_min_quota_remaining
        ctx, _ = await _setup(tmp_path, monkeypatch, quota=floor)
        sgo_calls: list = []
        ctx = dataclasses.replace(ctx, sgo=_sgo(sgo_calls, left=floor))
        assert await run_due([_job()], ctx, NOW) == {YESTERDAY: "missed", OPEN: "skipped_quota"}
        assert sgo_calls == []

    asyncio.run(scenario())


def test_an_sgo_failure_leaves_the_slot_to_retry(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(
            tmp_path, monkeypatch, quota=load_settings().provider_min_quota_remaining
        )
        ctx = dataclasses.replace(ctx, sgo=_sgo([], status=503))
        assert (await run_due([_job()], ctx, NOW))[OPEN] == "waiting"

    asyncio.run(scenario())
