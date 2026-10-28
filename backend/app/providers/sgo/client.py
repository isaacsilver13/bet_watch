import json
from datetime import datetime
from typing import Any, Optional

import httpx

from app.config import ProviderMode, Settings
from app.domain.models import Sport
from app.providers.errors import (
    ProviderAuthError,
    ProviderPayloadError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)

MAX_PAGES = 5  # a stuck cursor must not bill the free tier away page by page
LEAGUES = {Sport.NFL: "NFL", Sport.NBA: "NBA"}


class SgoClient:
    """SportsGameOdds v2, the free tier: billed per event returned, whatever the books or markets.

    The key travels in a header, so no URL or error text here ever contains it."""

    base_url = "https://api.sportsgameodds.com/v2"

    def __init__(
        self,
        api_key: str,
        timeout: float = 15.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> Optional["SgoClient"]:
        if settings.provider_mode is not ProviderMode.LIVE or not settings.sgo_api_key:
            return None
        return cls(settings.sgo_api_key, timeout=settings.provider_timeout_seconds)

    async def fetch_events_raw(self, sport: Sport, after: datetime, before: datetime) -> str:
        """Every game starting in the window, as a JSON list of the pages, unparsed, so the paid
        response can be saved before it is read."""
        pages: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while len(pages) < MAX_PAGES:
            params = {
                "leagueID": LEAGUES[sport],
                "startsAfter": after.isoformat(),
                "startsBefore": before.isoformat(),
                "limit": "50",
            }
            if cursor:
                params["cursor"] = cursor
            body = await self._get("/events", params)
            pages.append(body)
            cursor = body.get("nextCursor")
            if not cursor:
                break
        return json.dumps(pages)

    async def fetch_quota_remaining(self) -> Optional[int]:
        """Events left this month, from the free account endpoint; None when it cannot be read."""
        try:
            body = await self._get("/account/usage", {})
            month = body["data"]["rateLimits"]["per-month"]
            return int(month["max-entities"]) - int(month["current-entities"])
        except (ProviderPayloadError, KeyError, TypeError, ValueError):
            return None

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
                headers={"x-api-key": self.api_key},
            ) as client:
                response = await client.get(path, params=params)
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("SportsGameOdds request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUpstreamError("SportsGameOdds request failed") from error
        if response.status_code in {401, 403}:
            raise ProviderAuthError("SportsGameOdds rejected the configured credentials")
        if response.status_code == 429:
            raise ProviderRateLimitError("SportsGameOdds rate limit was reached")
        if response.status_code >= 500:
            raise ProviderUpstreamError(f"SportsGameOdds returned status {response.status_code}")
        if response.status_code >= 400:
            raise ProviderRequestRejectedError(
                f"SportsGameOdds rejected the request with status "
                f"{response.status_code}: {response.text[:300]}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderPayloadError("SportsGameOdds returned a non-JSON body") from error
        if not isinstance(body, dict):
            raise ProviderPayloadError("SportsGameOdds returned a non-object body")
        return body
