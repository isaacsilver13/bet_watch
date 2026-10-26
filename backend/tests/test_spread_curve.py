"""Synthetic checks for alternate-threshold math; no historical edge is inferred."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from app.analytics.market import SideOffer
from app.analytics.spread_curve import (
    PairedSpread,
    leave_one_book_out_gap,
    monotone_curve,
    net_spread_ev,
    paired_spreads,
    spread_mass,
)

T0 = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


def offer(book: str, side: str, line: float, price: int, at: datetime = T0) -> SideOffer:
    return SideOffer(book, "spread", side, line, price, at, at)


def test_integer_scoring_gives_half_points_no_push_and_whole_points_push_mass() -> None:
    half = spread_mass(0, 12, -3.5)
    whole = spread_mass(0, 12, -3)
    assert half.push == 0
    assert sum((half.home_cover, half.away_cover, half.push)) == pytest.approx(1)
    assert whole.push > 0
    assert sum((whole.home_cover, whole.away_cover, whole.push)) == pytest.approx(1)
    even = spread_mass(0, 12, 0)
    assert even.home_cover == pytest.approx(even.away_cover)
    assert net_spread_ev(0.45, 0.45, 100, 1, 100) == pytest.approx(-0.01)


def test_same_win_chance_can_have_different_cover_chances_at_an_alt_line() -> None:
    low_variance = spread_mass(3, 10, 0)
    high_variance = spread_mass(6, 20, 0)
    assert low_variance.home_cover == pytest.approx(high_variance.home_cover, abs=0.02)
    assert spread_mass(3, 10, -7).home_cover < spread_mass(6, 20, -7).home_cover
    with pytest.raises(ValueError):
        spread_mass(3, 0, -7)


def test_pairs_require_same_book_time_mirrored_line_and_freshness() -> None:
    offers = [
        offer("valid", "home", -2.5, -110), offer("valid", "away", 2.5, -110),
        offer("async", "home", -2.5, -110),
        offer("async", "away", 2.5, -110, T0 + timedelta(seconds=1)),
        offer("wrong-line", "home", -2.5, -110), offer("wrong-line", "away", 3.5, -110),
        offer("old", "home", -2.5, -110, T0 - timedelta(minutes=20)),
        offer("old", "away", 2.5, -110, T0 - timedelta(minutes=20)),
        offer("future", "home", -2.5, -110, T0 + timedelta(minutes=6)),
        offer("future", "away", 2.5, -110, T0 + timedelta(minutes=6)),
        replace(offer("stale-market", "home", -2.5, -110),
                provider_updated_at=T0 - timedelta(minutes=20)),
        replace(offer("stale-market", "away", 2.5, -110),
                provider_updated_at=T0 - timedelta(minutes=20)),
        replace(offer("unknown-market", "home", -2.5, -110), provider_updated_at=None),
        replace(offer("unknown-market", "away", 2.5, -110), provider_updated_at=None),
        replace(offer("different-updates", "home", -2.5, -110),
                provider_updated_at=T0 - timedelta(seconds=1)),
        offer("different-updates", "away", 2.5, -110),
    ]
    pairs = paired_spreads(offers, T0 + timedelta(minutes=5))
    assert [(pair.book, pair.spread_home) for pair in pairs] == [("valid", -2.5)]


def test_curve_is_monotone_and_candidate_is_compared_without_its_book() -> None:
    pairs = [
        PairedSpread("left", T0, -2, -150, 130, T0),
        PairedSpread("middle", T0, 0, -110, -110, T0),
        PairedSpread("right", T0, 2, -200, 160, T0),
    ]
    curve = monotone_curve(pairs)
    assert [point.spread_home for point in curve] == [-2, 0, 2]
    assert curve[0].home_prob_novig == pytest.approx(curve[1].home_prob_novig)
    assert curve[1].home_prob_novig <= curve[2].home_prob_novig
    candidate = PairedSpread("candidate", T0, 0, -250, 180, T0)
    assert leave_one_book_out_gap(candidate, [*pairs, candidate]) > 0.07
    assert leave_one_book_out_gap(candidate, [candidate, pairs[0]]) is None


def test_books_disagreeing_at_one_line_contribute_one_median_market_point() -> None:
    pairs = [
        PairedSpread("a", T0, 1.5, -110, -110, T0),
        PairedSpread("b", T0, 1.5, -120, 100, T0),
        PairedSpread("c", T0, 1.5, 100, -120, T0),
    ]
    point, = monotone_curve(pairs)
    assert point.books == 3
    assert point.home_prob_novig == pytest.approx(0.5)
