import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import pytest
from app.domain.models import MarketType, Sport
from app.providers.errors import ProviderTimeoutError
from app.providers.the_odds_api.adapter import TheOddsApiProvider, american_to_decimal

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "the_odds_api_nfl.json"


class FakeTheOddsApiClient:
    def __init__(
        self,
        payload: list[dict[str, Any]],
        prop_payloads: Optional[dict[str, dict[str, Any]]] = None,
    ) -> None:
        self.payload = payload
        self.prop_payloads = prop_payloads or {}
        self.requested_sports: list[Sport] = []
        self.requested_prop_event_ids: list[str] = []
        self.quota_remaining: Optional[int] = None

    async def fetch_odds(self, sport: Sport) -> list[dict[str, Any]]:
        self.requested_sports.append(sport)
        return self.payload

    async def fetch_event_player_props(self, sport: Sport, event_id: str) -> dict[str, Any]:
        self.requested_prop_event_ids.append(event_id)
        return self.prop_payloads.get(event_id, {})


def test_normalizes_events_main_markets_and_props() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    client = FakeTheOddsApiClient(payload)
    provider = TheOddsApiProvider(client)

    comparisons = asyncio.run(provider.get_comparisons(Sport.NFL))

    assert client.requested_sports == [Sport.NFL]
    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert comparison.event.id == "the_odds_api:event-nfl-1"
    assert comparison.event.sport is Sport.NFL
    assert comparison.event.home_team == "Home Hawks"

    moneyline = next(
        offer for offer in comparison.offers if offer.market_type is MarketType.MONEYLINE
    )
    assert moneyline.bookmaker == "DraftKings"
    assert moneyline.selection == "Home Hawks"
    assert moneyline.price_american == -120
    assert moneyline.price_decimal == pytest.approx(1.8333)

    alternate_spreads = [
        offer
        for offer in comparison.offers
        if offer.market_type is MarketType.ALTERNATE_SPREAD
    ]
    assert {offer.line for offer in alternate_spreads} == {-4.5, -1.5, 1.5, 4.5}
    assert all(
        offer.provider_offer_id and "alternate_spreads" in offer.provider_offer_id
        for offer in alternate_spreads
    )
    assert all(offer.market_type is not MarketType.SPREAD for offer in alternate_spreads)

    passing = next(
        offer for offer in comparison.offers if offer.market_type is MarketType.PLAYER_PASSING_YARDS
    )
    assert passing.player_name == "Quarterback One"
    assert passing.selection == "Over"
    assert passing.line == 245.5
    assert passing.provider_updated_at is not None

    assert all(offer.selection != "Ignore me" for offer in comparison.offers)


def test_american_to_decimal_handles_positive_and_negative_prices() -> None:
    assert american_to_decimal(150) == 2.5
    assert american_to_decimal(-200) == 1.5


def test_get_comparisons_without_props_never_calls_per_event_endpoint() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    client = FakeTheOddsApiClient(payload)
    provider = TheOddsApiProvider(client)

    asyncio.run(provider.get_comparisons(Sport.NFL))

    assert client.requested_prop_event_ids == []


def test_include_player_props_merges_per_event_offers() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    prop_payload = {
        "id": "event-nfl-1",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_anytime_td",
                        "last_update": "2026-09-08T12:00:00Z",
                        "outcomes": [
                            {"name": "Yes", "description": "Lead Receiver", "price": 150},
                        ],
                    }
                ],
            }
        ],
    }
    client = FakeTheOddsApiClient(payload, prop_payloads={"event-nfl-1": prop_payload})
    provider = TheOddsApiProvider(client)

    comparisons = asyncio.run(
        provider.get_comparisons(Sport.NFL, include_player_props=True)
    )

    assert client.requested_prop_event_ids == ["event-nfl-1"]
    comparison = comparisons[0]
    anytime_td = next(
        offer
        for offer in comparison.offers
        if offer.market_type is MarketType.PLAYER_ANYTIME_TOUCHDOWN
    )
    assert anytime_td.player_name == "Lead Receiver"
    # Main-market offers from fetch_odds are still present alongside the props.
    assert any(offer.market_type is MarketType.MONEYLINE for offer in comparison.offers)


def test_include_player_props_stops_once_quota_is_exhausted() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())
    client = FakeTheOddsApiClient(payload)
    client.quota_remaining = 0
    provider = TheOddsApiProvider(client)

    comparisons = asyncio.run(
        provider.get_comparisons(Sport.NFL, include_player_props=True)
    )

    assert client.requested_prop_event_ids == []
    assert comparisons[0].offers  # main-market offers are unaffected


def test_include_player_props_skips_event_on_per_event_error() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())

    class FailingPropsClient(FakeTheOddsApiClient):
        async def fetch_event_player_props(self, sport, event_id):
            self.requested_prop_event_ids.append(event_id)
            raise ProviderTimeoutError("props request timed out")

    client = FailingPropsClient(payload)
    provider = TheOddsApiProvider(client)

    comparisons = asyncio.run(
        provider.get_comparisons(Sport.NFL, include_player_props=True)
    )

    assert client.requested_prop_event_ids == ["event-nfl-1"]
    assert comparisons[0].offers  # main-market offers survive the props failure


def _captured_params(**client_kwargs: Any) -> dict[str, str]:
    import httpx
    from app.providers.the_odds_api.client import TheOddsApiClient

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=[], headers={"x-requests-remaining": "400"})

    client = TheOddsApiClient("k", transport=httpx.MockTransport(handler), **client_kwargs)
    asyncio.run(client.fetch_odds(Sport.NFL))
    return seen


def test_client_defaults_to_us_region() -> None:
    params = _captured_params()
    assert params["regions"] == "us"
    assert "bookmakers" not in params


def test_bookmakers_allowlist_takes_precedence_over_regions() -> None:
    params = _captured_params(regions="us,eu", bookmakers=("draftkings", "fanduel"))
    assert params["bookmakers"] == "draftkings,fanduel"
    assert "regions" not in params  # sending both would bill both


def test_settings_parse_and_validate_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import load_settings

    monkeypatch.setenv("ODDS_BOOKMAKERS", " DraftKings, fanduel ,draftkings")
    monkeypatch.setenv("ODDS_REGIONS", "us, eu")
    s = load_settings()
    assert s.odds_bookmakers == ("draftkings", "fanduel")
    assert s.odds_regions == "us,eu"

    monkeypatch.setenv("ODDS_BOOKMAKERS", "draft kings!")
    with pytest.raises(ValueError, match="ODDS_BOOKMAKERS"):
        load_settings()
    monkeypatch.setenv("ODDS_BOOKMAKERS", "")
    monkeypatch.setenv("ODDS_REGIONS", "mars")
    with pytest.raises(ValueError, match="ODDS_REGIONS"):
        load_settings()
