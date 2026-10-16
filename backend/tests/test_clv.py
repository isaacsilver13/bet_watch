from datetime import datetime, timedelta, timezone

import pytest
from app.analytics.clv import Close, close_from_consensus, clv, pick_close, ratio_clv
from app.analytics.market import BookQuote, Consensus, consensus

KICKOFF = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)


def _consensus(spread=-3.5, total=46.0, cover=0.52, over=0.45, win=0.60) -> Consensus:
    return Consensus(spread_home=spread, total=total, home_cover_prob=cover, over_prob=over,
                     home_win_prob=win, books=5)


def test_line_clv_is_the_points_gained_over_the_closing_number() -> None:
    home = Close("odds_api", -3.5, 0.5)
    assert clv("spread", "home", -2.5, -110, home).points == 1.0  # laid a point less than the close
    away = Close("odds_api", 3.5, 0.5)
    assert clv("spread", "away", 2.5, -110, away).points == -1.0  # got a point less than the close
    assert clv("total", "over", 44.5, -110, Close("odds_api", 46.0, 0.5)).points == 1.5
    assert clv("total", "under", 44.5, -110, Close("odds_api", 46.0, 0.5)).points == -1.5
    assert clv("spread", "home", -3.5, -110, home).points == 0.0


def test_price_clv_is_the_no_vig_close_minus_the_price_you_took() -> None:
    result = clv("moneyline", "home", None, -105, Close("odds_api", None, 6 / 11))
    assert result.points is None
    assert result.close_price == -120  # the no-vig closing price on your side
    assert result.prob == pytest.approx(6 / 11 - 105 / 205)  # +3.3 percentage points
    assert ratio_clv(-105, -120) == pytest.approx(0.0649, abs=1e-4)  # 6.5% better than break-even


def test_a_close_without_prices_still_gives_line_clv() -> None:
    result = clv("spread", "home", -2.5, -110, Close("espn", -3.0, None))
    assert (result.points, result.prob, result.close_price) == (0.5, None, None)


def test_the_close_is_read_from_the_consensus_for_the_bets_side() -> None:
    c = _consensus()
    home = close_from_consensus(c, "spread", "home", "odds_api")
    away = close_from_consensus(c, "spread", "away", "odds_api")
    assert (home.line, home.prob) == (-3.5, 0.52)
    assert (away.line, away.prob) == (3.5, pytest.approx(0.48))
    over = close_from_consensus(c, "total", "over", "odds_api")
    under = close_from_consensus(c, "total", "under", "odds_api")
    assert (over.line, over.prob) == (46.0, 0.45) and under.prob == pytest.approx(0.55)
    ml = close_from_consensus(c, "moneyline", "away", "odds_api")
    assert (ml.line, ml.prob) == (None, pytest.approx(0.40))
    assert close_from_consensus(_consensus(spread=None), "spread", "home", "x") is None


def _espn_quote() -> BookQuote:
    return BookQuote("espn", KICKOFF, spread_home=-3.0, spread_home_price=-110,
                     spread_away_price=-110, total=45.5, over_price=-110, under_price=-110)


def test_the_pre_kick_snapshot_is_the_close_when_it_is_fresh_enough() -> None:
    snapshot = (KICKOFF - timedelta(minutes=20), _consensus())
    close = pick_close(snapshot, KICKOFF, _espn_quote(), "spread", "home")
    assert (close.source, close.line) == ("odds_api", -3.5)


def test_a_stale_or_late_snapshot_falls_back_to_espns_close() -> None:
    morning = (KICKOFF - timedelta(hours=3), _consensus())  # not a close: too early
    close = pick_close(morning, KICKOFF, _espn_quote(), "spread", "home")
    assert (close.source, close.line, close.prob) == ("espn", -3.0, 0.5)
    after = (KICKOFF + timedelta(minutes=5), _consensus())  # in-play prices are not a close
    assert pick_close(after, KICKOFF, _espn_quote(), "total", "over").source == "espn"
    assert pick_close(None, KICKOFF, None, "spread", "home") is None
    assert consensus([_espn_quote()]).home_cover_prob == 0.5
