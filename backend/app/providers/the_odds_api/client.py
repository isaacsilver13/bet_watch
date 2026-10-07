import asyncio
from typing import Any, Optional

import httpx

from app.domain.models import MarketType, Sport
from app.providers.errors import (
    ProviderAuthError,
    ProviderPayloadError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)

# Featured markets accepted by the bulk /sports/{sport}/odds endpoint used by
# fetch_odds(). Anything else -- alternate lines and player props -- is only
# served per event via /sports/{sport}/events/{event_id}/odds; asking for it in
# bulk makes the whole request fail with 422 INVALID_MARKET (verified live
# 2026-09-29), which left the app with no odds at all.
CORE_MARKETS = ("h2h", "spreads", "totals")

# Player prop markets are NOT returned by the bulk odds endpoint -- The Odds API
# only exposes them per-event via /sports/{sport}/events/{event_id}/odds. See
# fetch_event_player_props(). Each sport's list includes the corresponding
# "_alternate" markets where The Odds API offers alternate lines; unsupported
# keys are simply omitted from the response rather than erroring the request.
NFL_PLAYER_PROP_MARKETS = (
    "player_pass_yds",
    "player_pass_yds_alternate",
    "player_rush_yds",
    "player_rush_yds_alternate",
    "player_reception_yds",
    "player_reception_yds_alternate",
    "player_pass_rush_yds",
    "player_pass_rush_yds_alternate",
    "player_anytime_td",
)

NBA_PLAYER_PROP_MARKETS = (
    "player_points",
    "player_points_alternate",
    "player_rebounds",
    "player_rebounds_alternate",
    "player_assists",
    "player_assists_alternate",
    "player_threes",
    "player_threes_alternate",
    "player_points_rebounds_assists",
    "player_points_rebounds_assists_alternate",
)

# Map sports to their available markets
SPORT_MARKETS = {
    "NFL": CORE_MARKETS,
    "NBA": CORE_MARKETS,
}

SPORT_PLAYER_PROP_MARKETS = {
    "NFL": NFL_PLAYER_PROP_MARKETS,
    "NBA": NBA_PLAYER_PROP_MARKETS,
}


class TheOddsApiClient:
    base_url = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: str,
        timeout: float = 15.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        regions: str = "us",
        bookmakers: tuple = (),
    ) -> None:
        # With a bookmaker allowlist, send only `bookmakers` (it overrides `regions`).
        self.coverage = (
            {"bookmakers": ",".join(bookmakers)} if bookmakers else {"regions": regions}
        )
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.transport = transport
        self.quota_remaining: Optional[int] = None

    async def fetch_odds(self, sport: Sport) -> list[dict[str, Any]]:
        sport_name = sport.value.upper()  # Convert Sport enum to string like "NFL", "NBA"
        markets = SPORT_MARKETS.get(sport_name, CORE_MARKETS)
        payload = await self._get_json(
            f"/sports/{sport_key(sport)}/odds",
            {
                "apiKey": self.api_key,
                **self.coverage,
                "markets": ",".join(markets),
                "oddsFormat": "american",
            },
        )
        if not isinstance(payload, list):
            raise ProviderPayloadError("The Odds API returned a non-list payload")
        return payload

    async def fetch_event_player_props(self, sport: Sport, event_id: str) -> dict[str, Any]:
        """Fetch player-prop odds for a single event.

        The Odds API only exposes player props via this per-event endpoint,
        not the bulk /sports/{sport}/odds endpoint fetch_odds() uses -- so
        this costs one additional API request per event.
        """
        sport_name = sport.value.upper()
        markets = SPORT_PLAYER_PROP_MARKETS.get(sport_name, ())
        if not markets:
            return {}
        payload = await self._get_json(
            f"/sports/{sport_key(sport)}/events/{event_id}/odds",
            {
                "apiKey": self.api_key,
                **self.coverage,
                "markets": ",".join(markets),
                "oddsFormat": "american",
            },
        )
        if not isinstance(payload, dict):
            raise ProviderPayloadError("The Odds API returned a non-object payload for event odds")
        return payload

    async def _get_json(self, path: str, params: dict[str, str]) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=self.timeout,
                    transport=self.transport,
                ) as client:
                    response = await client.get(path, params=params)
                    self.quota_remaining = _quota_remaining(response)
                    if response.status_code in {401, 403}:
                        raise ProviderAuthError("The Odds API rejected the configured credentials")
                    if response.status_code == 429:
                        raise ProviderRateLimitError("The Odds API rate limit was reached")
                    if response.status_code >= 500:
                        raise ProviderUpstreamError(
                            f"The Odds API returned upstream status {response.status_code}"
                        )
                    if response.status_code >= 400:
                        # Never retried: a 4xx (e.g. 422 INVALID_MARKET) will not
                        # succeed on a second try. Report status + body only --
                        # not the httpx error, whose text includes the apiKey URL.
                        raise ProviderRequestRejectedError(
                            f"The Odds API rejected the request with status "
                            f"{response.status_code}: {response.text[:300]}"
                        )
                    return response.json()
            except (
                ProviderAuthError,
                ProviderRateLimitError,
                ProviderPayloadError,
                ProviderRequestRejectedError,
            ):
                raise
            except httpx.TimeoutException as error:
                if attempt >= self.max_retries:
                    raise ProviderTimeoutError("The Odds API request timed out") from error
            except (httpx.HTTPError, ProviderUpstreamError) as error:
                if attempt >= self.max_retries:
                    if isinstance(error, ProviderUpstreamError):
                        raise
                    raise ProviderUpstreamError("The Odds API request failed") from error

            await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise ProviderUpstreamError("The Odds API request exhausted its retry budget")


def _quota_remaining(response: httpx.Response) -> Optional[int]:
    value = response.headers.get("x-requests-remaining")
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def sport_key(sport: Sport) -> str:
    return {
        Sport.NFL: "americanfootball_nfl",
        Sport.NBA: "basketball_nba",
    }[sport]


def market_type_for_key(market_key: str) -> Optional[MarketType]:
    # Alternate-line markets share the same MarketType as their base market --
    # the specific line is already captured per-offer via Offer.line, so
    # alternates just show up as additional rows/lines under the same market.
    return {
        "h2h": MarketType.MONEYLINE,
        "spreads": MarketType.SPREAD,
        "totals": MarketType.TOTAL,
        "alternate_spreads": MarketType.ALTERNATE_SPREAD,
        "alternate_totals": MarketType.TOTAL,
        "player_points": MarketType.PLAYER_POINTS,
        "player_points_alternate": MarketType.PLAYER_POINTS,
        "player_rebounds": MarketType.PLAYER_REBOUNDS,
        "player_rebounds_alternate": MarketType.PLAYER_REBOUNDS,
        "player_assists": MarketType.PLAYER_ASSISTS,
        "player_assists_alternate": MarketType.PLAYER_ASSISTS,
        "player_threes": MarketType.PLAYER_THREES,
        "player_threes_alternate": MarketType.PLAYER_THREES,
        "player_points_rebounds_assists": MarketType.PLAYER_POINTS_REBOUNDS_ASSISTS,
        "player_points_rebounds_assists_alternate": MarketType.PLAYER_POINTS_REBOUNDS_ASSISTS,
        "player_pass_yds": MarketType.PLAYER_PASSING_YARDS,
        "player_pass_yds_alternate": MarketType.PLAYER_PASSING_YARDS,
        "player_rush_yds": MarketType.PLAYER_RUSHING_YARDS,
        "player_rush_yds_alternate": MarketType.PLAYER_RUSHING_YARDS,
        "player_reception_yds": MarketType.PLAYER_RECEIVING_YARDS,
        "player_reception_yds_alternate": MarketType.PLAYER_RECEIVING_YARDS,
        "player_pass_rush_yds": MarketType.PLAYER_PASSING_RUSHING_YARDS,
        "player_pass_rush_yds_alternate": MarketType.PLAYER_PASSING_RUSHING_YARDS,
        "player_anytime_td": MarketType.PLAYER_ANYTIME_TOUCHDOWN,
    }.get(market_key)


