"""Per-book quotes for one game, the consensus across books, and the best price on each side."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Optional

from app.analytics.pricing import implied_prob, novig


@dataclass(frozen=True)
class SideOffer:
    """One outcome a book offered. `line` is that side's own number: home -2.5 and away +2.5."""

    book: str
    market: str  # spread | total | moneyline
    side: str  # home | away | over | under
    line: Optional[float]
    price: int
    observed_at: datetime
    provider_updated_at: Optional[datetime] = None
    observation_id: Optional[int] = None


@dataclass(frozen=True)
class BookQuote:
    book: str
    observed_at: datetime
    spread_home: Optional[float] = None
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    ml_home: Optional[int] = None
    ml_away: Optional[int] = None


@dataclass(frozen=True)
class Consensus:
    spread_home: Optional[float]
    total: Optional[float]
    home_cover_prob: Optional[float]  # no-vig, at spread_home
    over_prob: Optional[float]  # no-vig, at total
    home_win_prob: Optional[float]  # no-vig moneyline
    books: int


@dataclass(frozen=True)
class BestPrice:
    book: str
    price: int
    line: Optional[float]


def book_quotes(offers: Iterable[SideOffer]) -> list[BookQuote]:
    """Each book's newest offer per market side, folded into one quote per book."""
    newest: dict[tuple[str, str, str], SideOffer] = {}
    for offer in offers:
        key = (offer.book, offer.market, offer.side)
        if key not in newest or offer.observed_at >= newest[key].observed_at:
            newest[key] = offer
    by_book: dict[str, dict[tuple[str, str], SideOffer]] = defaultdict(dict)
    for (book, market, side), offer in newest.items():
        by_book[book][(market, side)] = offer
    quotes = []
    for book, sides in sorted(by_book.items()):
        home, away = sides.get(("spread", "home")), sides.get(("spread", "away"))
        over, under = sides.get(("total", "over")), sides.get(("total", "under"))
        total_line = next((o.line for o in (over, under) if o and o.line is not None), None)
        spread_home = None
        if home and home.line is not None:
            spread_home = home.line
        elif away and away.line is not None:
            spread_home = -away.line
        ml_home, ml_away = sides.get(("moneyline", "home")), sides.get(("moneyline", "away"))
        quotes.append(
            BookQuote(
                book=book,
                observed_at=max(o.observed_at for o in sides.values()),
                spread_home=spread_home,
                spread_home_price=home.price if home else None,
                spread_away_price=away.price if away else None,
                total=total_line,
                over_price=over.price if over else None,
                under_price=under.price if under else None,
                ml_home=ml_home.price if ml_home else None,
                ml_away=ml_away.price if ml_away else None,
            )
        )
    return quotes


def fresh_paired_main_offers(
    offers: Iterable[SideOffer], as_of: datetime,
    max_age: timedelta = timedelta(hours=1),
) -> list[SideOffer]:
    """Newest complete two-sided main markets from one capture and provider update.

    A newer incomplete capture never falls back to an older one. A book can
    supply an ML and spread pair with different market update times, but the
    two outcomes within either pair must be synchronized. Unknown provider
    update times are ineligible for a priced model decision.
    """
    if max_age < timedelta(0):
        raise ValueError("max_age must be nonnegative")
    found = [o for o in offers if o.market in {"moneyline", "spread"}
             and o.observed_at <= as_of]
    if not found:
        return []
    latest_capture = max(o.observed_at for o in found)
    if as_of - latest_capture > max_age:
        return []
    grouped: dict[
        tuple[str, str, datetime, Optional[float]], dict[str, SideOffer]
    ] = defaultdict(dict)
    for offer in found:
        if (offer.observed_at != latest_capture or offer.provider_updated_at is None
                or offer.provider_updated_at > as_of
                or as_of - offer.provider_updated_at > max_age):
            continue
        if offer.market == "moneyline":
            if offer.line is not None:
                continue
            line = None
        else:
            if offer.line is None:
                continue
            line = offer.line if offer.side == "home" else -offer.line
        if offer.side in {"home", "away"}:
            grouped[(offer.book, offer.market, offer.provider_updated_at, line)][offer.side] = offer
    newest: dict[tuple[str, str], tuple[SideOffer, SideOffer]] = {}
    for (book, market, updated_at, _), sides in grouped.items():
        if "home" not in sides or "away" not in sides:
            continue
        key = (book, market)
        if key not in newest or updated_at > newest[key][0].provider_updated_at:
            newest[key] = (sides["home"], sides["away"])
    return [offer for key in sorted(newest) for offer in newest[key]]


def _modal(values: Sequence[float]) -> Optional[float]:
    """The line most books agree on; a tie goes to the one nearest the middle, then the lower."""
    if not values:
        return None
    counts = Counter(values)
    middle = median(values)
    return min(counts, key=lambda value: (-counts[value], abs(value - middle), value))


def _no_vig_first(prices: Iterable[tuple[Optional[int], Optional[int]]]) -> Optional[float]:
    shares = [
        novig(implied_prob(first), implied_prob(second))[0]
        for first, second in prices
        if first is not None and second is not None
    ]
    return mean(shares) if shares else None


def consensus(quotes: Sequence[BookQuote]) -> Consensus:
    spread = _modal([q.spread_home for q in quotes if q.spread_home is not None])
    total = _modal([q.total for q in quotes if q.total is not None])
    return Consensus(
        spread_home=spread,
        total=total,
        home_cover_prob=_no_vig_first(
            (q.spread_home_price, q.spread_away_price) for q in quotes if q.spread_home == spread
        ),
        over_prob=_no_vig_first((q.over_price, q.under_price) for q in quotes if q.total == total),
        home_win_prob=_no_vig_first((q.ml_home, q.ml_away) for q in quotes),
        books=len(quotes),
    )


def best_prices(quotes: Sequence[BookQuote], reference: Consensus) -> dict[str, BestPrice]:
    """Highest price per side. Spread and total sides only compare books at the consensus line."""
    at_spread = [q for q in quotes if q.spread_home == reference.spread_home]
    at_total = [q for q in quotes if q.total == reference.total]
    # side -> (books to compare, price field, line field, line sign for that side)
    sides = {
        "spread_home": (at_spread, "spread_home_price", "spread_home", 1),
        "spread_away": (at_spread, "spread_away_price", "spread_home", -1),
        "over": (at_total, "over_price", "total", 1),
        "under": (at_total, "under_price", "total", 1),
        "ml_home": (list(quotes), "ml_home", None, 1),
        "ml_away": (list(quotes), "ml_away", None, 1),
    }
    best: dict[str, BestPrice] = {}
    for name, (pool, price_field, line_field, sign) in sides.items():
        priced = [q for q in pool if getattr(q, price_field) is not None]
        if not priced:
            continue
        top = max(priced, key=lambda q: getattr(q, price_field))
        line = getattr(top, line_field) if line_field else None
        best[name] = BestPrice(
            top.book, getattr(top, price_field), None if line is None else sign * line
        )
    return best


def coverage(quotes: Sequence[BookQuote], reference: Consensus) -> dict[str, tuple[int, int]]:
    """Per market: books quoting it, and books quoting it at the consensus line."""
    spread = [q.spread_home for q in quotes if q.spread_home is not None]
    total = [q.total for q in quotes if q.total is not None]
    both = sum(q.ml_home is not None and q.ml_away is not None for q in quotes)
    return {
        "spread": (len(spread), spread.count(reference.spread_home)),
        "total": (len(total), total.count(reference.total)),
        "moneyline": (both, both),  # a moneyline has no number to differ on
    }


def consensus_history(offers: Iterable[SideOffer]) -> list[tuple[datetime, Consensus]]:
    """One consensus per snapshot, oldest first, for the line-movement chart."""
    by_snapshot: dict[datetime, list[SideOffer]] = defaultdict(list)
    for offer in offers:
        by_snapshot[offer.observed_at].append(offer)
    return [(when, consensus(book_quotes(group))) for when, group in sorted(by_snapshot.items())]
