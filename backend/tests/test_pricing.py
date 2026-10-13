import pytest
from app.analytics.pricing import decimal_odds, fair_american, implied_prob, novig


def test_implied_probability_of_american_prices() -> None:
    assert implied_prob(-110) == pytest.approx(110 / 210)
    assert implied_prob(105) == pytest.approx(100 / 205)
    assert implied_prob(-105) == pytest.approx(105 / 205)
    assert implied_prob(100) == 0.5 and implied_prob(-100) == 0.5


def test_removing_the_vig_makes_a_pair_sum_to_one() -> None:
    assert novig(implied_prob(-110), implied_prob(-110)) == pytest.approx((0.5, 0.5))
    home, away = novig(implied_prob(102), implied_prob(-122))
    assert (home, away) == pytest.approx((0.4739, 0.5261), abs=1e-4)
    assert home + away == pytest.approx(1.0)


def test_fair_price_is_the_inverse_of_implied_probability() -> None:
    assert fair_american(0.6) == -150
    assert fair_american(0.4) == 150
    assert fair_american(0.5) == -100
    assert fair_american(6 / 11) == -120
    assert implied_prob(fair_american(0.5455)) == pytest.approx(0.5455, abs=0.002)


def test_decimal_odds() -> None:
    assert decimal_odds(-110) == pytest.approx(1.9091, abs=1e-4)
    assert decimal_odds(150) == 2.5
