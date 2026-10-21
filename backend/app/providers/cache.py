import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.domain.models import EventComparison, MarketType, Sport
from app.identity import canonicalize_comparison, scope_comparisons
from app.providers.base import OddsProvider
from app.providers.errors import ProviderQuotaError, ProviderRateLimitError


class CachedOddsProvider:
    def __init__(
        self,
        provider: OddsProvider,
        ttl_seconds: int,
        min_quota_remaining: int = 0,
        rate_limit_cooldown_seconds: int = 900,
    ) -> None:
        self.provider = provider
        self.rate_limit_cooldown = timedelta(seconds=rate_limit_cooldown_seconds)
        self._cooldown_until: Optional[datetime] = None
        self._cooldown_error: Optional[ProviderRateLimitError] = None
        self.ttl = timedelta(seconds=ttl_seconds)
        self.min_quota_remaining = min_quota_remaining
        self._cache: dict[
            tuple[Optional[Sport], Optional[str], Optional[MarketType], bool],
            tuple[datetime, Sequence[EventComparison]],
        ] = {}
        self._lock = asyncio.Lock()
        self._last_error_type: Optional[str] = None
        self._cache_hit = False
        self._last_refreshed_at: Optional[datetime] = None

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def status(self) -> str:
        if self._last_error_type and self._cache:
            return "stale"
        return self.provider.status

    @property
    def cache_hit(self) -> bool:
        return self._cache_hit

    @property
    def last_error_type(self) -> Optional[str]:
        return self._last_error_type

    @property
    def last_refreshed_at(self) -> Optional[datetime]:
        return self._last_refreshed_at

    @property
    def quota_remaining(self) -> Optional[int]:
        return getattr(self.provider, "quota_remaining", None)

    @property
    def cached_event_count(self) -> int:
        event_ids = {
            comparison.event.id
            for comparisons in self._cache.values()
            for comparison in comparisons[1]
        }
        return len(event_ids)

    def cached_events(self, sport: Sport) -> list:
        """Events already in memory for `sport`. Never fetches (no quota spend)."""
        seen: dict = {}
        for (key_sport, *_), (_, comparisons) in self._cache.items():
            if key_sport == sport:
                seen.update({c.event.id: c.event for c in comparisons})
        return list(seen.values())

    def cached_comparisons(self, sport: Optional[Sport]) -> list[EventComparison]:
        """The last board fetched for `sport`, if any. Never fetches (no quota spend)."""
        cached = self._cache.get((sport, None, None, False))
        return list(cached[1]) if cached else []

    async def get_comparisons(
        self,
        sport: Optional[Sport] = None,
        event_id: Optional[str] = None,
        market_type: Optional[MarketType] = None,
        force_refresh: bool = False,
        include_player_props: bool = False,
    ) -> Sequence[EventComparison]:
        # market_type never changes what is fetched upstream, and event_id only does for
        # props. Keying on them for ordinary reads would let callers mint fresh cache
        # entries (and so fresh paid fetches) just by varying query parameters.
        cache_key = (sport, event_id if include_player_props else None, None, include_player_props)
        upstream_event_id = event_id if include_player_props else None
        now = datetime.now(timezone.utc)
        cached = self._cache.get(cache_key)
        if not force_refresh and cached and now - cached[0] < self.ttl:
            self._cache_hit = True
            return scope_comparisons(list(cached[1]), event_id, market_type)

        async with self._lock:
            now = datetime.now(timezone.utc)
            cached = self._cache.get(cache_key)
            if not force_refresh and cached and now - cached[0] < self.ttl:
                self._cache_hit = True
                return scope_comparisons(list(cached[1]), event_id, market_type)

            self._cache_hit = False
            if self._cooldown_until is not None and now < self._cooldown_until:
                # Backing off after a 429: don't spend more upstream calls.
                self._last_error_type = ProviderRateLimitError.error_type
                if cached:
                    return scope_comparisons(list(cached[1]), event_id, market_type)
                raise self._cooldown_error or ProviderRateLimitError("Provider rate limited")
            quota_remaining = getattr(self.provider, "quota_remaining", None)
            if quota_remaining is not None and quota_remaining <= self.min_quota_remaining:
                quota_error = ProviderQuotaError(
                    f"Provider quota is {quota_remaining}; refresh threshold is "
                    f"{self.min_quota_remaining}"
                )
                self._last_error_type = quota_error.error_type
                if cached:
                    return scope_comparisons(list(cached[1]), event_id, market_type)
                raise quota_error
            try:
                comparisons = [
                    canonicalize_comparison(comparison)
                    for comparison in await self.provider.get_comparisons(
                        sport=sport,
                        event_id=upstream_event_id,
                        market_type=None,
                        force_refresh=force_refresh,
                        include_player_props=include_player_props,
                    )
                ]
            except Exception as error:
                if isinstance(error, ProviderRateLimitError):
                    self._cooldown_until = now + self.rate_limit_cooldown
                    self._cooldown_error = error
                self._last_error_type = getattr(error, "error_type", type(error).__name__)
                if cached:
                    return scope_comparisons(list(cached[1]), event_id, market_type)
                raise

            self._last_error_type = None
            self._cooldown_until = None
            self._last_refreshed_at = now
            self._cache[cache_key] = (now, comparisons)
            return scope_comparisons(comparisons, event_id, market_type)