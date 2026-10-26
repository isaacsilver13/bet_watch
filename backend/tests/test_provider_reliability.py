import asyncio
from typing import Optional

import httpx
import pytest
from app.domain.models import EventComparison, Sport
from app.providers.cache import CachedOddsProvider
from app.providers.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)
from app.providers.the_odds_api.client import TheOddsApiClient


class CountingProvider:
    name = "counting"
    status = "configured"

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.error: Optional[Exception] = None
        self.quota_remaining: Optional[int] = None

    async def get_comparisons(
        self,
        sport: Optional[Sport] = None,
        event_id: Optional[str] = None,
        market_type=None,
        force_refresh: bool = False,
        include_player_props: bool = False,
    ) -> list[EventComparison]:
        self.calls += 1
        if self.error:
            raise self.error
        if self.fail:
            raise TimeoutError("fixture failure")
        return []


def test_cache_coalesces_concurrent_refreshes() -> None:
    async def scenario() -> None:
        inner = CountingProvider()
        provider = CachedOddsProvider(inner, ttl_seconds=60)

        await asyncio.gather(*[provider.get_comparisons(Sport.NFL) for _ in range(3)])

        assert inner.calls == 1
        assert provider.cache_hit is True

    asyncio.run(scenario())


def test_cache_returns_last_good_snapshot_after_refresh_failure() -> None:
    async def scenario() -> None:
        inner = CountingProvider()
        provider = CachedOddsProvider(inner, ttl_seconds=0)

        await provider.get_comparisons(Sport.NFL)
        inner.fail = True
        await provider.get_comparisons(Sport.NFL)

        assert inner.calls == 2
        assert provider.status == "stale"
        assert provider.last_error_type == "TimeoutError"

    asyncio.run(scenario())


def test_odds_api_client_captures_quota_remaining() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"x-requests-remaining": "17"},
                json=[],
                request=request,
            )

        client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))

        assert await client.fetch_odds(Sport.NFL) == []
        assert client.quota_remaining == 17

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(401, ProviderAuthError), (429, ProviderRateLimitError)],
)
def test_odds_api_client_maps_non_retryable_provider_errors(status_code, error_type) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, request=request)

        client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))

        with pytest.raises(error_type):
            await client.fetch_odds(Sport.NFL)

    asyncio.run(scenario())


def test_odds_api_client_maps_timeout_after_retry_budget() -> None:
    async def scenario() -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timed out", request=request)

        client = TheOddsApiClient(
            "test-key",
            max_retries=1,
            backoff_seconds=0,
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(ProviderTimeoutError):
            await client.fetch_odds(Sport.NFL)
        assert calls == 2

    asyncio.run(scenario())


def test_cache_suppresses_refresh_when_quota_is_at_threshold() -> None:
    async def scenario() -> None:
        inner = CountingProvider()
        provider = CachedOddsProvider(inner, ttl_seconds=0, min_quota_remaining=5)

        await provider.get_comparisons(Sport.NFL)
        inner.quota_remaining = 5
        await provider.get_comparisons(Sport.NFL)

        assert inner.calls == 1
        assert provider.last_error_type == "quota_low"
        assert provider.status == "stale"

    asyncio.run(scenario())


def test_cache_force_refresh_bypasses_warm_snapshot() -> None:
    async def scenario() -> None:
        inner = CountingProvider()
        provider = CachedOddsProvider(inner, ttl_seconds=60)

        await provider.get_comparisons(Sport.NFL)
        await provider.get_comparisons(Sport.NFL, force_refresh=True)

        assert inner.calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("sport", "expected_path"),
    [
        (Sport.NFL, "/v4/sports/americanfootball_nfl/odds"),
        (Sport.NBA, "/v4/sports/basketball_nba/odds"),
    ],
)
def test_odds_api_client_bulk_request_uses_sport_path_and_featured_markets(
    sport: Sport, expected_path: str
) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v4/sports/":
                return httpx.Response(200, json=[], headers={"x-requests-remaining": "480"})
            assert request.url.path == expected_path
            requested_markets = set(request.url.params["markets"].split(","))
            # Alternates are per-event only; in bulk they cause 422 INVALID_MARKET.
            assert requested_markets == {"h2h", "spreads", "totals"}
            return httpx.Response(200, json=[], request=request)

        client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))

        assert await client.fetch_odds(sport) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("sport", "expected_path", "expected_markets"),
    [
        (
            Sport.NFL,
            "/v4/sports/americanfootball_nfl/events/event-1/odds",
            {"player_pass_yds", "player_pass_rush_yds", "player_anytime_td"},
        ),
        (
            Sport.NBA,
            "/v4/sports/basketball_nba/events/event-1/odds",
            {"player_points", "player_points_rebounds_assists"},
        ),
    ],
)
def test_odds_api_client_requests_player_props_per_event(
    sport: Sport, expected_path: str, expected_markets: set[str]
) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v4/sports/":
                return httpx.Response(200, json=[], headers={"x-requests-remaining": "480"})
            assert request.url.path == expected_path
            requested_markets = set(request.url.params["markets"].split(","))
            assert expected_markets.issubset(requested_markets)
            # h2h/spreads/totals are only requested via fetch_odds, not here
            assert "h2h" not in requested_markets
            return httpx.Response(200, json={"id": "event-1", "bookmakers": []}, request=request)

        client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))

        payload = await client.fetch_event_player_props(sport, "event-1")
        assert payload == {"id": "event-1", "bookmakers": []}

    asyncio.run(scenario())

def _bulk_endpoint_handler(requests: list[httpx.Request]):
    """Mimics The Odds API bulk endpoint: only featured markets are allowed."""
    allowed = {"h2h", "spreads", "totals"}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v4/sports/":
            return httpx.Response(200, headers={"x-requests-remaining": "480"}, json=[])
        markets = set(request.url.params["markets"].split(","))
        unsupported = sorted(markets - allowed)
        if unsupported:
            return httpx.Response(
                422,
                headers={"x-requests-remaining": "480"},
                json={
                    "message": "Markets not supported by this endpoint: " + ", ".join(unsupported),
                    "error_code": "INVALID_MARKET",
                },
                request=request,
            )
        return httpx.Response(
            200, headers={"x-requests-remaining": "477"}, json=[], request=request
        )

    return handler


@pytest.mark.parametrize("sport", [Sport.NFL, Sport.NBA])
def test_bulk_request_only_asks_for_markets_the_bulk_endpoint_supports(sport: Sport) -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []
        client = TheOddsApiClient(
            "test-key", transport=httpx.MockTransport(_bulk_endpoint_handler(requests))
        )

        assert await client.fetch_odds(sport) == []
        assert len(requests) == 2
        assert requests[1].url.params["markets"] == "h2h,spreads,totals"

    asyncio.run(scenario())


def test_client_error_reports_status_and_body_and_is_not_retried() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/v4/sports/":
                return httpx.Response(200, headers={"x-requests-remaining": "480"}, json=[])
            return httpx.Response(
                422,
                json={"message": "Markets not supported", "error_code": "INVALID_MARKET"},
                request=request,
            )

        client = TheOddsApiClient(
            "test-key", backoff_seconds=0, transport=httpx.MockTransport(handler)
        )

        with pytest.raises(ProviderUpstreamError) as error:
            await client.fetch_odds(Sport.NFL)

        assert "422" in str(error.value)
        assert "INVALID_MARKET" in str(error.value)
        assert "test-key" not in str(error.value)
        assert len(requests) == 2  # one free preflight and one non-retried paid request

    asyncio.run(scenario())


def test_rate_limit_starts_a_cooldown_during_which_the_provider_is_not_called() -> None:
    async def scenario() -> None:
        inner = CountingProvider()
        provider = CachedOddsProvider(inner, ttl_seconds=0, rate_limit_cooldown_seconds=600)

        await provider.get_comparisons(Sport.NFL)  # last good snapshot
        inner.error = ProviderRateLimitError("The Odds API rate limit was reached")
        await provider.get_comparisons(Sport.NFL)  # hits the 429, serves last good
        calls_after_429 = inner.calls
        await provider.get_comparisons(Sport.NFL)
        await provider.get_comparisons(Sport.NFL, force_refresh=True)

        assert inner.calls == calls_after_429  # backed off: no further upstream calls
        assert provider.status == "stale"
        assert provider.last_error_type == "rate_limited"

    asyncio.run(scenario())


def test_rate_limit_cooldown_without_a_snapshot_raises_without_calling_provider() -> None:
    async def scenario() -> None:
        inner = CountingProvider()
        inner.error = ProviderRateLimitError("The Odds API rate limit was reached")
        provider = CachedOddsProvider(inner, ttl_seconds=0, rate_limit_cooldown_seconds=600)

        with pytest.raises(ProviderRateLimitError):
            await provider.get_comparisons(Sport.NFL)
        with pytest.raises(ProviderRateLimitError):
            await provider.get_comparisons(Sport.NFL)

        assert inner.calls == 1

    asyncio.run(scenario())
