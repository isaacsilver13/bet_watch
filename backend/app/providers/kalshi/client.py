import asyncio
from typing import Any, Optional

import httpx

from app.domain.models import Sport
from app.providers.errors import (
    ProviderIncompleteResponseError,
    ProviderPayloadError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)

SERIES = {Sport.NFL: "KXNFLGAME", Sport.NBA: "KXNBAGAME"}
MAX_PAGES = 5  # bounded polling: ~1000 contracts is far above a week of NFL/NBA games


class KalshiClient:
    """Read-only client for Kalshi's unauthenticated public market-data endpoint."""

    default_base_url = "https://external-api.kalshi.com/trade-api/v2"

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        base_url: str = default_base_url,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def fetch_markets(self, sport: Sport) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(MAX_PAGES):
            params = {"series_ticker": SERIES[sport], "status": "open", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            page = await self._get_json("/markets", params)
            if not isinstance(page, dict) or not isinstance(page.get("markets"), list):
                raise ProviderPayloadError("Kalshi returned an unexpected markets payload")
            markets.extend(page["markets"])
            next_cursor = page.get("cursor") or ""
            if not next_cursor:
                return markets
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise ProviderIncompleteResponseError("Kalshi repeated a pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise ProviderIncompleteResponseError("Kalshi pagination exceeded the page budget")

    async def _get_json(self, path: str, params: dict[str, str]) -> Any:
        for attempt in range(self.max_retries + 1):
            last = attempt >= self.max_retries
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=self.timeout, transport=self.transport
                ) as client:
                    response = await client.get(path, params=params)
            except httpx.TimeoutException as error:
                if last:
                    raise ProviderTimeoutError("Kalshi request timed out") from error
            except httpx.HTTPError as error:
                if last:
                    raise ProviderUpstreamError("Kalshi request failed") from error
            else:
                if response.status_code == 429:
                    raise ProviderRateLimitError("Kalshi rate limit was reached")
                if 400 <= response.status_code < 500:
                    raise ProviderRequestRejectedError(
                        f"Kalshi rejected the request with status {response.status_code}"
                    )
                if response.status_code < 500:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise ProviderPayloadError("Kalshi returned a non-JSON body") from error
                if last:
                    raise ProviderUpstreamError(
                        f"Kalshi returned upstream status {response.status_code}"
                    )
            await asyncio.sleep(self.backoff_seconds * (2**attempt))
        raise ProviderUpstreamError("Kalshi request exhausted its retry budget")
