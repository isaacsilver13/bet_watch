import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from app.domain.models import Event, MatchStatus, Sport
from app.identity import canonical_event_id
from app.providers.errors import (
    ProviderIncompleteResponseError,
    ProviderPayloadError,
    ProviderUpstreamError,
)
from app.providers.kalshi.adapter import normalize_markets
from app.providers.kalshi.client import KalshiClient

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def _payload(name: str = "kalshi_nfl.json") -> dict:
    return json.loads((FIX / name).read_text())


def _event(home: str, away: str, start: str, eid: str = "event:x") -> Event:
    return Event(
        id=eid, sport=Sport.NFL, league="NFL", home_team=home, away_team=away,
        start_time=datetime.fromisoformat(start),
    )


WAS_SF = _event("San Francisco 49ers", "Washington Commanders", "2026-10-20T00:15:00+00:00")


def test_normalizes_native_prices_rules_and_matches_game() -> None:
    contracts = normalize_markets(_payload(), Sport.NFL, [WAS_SF], NOW)
    assert len(contracts) == 2
    was = next(c for c in contracts if c.contract_id.endswith("-WAS"))
    assert was.venue == "kalshi" and was.outcome == "Washington"
    assert was.probability == 0.23 and was.last_price == 0.23
    assert was.best_bid == 0.23 and was.best_ask == 0.24 and was.midpoint == 0.235
    assert was.spread == pytest.approx(0.01)
    assert was.metadata_updated_at == datetime(2026, 10, 6, 4, 6, 0, 764221, tzinfo=timezone.utc)
    assert was.url.endswith("/kxnflgame/nfl-game/kxnflgame-26oct19wassf")
    assert was.rules_primary.startswith("If Washington wins")
    assert "game ends in a tie" in was.rules  # settlement semantics are carried, not inferred
    assert was.match_status is MatchStatus.MATCHED
    assert was.event_id == canonical_event_id(WAS_SF)
    assert was.matched_selection == "Washington Commanders"


def test_no_sportsbook_event_is_unmatched_not_dropped() -> None:
    contracts = normalize_markets(_payload(), Sport.NFL, [], NOW)
    assert [c.match_status for c in contracts] == [MatchStatus.UNMATCHED] * 2
    assert all(c.event_id is None for c in contracts)


def test_wrong_date_or_teams_do_not_match() -> None:
    wrong_day = _event("San Francisco 49ers", "Washington Commanders", "2026-10-25T00:15:00+00:00")
    other = _event("Dallas Cowboys", "Washington Commanders", "2026-10-20T00:15:00+00:00")
    for ev in (wrong_day, other):
        contracts = normalize_markets(_payload(), Sport.NFL, [ev], NOW)
        assert {c.match_status for c in contracts} == {MatchStatus.UNMATCHED}


def test_two_candidate_events_are_ambiguous() -> None:
    dup = _event(
        "San Francisco 49ers", "Washington Commanders", "2026-10-20T00:20:00+00:00", "event:y"
    )
    contracts = normalize_markets(_payload(), Sport.NFL, [WAS_SF, dup], NOW)
    assert {c.match_status for c in contracts} == {MatchStatus.AMBIGUOUS}


def test_non_moneyline_rules_never_match() -> None:
    payload = copy.deepcopy(_payload())
    for m in payload["markets"]:
        m["rules_primary"] = "If the total points scored are over 45.5, resolves to Yes."
    contracts = normalize_markets(payload, Sport.NFL, [WAS_SF], NOW)
    assert {c.match_status for c in contracts} == {MatchStatus.UNMATCHED}


def test_single_sided_event_never_matches() -> None:
    payload = copy.deepcopy(_payload())
    payload["markets"] = payload["markets"][:1]
    contracts = normalize_markets(payload, Sport.NFL, [WAS_SF], NOW)
    assert contracts[0].match_status is MatchStatus.UNMATCHED


def test_suspended_status_is_kept_and_malformed_market_skipped() -> None:
    payload = copy.deepcopy(_payload())
    payload["markets"][0]["status"] = "inactive"
    payload["markets"][1]["yes_bid_dollars"] = "not-a-number"
    payload["markets"].append({"ticker": "broken"})
    contracts = normalize_markets(payload, Sport.NFL, [WAS_SF], NOW)
    assert len(contracts) == 2
    assert contracts[0].status == "inactive"
    assert contracts[1].best_bid is None  # unparseable quote becomes absent, not a crash


def test_non_object_payload_is_a_typed_error() -> None:
    with pytest.raises(ProviderPayloadError):
        normalize_markets({"markets": "nope"}, Sport.NFL, [], NOW)


def test_nba_fixture_normalizes() -> None:
    contracts = normalize_markets(_payload("kalshi_nba.json"), Sport.NBA, [], NOW)
    assert {c.outcome for c in contracts} == {"Sacramento", "Portland"}


def _client(handler, **kw) -> KalshiClient:
    return KalshiClient(transport=httpx.MockTransport(handler), backoff_seconds=0, **kw)


def test_client_pages_until_cursor_exhausted_and_sends_no_credentials() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(200, json={"markets": [{"ticker": "a"}], "cursor": "c1"})
        return httpx.Response(200, json={"markets": [{"ticker": "b"}], "cursor": ""})

    markets = asyncio.run(_client(handler).fetch_markets(Sport.NBA))
    assert [m["ticker"] for m in markets] == ["a", "b"]
    assert seen[0].url.params["series_ticker"] == "KXNBAGAME"
    assert "authorization" not in seen[0].headers
    assert seen[0].url.host == "external-api.kalshi.com"


def test_client_rejects_repeated_pagination_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"markets": [], "cursor": "same"})

    with pytest.raises(ProviderIncompleteResponseError, match="repeated"):
        asyncio.run(_client(handler).fetch_markets(Sport.NFL))


def test_client_rejects_page_budget_exhaustion(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.kalshi.client.MAX_PAGES", 2)
    page = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page
        page += 1
        return httpx.Response(200, json={"markets": [], "cursor": f"c{page}"})

    with pytest.raises(ProviderIncompleteResponseError, match="page budget"):
        asyncio.run(_client(handler).fetch_markets(Sport.NFL))


def test_client_maps_server_errors_after_retries() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(ProviderUpstreamError):
        asyncio.run(_client(handler, max_retries=1).fetch_markets(Sport.NFL))
    assert len(calls) == 2


# --- route + provider caching -------------------------------------------------------------
from app.api import routes  # noqa: E402
from app.main import app  # noqa: E402
from app.providers.errors import ProviderTimeoutError  # noqa: E402
from app.providers.kalshi.adapter import KalshiFixtureClient, KalshiProvider  # noqa: E402

from tests.local_client import TestClient  # noqa: E402

api = TestClient(app)


class FlakyClient:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.inner = KalshiFixtureClient()

    async def fetch_markets(self, sport):
        self.calls += 1
        if self.fail:
            raise ProviderTimeoutError("down")
        return await self.inner.fetch_markets(sport)


def test_route_is_empty_when_kalshi_disabled(monkeypatch) -> None:
    monkeypatch.setattr(routes, "kalshi", None)
    body = api.get("/api/v1/prediction-markets", params={"sport": "nfl"}).json()
    assert body["contracts"] == [] and body["provider_status"] == {"kalshi": "disabled"}


def test_route_serves_contracts_and_caches_within_ttl(monkeypatch) -> None:
    flaky = FlakyClient()
    monkeypatch.setattr(routes, "kalshi", KalshiProvider(flaky, ttl_seconds=300))
    for _ in range(2):
        body = api.get("/api/v1/prediction-markets", params={"sport": "nfl"}).json()
    assert len(body["contracts"]) == 2
    assert body["contracts"][0]["match_status"] == "unmatched"  # no sportsbook events cached
    assert body["contract_counts"] == {
        "total": 2,
        "matched": 0,
        "unmatched": 2,
        "ambiguous": 0,
        "rejected": 0,
    }
    assert body["provider_details"]["kalshi"]["cache_age_seconds"] is not None
    assert flaky.calls == 1


def test_cache_hits_preserve_the_original_observation_time() -> None:
    provider = KalshiProvider(KalshiFixtureClient(), ttl_seconds=300)

    first = asyncio.run(provider.get_contracts(Sport.NFL, []))
    second = asyncio.run(provider.get_contracts(Sport.NFL, []))

    assert second[0].observed_at == first[0].observed_at


def test_normalization_failure_keeps_last_good_snapshot_and_marks_stale() -> None:
    class MalformedAfterFirstClient(FlakyClient):
        async def fetch_markets(self, sport):
            markets = await super().fetch_markets(sport)
            if self.calls > 1:
                markets = copy.deepcopy(markets)
                for market in markets:
                    market["event_ticker"] = "KXNFLGAME-26FEB31-BAD"
            return markets

    upstream = MalformedAfterFirstClient()
    provider = KalshiProvider(upstream, ttl_seconds=0)
    first = asyncio.run(provider.get_contracts(Sport.NFL, []))
    second = asyncio.run(provider.get_contracts(Sport.NFL, []))

    assert [contract.contract_id for contract in second] == [
        contract.contract_id for contract in first
    ]
    assert second[0].observed_at == first[0].observed_at
    assert provider.status(Sport.NFL) == "stale"
    assert provider.last_error_type(Sport.NFL) == "invalid_payload"
    assert provider.metrics(Sport.NFL)["success_count"] == 1
    assert provider.metrics(Sport.NFL)["error_count"] == 1


def test_failure_serves_stale_then_errors_without_cache_and_never_touches_odds(monkeypatch) -> None:
    flaky = FlakyClient()
    provider = KalshiProvider(flaky, ttl_seconds=0)  # always expired -> always refetch
    monkeypatch.setattr(routes, "kalshi", provider)
    assert api.get("/api/v1/prediction-markets", params={"sport": "nfl"}).json()["contracts"]
    flaky.fail = True
    stale = api.get("/api/v1/prediction-markets", params={"sport": "nfl"}).json()
    assert stale["provider_status"] == {"kalshi": "stale"} and len(stale["contracts"]) == 2
    assert stale["provider_details"]["kalshi"]["error_type"] == "timeout"
    cold = api.get("/api/v1/prediction-markets", params={"sport": "nba"}).json()
    assert cold["provider_status"] == {"kalshi": "error"} and cold["contracts"] == []
    assert api.get("/api/v1/health").status_code == 200  # sportsbook side unaffected


def test_build_kalshi_respects_flag_and_mode() -> None:
    from dataclasses import replace

    from app.config import ProviderMode, load_settings

    base = load_settings()
    assert routes.build_kalshi(replace(base, kalshi_enabled=False)) is None
    fixture = routes.build_kalshi(
        replace(base, kalshi_enabled=True, provider_mode=ProviderMode.FIXTURE)
    )
    assert isinstance(fixture.client, KalshiFixtureClient)
    live = routes.build_kalshi(replace(base, kalshi_enabled=True, provider_mode=ProviderMode.LIVE))
    assert isinstance(live.client, KalshiClient)
    assert live.client.base_url == base.kalshi_base_url


def test_non_json_body_is_a_typed_error_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(ProviderPayloadError):
        asyncio.run(_client(handler).fetch_markets(Sport.NFL))


def test_failed_refresh_backs_off_instead_of_rehitting_upstream() -> None:
    flaky = FlakyClient()
    provider = KalshiProvider(flaky, ttl_seconds=0, retry_after_seconds=60)
    asyncio.run(provider.get_contracts(Sport.NFL, []))
    flaky.fail = True
    for _ in range(3):
        assert len(asyncio.run(provider.get_contracts(Sport.NFL, []))) == 2  # stale served
    assert flaky.calls == 2  # one good fetch + one failure; no further upstream calls
    assert provider.last_error_type(Sport.NFL) == "timeout"


def test_metrics_are_isolated_by_sport() -> None:
    provider = KalshiProvider(KalshiFixtureClient(), ttl_seconds=300)

    asyncio.run(provider.get_contracts(Sport.NFL, []))

    assert provider.metrics(Sport.NFL)["success_count"] == 1
    assert provider.metrics(Sport.NFL)["contract_counts"]["total"] == 2
    assert provider.metrics(Sport.NBA)["success_count"] == 0
    assert provider.metrics(Sport.NBA)["contract_counts"]["total"] == 0
