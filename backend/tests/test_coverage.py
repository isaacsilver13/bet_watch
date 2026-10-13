import asyncio
from datetime import datetime, timezone

from app.analytics.coverage import coverage_matrix, snapshot_coverage
from app.domain.models import Sport
from app.providers.fixture import FixtureOddsProvider
from app.providers.the_odds_api.adapter import TheOddsApiProvider

from tests.matchup_helpers import load_json


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


def _snapshot(keep_books=5, drop_markets=()):
    payload = load_json("the_odds_api/nfl_week6.json")
    event = payload[0]
    event["bookmakers"] = event["bookmakers"][:keep_books]
    for book in event["bookmakers"]:
        book["markets"] = [m for m in book["markets"] if m["key"] not in drop_markets]
    taken = datetime(2026, 10, 14, tzinfo=timezone.utc)
    return TheOddsApiProvider.normalize_events(payload, Sport.NFL, taken)


EVENT = "the_odds_api:odds-den-sea"


def test_a_game_is_complete_with_all_three_markets_from_three_books() -> None:
    result = snapshot_coverage(_snapshot(), [(7, EVENT)])
    assert (result["targeted"], result["complete"], result["missing"]) == (1, 1, [])
    assert result["books"]["DraftKings"] == {"moneyline": 1, "spread": 1, "total": 1}
    assert result["events"] == 1


def test_two_books_is_not_enough_and_the_report_says_which_markets_are_short() -> None:
    result = snapshot_coverage(_snapshot(keep_books=2), [(7, EVENT)])
    assert result["complete"] == 0
    assert result["missing"] == [
        {"game_id": 7, "event_id": EVENT, "books": {"moneyline": 2, "spread": 2, "total": 2}}
    ]


def test_a_missing_market_or_an_unlinked_game_is_incomplete() -> None:
    no_totals = snapshot_coverage(_snapshot(drop_markets=("totals",)), [(7, EVENT)])
    assert no_totals["missing"][0]["books"] == {"moneyline": 5, "spread": 5, "total": 0}
    unlinked = snapshot_coverage(_snapshot(), [(7, EVENT), (8, None)])
    assert (unlinked["targeted"], unlinked["complete"]) == (2, 1)
    assert unlinked["missing"] == [
        {"game_id": 8, "event_id": None, "books": {"moneyline": 0, "spread": 0, "total": 0}}
    ]
