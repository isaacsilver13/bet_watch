import pytest
from app.analytics.bet_stats import BetFacts, summarize


def fact(venue, market, status, stake, fee, payout, points=None, prob=None, sport="nfl"):
    return BetFacts(venue, sport, market, status, stake, fee, payout, points, prob)


FACTS = [
    fact("fanduel", "spread", "won", 11.0, 0.5, 21.0, points=1.0, prob=0.02),  # +9.50
    fact("fanduel", "total", "lost", 20.0, 0.5, 0.0, points=-0.5, prob=-0.01),  # -20.50
    fact("draftkings", "moneyline", "push", 10.0, 0.5, 10.0, prob=0.03),  # -0.50
    fact("betmgm", "spread", "open", 25.0, 0.0, None),
    fact("betrivers", "total", "void", 10.0, 0.0, 10.0),
]


def test_totals_count_only_graded_bets_in_roi_and_every_bet_with_a_close_in_clv() -> None:
    total, _ = summarize(FACTS, "venue")
    assert (total.bets, total.open, total.settled) == (5, 1, 3)
    assert (total.staked, total.fees, total.profit) == (41.0, 1.5, -11.5)
    assert total.roi == pytest.approx(-11.5 / 41.0)
    assert total.avg_clv_points == pytest.approx(0.25)  # two bets had a line to compare
    assert total.avg_clv_prob == pytest.approx((0.02 - 0.01 + 0.03) / 3)
    assert total.clv_samples == 3
    assert total.beat_close_pct == pytest.approx(200 / 3)  # the +1.0 and the moneyline's +3 pts


def test_groups_split_by_venue_sport_or_market_and_sort_by_key() -> None:
    _, groups = summarize(FACTS, "venue")
    assert [g.key for g in groups] == ["betmgm", "betrivers", "draftkings", "fanduel"]
    fanduel = next(g for g in groups if g.key == "fanduel")
    assert (fanduel.bets, fanduel.settled, fanduel.staked, fanduel.fees) == (2, 2, 31.0, 1.0)
    assert fanduel.profit == -11.0 and fanduel.roi == pytest.approx(-11.0 / 31.0)
    assert fanduel.avg_clv_points == pytest.approx(0.25) and fanduel.beat_close_pct == 50.0
    assert [g.key for g in summarize(FACTS, "market")[1]] == ["moneyline", "spread", "total"]
    assert [g.key for g in summarize(FACTS, "sport")[1]] == ["nfl"]


def test_a_group_with_nothing_graded_or_measured_has_no_roi_or_clv() -> None:
    _, groups = summarize(FACTS, "venue")
    open_only = next(g for g in groups if g.key == "betmgm")
    assert (open_only.roi, open_only.avg_clv_points, open_only.avg_clv_prob) == (None, None, None)
    assert (open_only.beat_close_pct, open_only.clv_samples, open_only.open) == (None, 0, 1)
    void = next(g for g in groups if g.key == "betrivers")
    assert (void.bets, void.settled, void.staked, void.profit) == (1, 0, 0.0, 0.0)
    empty, none = summarize([], "venue")
    assert (empty.bets, empty.roi, none) == (0, None, [])
