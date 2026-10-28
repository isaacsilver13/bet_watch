"""SportsGameOdds `/v2/events` pages -> the canonical contract. Only full-game moneyline, spread
and total are read (what the Odds API snapshot supplies); props and period markets are skipped."""

from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.models import Event, EventComparison, MarketType, Offer, OfferStatus, Sport
from app.providers.the_odds_api.adapter import american_to_decimal

NAME = "sgo"

# SGO bookmakerID -> the title `domain.venues.venue_for_book` knows. Only the Illinois books the
# board tracks; Caesars and the offshore books are left out, like ODDS_BOOKMAKERS does.
BOOK_TITLES = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "espnbet": "ESPN BET",
    "thescore": "ESPN BET",
}
_MARKETS = {"ml": MarketType.MONEYLINE, "sp": MarketType.SPREAD, "ou": MarketType.TOTAL}
_HEADS = ("points-home", "points-away", "points-all")


def normalize_events(
    pages: list[dict[str, Any]], sport: Sport, observed_at: Optional[datetime] = None
) -> list[EventComparison]:
    """`pages` is the saved response (one dict per page). `observed_at` is the snapshot time, so
    re-parsing a saved file stamps rows exactly as the first parse did."""
    observed_at = observed_at or datetime.now(timezone.utc)
    comparisons = []
    for page in pages:
        for raw in page.get("data") or []:
            comparison = _event(raw, sport, observed_at)
            if comparison is not None:
                comparisons.append(comparison)
    return comparisons


def _event(raw: dict[str, Any], sport: Sport, observed_at: datetime) -> Optional[EventComparison]:
    teams = raw.get("teams") or {}
    home, away = _team(teams, "home"), _team(teams, "away")
    starts_at = (raw.get("status") or {}).get("startsAt")
    if not (raw.get("eventID") and home and away and starts_at):
        return None
    try:
        event = Event(
            id=f"{NAME}:{raw['eventID']}",
            sport=sport,
            league=raw.get("leagueID") or sport.value.upper(),
            home_team=home,
            away_team=away,
            start_time=starts_at,
        )
    except ValueError:  # an unparseable start time
        return None
    offers: list[Offer] = []
    for odd_id, odd in (raw.get("odds") or {}).items():
        offers.extend(_offers(raw["eventID"], event.id, odd_id, odd or {}, home, away, observed_at))
    return EventComparison(event=event, offers=offers)


def _team(teams: dict[str, Any], side: str) -> Optional[str]:
    return ((teams.get(side) or {}).get("names") or {}).get("long") or None


def _offers(
    event_key: str,
    event_id: str,
    odd_id: str,
    odd: dict[str, Any],
    home: str,
    away: str,
    observed_at: datetime,
) -> list[Offer]:
    """oddID is `{stat}-{entity}-{period}-{betType}-{side}`: keep points / home|away|all / game."""
    try:
        head, period, bet, side = odd_id.rsplit("-", 3)
    except ValueError:
        return []
    market_type = _MARKETS.get(bet)
    selection = {"home": home, "away": away, "over": "Over", "under": "Under"}.get(side)
    if market_type is None or selection is None or period != "game" or head not in _HEADS:
        return []
    offers = []
    for book, quote in (odd.get("byBookmaker") or {}).items():
        title = BOOK_TITLES.get(book)
        quote = quote or {}
        if title is None or quote.get("available") is False:
            continue
        price = _number(quote.get("odds"))
        line = {"sp": _number(quote.get("spread")), "ou": _number(quote.get("overUnder"))}.get(bet)
        if price is None or not 100 <= abs(price) <= 10000 or (bet != "ml" and line is None):
            continue
        offers.append(
            Offer(
                event_id=event_id,
                provider=NAME,
                bookmaker=title,
                market_type=market_type,
                selection=selection,
                line=line,
                price_american=int(price),
                price_decimal=american_to_decimal(int(price)),
                provider_offer_id=f"{event_key}:{book}:{odd_id}",
                provider_updated_at=quote.get("lastUpdatedAt"),
                observed_at=observed_at,
                deep_link=_link(quote.get("deeplink")),
                status=OfferStatus.AVAILABLE,
            )
        )
    return offers


def _link(value: Any) -> Optional[str]:
    """The feed's link is rendered as an href, so only a plain http(s) URL that fits the column."""
    ok = isinstance(value, str) and value.lower().startswith(("https://", "http://"))
    return value if ok and len(value) <= 500 else None


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
