import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.models import Event, EventComparison, MarketType, Offer, OfferStatus, Sport
from app.identity import canonicalize_comparison
from app.providers.errors import OddsProviderError
from app.providers.the_odds_api.client import TheOddsApiClient, market_type_for_key, sport_key


class TheOddsApiProvider:
    name = "the_odds_api"
    status = "configured"

    def __init__(self, client: TheOddsApiClient, max_concurrent_prop_fetches: int = 5) -> None:
        self.client = client
        self._max_concurrent_prop_fetches = max_concurrent_prop_fetches
        # Created lazily: on Python 3.9 a Semaphore binds to the event loop that
        # exists when it is constructed, which is not the loop requests run on.
        self._prop_fetch_semaphore: Optional[asyncio.Semaphore] = None

    @property
    def quota_remaining(self) -> Optional[int]:
        return self.client.quota_remaining

    async def get_comparisons(
        self,
        sport: Optional[Sport] = None,
        event_id: Optional[str] = None,
        market_type: Optional[MarketType] = None,
        force_refresh: bool = False,
        include_player_props: bool = False,
    ) -> Sequence[EventComparison]:
        sports = [sport] if sport else list(Sport)
        comparisons: list[EventComparison] = []
        for requested_sport in sports:
            payload = await self.client.fetch_odds(requested_sport)
            sport_comparisons = self._normalize_events(payload, requested_sport)
            if include_player_props:
                if event_id is not None:
                    # One upstream request per *requested* event, not per event in the sport.
                    sport_comparisons = [
                        c
                        for c in sport_comparisons
                        if event_id in (c.event.id, canonicalize_comparison(c).event.id)
                    ]
                sport_comparisons = await self._attach_player_props(
                    sport_comparisons, requested_sport
                )
            comparisons.extend(sport_comparisons)
        return comparisons

    async def _attach_player_props(
        self, comparisons: list[EventComparison], sport: Sport
    ) -> list[EventComparison]:
        """Fetch and merge player-prop offers for each event.

        One extra API request per event -- bounded by a semaphore and
        stopped early once the client's quota is exhausted -- so a single
        page load can't silently burn the whole free-tier budget.
        """

        if self._prop_fetch_semaphore is None:
            self._prop_fetch_semaphore = asyncio.Semaphore(self._max_concurrent_prop_fetches)
        semaphore = self._prop_fetch_semaphore

        async def fetch_one(comparison: EventComparison) -> EventComparison:
            async with semaphore:
                if self.client.quota_remaining is not None and self.client.quota_remaining <= 0:
                    return comparison
                raw_event_id = comparison.event.id.split(":", 1)[1]
                try:
                    raw_payload = await self.client.fetch_event_player_props(sport, raw_event_id)
                except OddsProviderError:
                    return comparison
                if not raw_payload:
                    return comparison
                prop_offers = self._normalize_offers(
                    raw_payload, comparison.event.id, _snapshot_time(comparison)
                )
                return EventComparison(
                    event=comparison.event, offers=[*comparison.offers, *prop_offers]
                )

        return list(await asyncio.gather(*(fetch_one(comparison) for comparison in comparisons)))

    def _normalize_events(
        self, payload: list[dict[str, Any]], sport: Sport
    ) -> list[EventComparison]:
        observed_at = datetime.now(timezone.utc)
        comparisons: list[EventComparison] = []
        for raw_event in payload:
            event_id = f"{self.name}:{raw_event['id']}"
            event = Event(
                id=event_id,
                sport=sport,
                league=raw_event.get("sport_title", sport_key(sport)),
                home_team=raw_event["home_team"],
                away_team=raw_event["away_team"],
                start_time=raw_event["commence_time"],
            )
            offers = self._normalize_offers(raw_event, event_id, observed_at)
            comparisons.append(EventComparison(event=event, offers=offers))
        return comparisons

    def _normalize_offers(
        self, raw_event: dict[str, Any], event_id: str, observed_at: datetime
    ) -> list[Offer]:
        offers: list[Offer] = []
        for bookmaker in raw_event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_type = market_type_for_key(market.get("key", ""))
                if market_type is None:
                    continue
                updated_at = market.get("last_update")
                for index, outcome in enumerate(market.get("outcomes", [])):
                    price = outcome.get("price")
                    if not isinstance(price, (int, float)):
                        continue
                    price_american = int(price)
                    offers.append(
                        Offer(
                            event_id=event_id,
                            provider=self.name,
                            bookmaker=bookmaker.get("title", bookmaker["key"]),
                            market_type=market_type,
                            selection=outcome.get("name", ""),
                            line=outcome.get("point"),
                            price_american=price_american,
                            price_decimal=american_to_decimal(price_american),
                            player_name=outcome.get("description"),
                            provider_offer_id=(
                                f"{raw_event['id']}:{bookmaker['key']}:{market['key']}:{index}"
                            ),
                            provider_updated_at=updated_at,
                            observed_at=observed_at,
                            deep_link=outcome.get("link") or market.get("link"),
                            status=OfferStatus.AVAILABLE,
                        )
                    )
        return offers


def _snapshot_time(comparison: EventComparison) -> datetime:
    # Props share the base offers' observed_at so a saved snapshot (newest observed_at per event)
    # contains both, not just the later-stamped props.
    return max((o.observed_at for o in comparison.offers), default=datetime.now(timezone.utc))


def american_to_decimal(price: int) -> float:
    if price >= 100:
        return round(1 + price / 100, 4)
    return round(1 + 100 / abs(price), 4)
