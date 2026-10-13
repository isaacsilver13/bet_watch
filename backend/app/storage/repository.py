from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import Event, EventComparison, MarketType, Offer, Sport
from app.storage.models import (
    EventRecord,
    OfferObservation,
    ProviderEventMapping,
    ProviderRefreshRun,
)


class OddsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def record_refresh_failure(
        self,
        provider: str,
        started_at: datetime,
        completed_at: datetime,
        error_type: str,
        error_message: str,
        sport: Optional[Sport] = None,
        quota_remaining: Optional[int] = None,
    ) -> int:
        async with self.session_factory() as session:
            async with session.begin():
                refresh = ProviderRefreshRun(
                    provider=provider,
                    sport=sport.value if sport else None,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    error_type=error_type,
                    error_message=error_message[:500],
                    quota_remaining=quota_remaining,
                )
                session.add(refresh)
                await session.flush()
                return refresh.id

    async def record_comparisons(
        self,
        provider: str,
        comparisons: Sequence[EventComparison],
        started_at: datetime,
        completed_at: datetime,
        sport: Optional[Sport] = None,
        quota_remaining: Optional[int] = None,
    ) -> int:
        async with self.session_factory() as session:
            async with session.begin():
                return await self._store(
                    session, provider, comparisons, started_at, completed_at, sport, quota_remaining
                )

    async def record_snapshot(
        self,
        provider: str,
        sport: Sport,
        comparisons: Sequence[EventComparison],
        observed_at: datetime,
        quota_remaining: Optional[int],
    ) -> bool:
        """Save one snapshot, once. Every row of a snapshot shares `observed_at`, so a second
        parse of the same raw file (a crash before the slot was updated) finds it and stops."""
        async with self.session_factory() as session:
            async with session.begin():
                already = await session.scalar(
                    select(OfferObservation.id)
                    .where(
                        OfferObservation.provider == provider,
                        OfferObservation.observed_at == observed_at,
                    )
                    .limit(1)
                )
                if already is not None:
                    return False
                await self._store(
                    session, provider, comparisons, observed_at, observed_at, sport, quota_remaining
                )
                return True

    async def latest_snapshot_at(self, provider: str) -> Optional[datetime]:
        async with self.session_factory() as session:
            return await session.scalar(
                select(func.max(OfferObservation.observed_at)).where(
                    OfferObservation.provider == provider
                )
            )

    async def latest_quota(self, provider: str) -> Optional[int]:
        """The provider's own remaining-credits figure from its newest response that had one."""
        async with self.session_factory() as session:
            return await session.scalar(
                select(ProviderRefreshRun.quota_remaining)
                .where(
                    ProviderRefreshRun.provider == provider,
                    ProviderRefreshRun.quota_remaining.is_not(None),
                )
                .order_by(ProviderRefreshRun.completed_at.desc(), ProviderRefreshRun.id.desc())
                .limit(1)
            )

    async def _store(
        self,
        session: AsyncSession,
        provider: str,
        comparisons: Sequence[EventComparison],
        started_at: datetime,
        completed_at: datetime,
        sport: Optional[Sport],
        quota_remaining: Optional[int],
    ) -> int:
        refresh = ProviderRefreshRun(
            provider=provider,
            sport=sport.value if sport else None,
            status="succeeded",
            started_at=started_at,
            completed_at=completed_at,
            quota_remaining=quota_remaining,
        )
        session.add(refresh)

        for comparison in comparisons:
            event = comparison.event
            existing_event = await session.get(EventRecord, event.id)
            if existing_event is None:
                session.add(
                    EventRecord(
                        id=event.id,
                        sport=event.sport.value,
                        league=event.league,
                        home_team=event.home_team,
                        away_team=event.away_team,
                        start_time=event.start_time,
                        first_observed_at=completed_at,
                        last_observed_at=completed_at,
                    )
                )
            else:
                existing_event.last_observed_at = completed_at

            mapping = await session.scalar(
                select(ProviderEventMapping).where(
                    ProviderEventMapping.provider == provider,
                    ProviderEventMapping.provider_event_id == event.id,
                )
            )
            if mapping is None:
                session.add(
                    ProviderEventMapping(
                        provider=provider,
                        provider_event_id=event.id,
                        event_id=event.id,
                        first_seen_at=completed_at,
                        last_seen_at=completed_at,
                    )
                )
            else:
                mapping.last_seen_at = completed_at

            for offer in comparison.offers:
                session.add(
                    OfferObservation(
                        event_id=event.id,
                        provider=offer.provider,
                        bookmaker=offer.bookmaker,
                        market_type=offer.market_type.value,
                        selection=offer.selection,
                        line=offer.line,
                        price_american=offer.price_american,
                        price_decimal=offer.price_decimal,
                        player_name=offer.player_name,
                        provider_offer_id=offer.provider_offer_id,
                        provider_updated_at=offer.provider_updated_at,
                        observed_at=offer.observed_at,
                        deep_link=offer.deep_link,
                        status=offer.status.value,
                    )
                )

        await session.flush()
        return refresh.id

    async def count_observations(self, event_id: Optional[str] = None) -> int:
        async with self.session_factory() as session:
            query = select(func.count(OfferObservation.id))
            if event_id is not None:
                query = query.where(OfferObservation.event_id == event_id)
            return int((await session.scalar(query)) or 0)

    async def count_refresh_runs(self, provider: Optional[str] = None) -> int:
        async with self.session_factory() as session:
            query = select(func.count(ProviderRefreshRun.id))
            if provider is not None:
                query = query.where(ProviderRefreshRun.provider == provider)
            return int((await session.scalar(query)) or 0)

    async def get_history(
        self,
        event_id: str,
        market_type: Optional[MarketType] = None,
        limit: int = 200,
    ) -> list[Offer]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        async with self.session_factory() as session:
            query = select(OfferObservation).where(OfferObservation.event_id == event_id)
            if market_type is not None:
                query = query.where(OfferObservation.market_type == market_type.value)
            query = query.order_by(OfferObservation.observed_at.desc()).limit(limit)
            rows = (await session.scalars(query)).all()
            return [
                Offer(
                    event_id=row.event_id,
                    provider=row.provider,
                    bookmaker=row.bookmaker,
                    market_type=MarketType(row.market_type),
                    selection=row.selection,
                    line=row.line,
                    price_american=row.price_american,
                    price_decimal=row.price_decimal,
                    player_name=row.player_name,
                    provider_offer_id=row.provider_offer_id,
                    provider_updated_at=row.provider_updated_at,
                    observed_at=row.observed_at,
                    deep_link=row.deep_link,
                    status=row.status,
                )
                for row in rows
            ]

    async def last_success_at(self, provider: str, sport: Optional[Sport]) -> Optional[datetime]:
        """When the provider last refreshed this sport successfully (None if never)."""
        async with self.session_factory() as session:
            sport_filter = (
                ProviderRefreshRun.sport == sport.value
                if sport
                else ProviderRefreshRun.sport.is_(None)
            )
            query = select(func.max(ProviderRefreshRun.completed_at)).where(
                ProviderRefreshRun.provider == provider,
                ProviderRefreshRun.status == "succeeded",
                sport_filter,
            )
            return await session.scalar(query)

    async def latest_comparisons(
        self, provider: str, sport: Optional[Sport]
    ) -> list[EventComparison]:
        """Rebuild the most recent saved snapshot of each event.

        Used to keep serving the last good odds when a live refresh fails and
        the in-memory cache is empty (e.g. right after a Fly machine wakes).
        """
        async with self.session_factory() as session:
            newest = (
                select(
                    OfferObservation.event_id.label("event_id"),
                    func.max(OfferObservation.observed_at).label("observed_at"),
                )
                .where(OfferObservation.provider == provider)
                .group_by(OfferObservation.event_id)
                .subquery()
            )
            query = (
                select(EventRecord, OfferObservation)
                .join(OfferObservation, OfferObservation.event_id == EventRecord.id)
                .join(
                    newest,
                    and_(
                        newest.c.event_id == OfferObservation.event_id,
                        newest.c.observed_at == OfferObservation.observed_at,
                    ),
                )
                .where(OfferObservation.provider == provider)
                .order_by(EventRecord.start_time, OfferObservation.id)
            )
            if sport is not None:
                query = query.where(EventRecord.sport == sport.value)
            rows = (await session.execute(query)).all()

        by_event: dict[str, EventComparison] = {}
        for event_row, row in rows:
            comparison = by_event.get(event_row.id)
            if comparison is None:
                comparison = EventComparison(
                    event=Event(
                        id=event_row.id,
                        sport=Sport(event_row.sport),
                        league=event_row.league,
                        home_team=event_row.home_team,
                        away_team=event_row.away_team,
                        start_time=event_row.start_time,
                    ),
                    offers=[],
                )
                by_event[event_row.id] = comparison
            comparison.offers.append(
                Offer(
                    event_id=row.event_id,
                    provider=row.provider,
                    bookmaker=row.bookmaker,
                    market_type=MarketType(row.market_type),
                    selection=row.selection,
                    line=row.line,
                    price_american=row.price_american,
                    price_decimal=row.price_decimal,
                    player_name=row.player_name,
                    provider_offer_id=row.provider_offer_id,
                    provider_updated_at=row.provider_updated_at,
                    observed_at=row.observed_at,
                    deep_link=row.deep_link,
                    status=row.status,
                )
            )
        return list(by_event.values())

    async def prune_expired(self, retention_days: int, now: datetime) -> int:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        cutoff = now - timedelta(days=retention_days)
        async with self.session_factory() as session:
            async with session.begin():
                observations = await session.execute(
                    delete(OfferObservation).where(OfferObservation.observed_at < cutoff)
                )
                await session.execute(
                    delete(ProviderRefreshRun).where(
                        ProviderRefreshRun.completed_at.is_not(None),
                        ProviderRefreshRun.completed_at < cutoff,
                    )
                )
                return int(observations.rowcount or 0)