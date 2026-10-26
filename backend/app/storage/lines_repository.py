"""Per-book offers for one game, read from the append-only odds observations."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.market import SideOffer
from app.domain.models import MarketType
from app.identity import normalize_name
from app.storage.games_repository import alias_map, aware, related_event_ids
from app.storage.models import Game, OfferObservation

CORE = (MarketType.MONEYLINE.value, MarketType.SPREAD.value, MarketType.TOTAL.value)


async def side_offers(
    session: AsyncSession,
    game: Game,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> list[SideOffer]:
    """Moneyline, spread and total offers for this game, across every odds event it has had
    (a flexed game has two), as home/away/over/under sides. Oldest first."""
    event_ids = await related_event_ids(session, game)
    if not event_ids:
        return []
    query = select(OfferObservation).where(
        OfferObservation.event_id.in_(event_ids),
        OfferObservation.market_type.in_(CORE),
        OfferObservation.price_american.is_not(None),
    )
    if since is not None:
        query = query.where(OfferObservation.observed_at >= since)
    if until is not None:
        query = query.where(OfferObservation.observed_at <= until)
    rows = (await session.scalars(query.order_by(OfferObservation.observed_at))).all()
    aliases = await alias_map(session, game.sport)
    team_side = {game.home_team_id: "home", game.away_team_id: "away"}
    offers = []
    for row in rows:
        if row.market_type == MarketType.TOTAL.value:
            side = {"over": "over", "under": "under"}.get(row.selection.strip().lower())
        else:
            side = team_side.get(aliases.get(normalize_name(row.selection), -1))
        if side is None:
            continue
        offers.append(
            SideOffer(
                book=row.bookmaker,
                market=row.market_type,
                side=side,
                line=row.line,
                price=row.price_american,
                observed_at=aware(row.observed_at),
                provider_updated_at=(aware(row.provider_updated_at)
                                     if row.provider_updated_at is not None else None),
                observation_id=row.id,
            )
        )
    return offers
