"""Per-book offers for one game, read from the append-only odds observations."""

from collections.abc import Sequence
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.market import SideOffer
from app.domain.models import MarketType
from app.identity import normalize_name
from app.storage.games_repository import MAX_EVENT_GAP, alias_map, aware, related_event_ids
from app.storage.models import EventRecord, Game, OfferObservation

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
    return _side_offers(rows, game, await alias_map(session, game.sport))


def _side_offers(
    rows: Sequence[OfferObservation], game: Game, aliases: dict[str, int]
) -> list[SideOffer]:
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


async def side_offers_for_games(
    session: AsyncSession, games: Sequence[Game], since: Optional[datetime] = None
) -> dict[int, list[SideOffer]]:
    """`side_offers` for a whole slate in three queries instead of three per game. Offers are
    not cut at kickoff here: the caller decides, per game, what counts as pregame."""
    if not games:
        return {}
    aliases = await alias_map(session, games[0].sport)
    kickoffs = [aware(game.start_time) for game in games]
    events = (
        await session.scalars(
            select(EventRecord).where(
                EventRecord.sport == games[0].sport,
                EventRecord.start_time >= min(kickoffs) - MAX_EVENT_GAP,
                EventRecord.start_time <= max(kickoffs) + MAX_EVENT_GAP,
            )
        )
    ).all()
    event_ids: dict[int, set[str]] = {}
    for game, kickoff in zip(games, kickoffs):
        wanted = {game.home_team_id, game.away_team_id}

        def teams(event: EventRecord) -> set:
            return {aliases.get(normalize_name(n)) for n in (event.home_team, event.away_team)}

        event_ids[game.id] = {
            event.id for event in events
            if abs(aware(event.start_time) - kickoff) <= MAX_EVENT_GAP and teams(event) == wanted
        }
    every = set().union(*event_ids.values())
    if not every:
        return {game.id: [] for game in games}
    query = select(OfferObservation).where(
        OfferObservation.event_id.in_(every),
        OfferObservation.market_type.in_(CORE),
        OfferObservation.price_american.is_not(None),
    )
    if since is not None:
        query = query.where(OfferObservation.observed_at >= since)
    rows = (await session.scalars(query.order_by(OfferObservation.observed_at))).all()
    return {
        game.id: _side_offers([r for r in rows if r.event_id in event_ids[game.id]], game, aliases)
        for game in games
    }
