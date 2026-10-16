from datetime import date

import pytest
from app.analytics.fees import FeeRule, fee_for
from app.analytics.grading import grade, payout, profit

TODAY = date(2026, 10, 15)


def test_fanduel_charges_fifty_cents_until_the_stake_reaches_twenty_five() -> None:
    assert fee_for("fanduel", 10.0, TODAY) == 0.50
    assert fee_for("fanduel", 24.99, TODAY) == 0.50
    assert fee_for("fanduel", 25.0, TODAY) == 0.0  # waived at the threshold itself
    assert fee_for("fanduel", 100.0, TODAY) == 0.0


def test_draftkings_waives_its_fee_on_singles_of_fifty_or_more() -> None:
    assert fee_for("draftkings", 20.0, TODAY) == 0.50
    assert fee_for("draftkings", 49.99, TODAY) == 0.50
    assert fee_for("draftkings", 50.0, TODAY) == 0.0


def test_other_books_and_unknown_venues_charge_nothing() -> None:
    assert fee_for("betmgm", 5.0, TODAY) == 0.0
    assert fee_for("betrivers", 5.0, TODAY) == 0.0
    assert fee_for("espnbet", 5.0, TODAY) == 0.0
    assert fee_for("somewhere", 5.0, TODAY) == 0.0


def test_fee_rules_are_effective_dated_so_a_policy_change_never_rewrites_history() -> None:
    rules = (
        FeeRule("fanduel", 0.50, 25.0, date(2025, 7, 1)),
        FeeRule("fanduel", 0.0, None, date(2026, 1, 1)),  # the fee is dropped
        FeeRule("fanduel", 0.75, None, date(2026, 6, 1)),  # and brought back, higher
    )
    assert fee_for("fanduel", 10.0, date(2025, 6, 30), rules) == 0.0  # before any rule
    assert fee_for("fanduel", 10.0, date(2025, 12, 31), rules) == 0.50
    assert fee_for("fanduel", 10.0, date(2026, 1, 1), rules) == 0.0
    assert fee_for("fanduel", 100.0, date(2026, 6, 1), rules) == 0.75  # no waiver any more


@pytest.mark.parametrize(
    ("market", "selection", "line", "home", "away", "expected"),
    [
        ("spread", "home", -2.5, 24, 20, "won"),  # won by 4, laid 2.5
        ("spread", "home", -2.5, 23, 21, "lost"),  # won by 2, did not cover
        ("spread", "home", -3.0, 23, 20, "push"),  # won by exactly 3
        ("spread", "away", 2.5, 20, 22, "won"),  # away won outright
        ("spread", "away", 2.5, 24, 22, "won"),  # lost by 2, getting 2.5
        ("spread", "away", 2.5, 27, 22, "lost"),  # lost by 5
        ("total", "over", 44.5, 24, 21, "won"),  # 45 points
        ("total", "over", 44.5, 24, 20, "lost"),  # 44 points
        ("total", "under", 44.5, 24, 20, "won"),
        ("total", "over", 44.0, 24, 20, "push"),
        ("moneyline", "home", None, 24, 20, "won"),
        ("moneyline", "away", None, 24, 20, "lost"),
        ("moneyline", "away", None, 20, 20, "push"),  # a tie returns the stake
    ],
)
def test_grading_covers_each_market_including_pushes(market, selection, line, home, away, expected):
    assert grade(market, selection, line, home, away) == expected


def test_payouts_and_profit_after_fees() -> None:
    assert payout("won", 11.0, -110) == 21.0  # 11 stake + 10 profit
    assert payout("won", 10.0, 150) == 25.0
    assert payout("push", 10.0, -110) == 10.0
    assert payout("void", 10.0, -110) == 10.0
    assert payout("lost", 10.0, -110) == 0.0
    assert profit(21.0, 11.0, 0.50) == 9.50  # won: 10 profit less the 50 cent fee
    assert profit(0.0, 20.0, 0.50) == -20.50  # lost: the stake and the fee
    assert profit(10.0, 10.0, 0.50) == -0.50  # push: only the fee is lost
