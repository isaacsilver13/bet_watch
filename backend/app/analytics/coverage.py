"""Measure which books and markets a snapshot actually returned (never assume coverage)."""

from collections import defaultdict
from collections.abc import Sequence

from app.domain.models import EventComparison


def coverage_matrix(comparisons: Sequence[EventComparison]) -> dict[str, dict[str, int]]:
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for comparison in comparisons:
        for offer in comparison.offers:
            seen[offer.bookmaker][offer.market_type.value].add(comparison.event.id)
    return {
        book: {market: len(events) for market, events in markets.items()}
        for book, markets in seen.items()
    }
