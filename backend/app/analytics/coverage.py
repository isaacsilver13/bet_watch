"""Measure which books and markets a snapshot actually returned (never assume coverage)."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Optional

from app.domain.models import EventComparison, MarketType

CORE_MARKETS = (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)
MIN_BOOKS = 3  # a game is only "covered" when this many books price every core market


def coverage_matrix(comparisons: Sequence[EventComparison]) -> dict[str, dict[str, int]]:
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for comparison in comparisons:
        for offer in comparison.offers:
            seen[offer.bookmaker][offer.market_type.value].add(comparison.event.id)
    return {
        book: {market: len(events) for market, events in markets.items()}
        for book, markets in seen.items()
    }


def snapshot_coverage(
    comparisons: Sequence[EventComparison],
    targets: Sequence[tuple[int, Optional[str]]],
    min_books: int = MIN_BOOKS,
) -> dict:
    """How well a snapshot covered the games it was for: `targets` is (game id, linked event id).

    A game is complete only if all three core markets came back from at least `min_books` books.
    """
    books: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for comparison in comparisons:
        for offer in comparison.offers:
            if offer.market_type in CORE_MARKETS:
                books[comparison.event.id][offer.market_type.value].add(offer.bookmaker)
    missing = []
    for game_id, event_id in targets:
        by_market = books.get(event_id or "", {})
        counts = {m.value: len(by_market.get(m.value, ())) for m in CORE_MARKETS}
        if min(counts.values()) < min_books:
            missing.append({"game_id": game_id, "event_id": event_id, "books": counts})
    return {
        "events": len(comparisons),
        "books": coverage_matrix(comparisons),
        "targeted": len(targets),
        "complete": len(targets) - len(missing),
        "missing": missing,
    }
