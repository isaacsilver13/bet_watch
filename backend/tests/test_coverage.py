import asyncio

from app.analytics.coverage import coverage_matrix
from app.providers.fixture import FixtureOddsProvider


def _comparisons():
    return list(asyncio.run(FixtureOddsProvider().get_comparisons()))


def test_matrix_lists_every_book_with_event_counts() -> None:
    comparisons = _comparisons()
    matrix = coverage_matrix(comparisons)
    books = {offer.bookmaker for c in comparisons for offer in c.offers}
    assert set(matrix) == books
    counts = [n for markets in matrix.values() for n in markets.values()]
    assert counts and all(1 <= n <= len(comparisons) for n in counts)


def test_same_event_counted_once() -> None:
    comparisons = _comparisons()
    assert coverage_matrix(comparisons + comparisons) == coverage_matrix(comparisons)


def test_empty_input() -> None:
    assert coverage_matrix([]) == {}
