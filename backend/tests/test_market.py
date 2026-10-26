from datetime import datetime, timedelta, timezone

import pytest
from app.analytics.market import (
    BookQuote,
    SideOffer,
    best_prices,
    book_quotes,
    consensus,
    consensus_history,
    fresh_paired_main_offers,
)

T0 = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def quote(book, spread=None, sp=(None, None), total=None, tp=(None, None), ml=(None, None)):
    return BookQuote(
        book=book, observed_at=T0, spread_home=spread, spread_home_price=sp[0],
        spread_away_price=sp[1], total=total, over_price=tp[0], under_price=tp[1],
        ml_home=ml[0], ml_away=ml[1],
    )


QUOTES = [
    quote("dk", 1.5, (-110, -110), 42.5, (-110, -110), (102, -122)),
    quote("fd", 1.5, (-108, -112), 42.5, (-112, -108), (105, -125)),
    quote("mgm", 2.0, (-110, -110), 43.0, (-110, -110), (110, -130)),
    quote("rivers", 1.5, (-105, -115), 42.5, (-105, -115), (100, -120)),
]


def test_consensus_uses_the_most_common_line_and_only_books_quoting_it() -> None:
    result = consensus(QUOTES)
    assert (result.spread_home, result.total, result.books) == (1.5, 42.5, 4)
    # dk .5, fd .49568, rivers .48916 -> home cover probability at +1.5 without vig
    assert result.home_cover_prob == pytest.approx((0.5 + 0.49568 + 0.48916) / 3, abs=1e-4)
    assert result.over_prob == pytest.approx((0.5 + 0.50432 + 0.48916) / 3, abs=1e-4)
    assert 0.46 < result.home_win_prob < 0.49


def test_a_line_tie_goes_to_the_value_nearest_the_middle() -> None:
    result = consensus([quote("a", 1.5), quote("b", 1.5), quote("c", 2.5), quote("d", 2.5),
                        quote("e", 2.0)])
    assert result.spread_home in (1.5, 2.5)  # tied at two books; nearest the median (2.0) wins
    assert consensus([quote("a", 1.5), quote("b", 2.5)]).spread_home == 1.5  # exact tie: lower


def test_consensus_of_nothing_is_empty() -> None:
    result = consensus([])
    assert (result.spread_home, result.total, result.home_cover_prob, result.books) == (
        None, None, None, 0)


def test_best_prices_compare_only_books_at_the_consensus_line() -> None:
    best = best_prices(QUOTES, consensus(QUOTES))
    assert best["spread_home"].price == -105 and best["spread_home"].book == "rivers"
    assert best["spread_home"].line == 1.5 and best["spread_away"].line == -1.5
    # the +2 book (mgm) posts -110 too, but it is a different number, so it never wins here
    assert best["spread_away"].price == -110 and best["spread_away"].book == "dk"
    assert best["over"].price == -105 and best["under"].price == -108
    assert best["ml_home"].price == 110 and best["ml_home"].book == "mgm"  # moneyline has no line
    assert best["ml_away"].price == -120


def test_book_quotes_keep_the_newest_offer_per_side() -> None:
    later = T0 + timedelta(hours=1)
    offers = [
        SideOffer("dk", "spread", "home", 1.5, -110, T0),
        SideOffer("dk", "spread", "away", -1.5, -110, T0),
        SideOffer("dk", "spread", "home", 2.0, -115, later),
        SideOffer("dk", "total", "over", 42.5, -108, T0),
        SideOffer("dk", "moneyline", "away", None, -122, T0),
        SideOffer("fd", "spread", "away", -2.5, -110, T0),  # only the away side was posted
    ]
    quotes = {q.book: q for q in book_quotes(offers)}
    assert (quotes["dk"].spread_home, quotes["dk"].spread_home_price) == (2.0, -115)
    assert quotes["dk"].spread_away_price == -110
    assert (quotes["dk"].total, quotes["dk"].over_price, quotes["dk"].under_price) == (
        42.5, -108, None)
    assert (quotes["dk"].ml_home, quotes["dk"].ml_away) == (None, -122)
    assert quotes["dk"].observed_at == later
    assert quotes["fd"].spread_home == 2.5 and quotes["fd"].spread_away_price == -110


def test_model_input_uses_only_fresh_synchronized_two_sided_quotes() -> None:
    now = T0 + timedelta(minutes=30)

    def offer(book, market, side, line, at=T0, updated=T0):
        return SideOffer(book, market, side, line, -110, at, updated)

    valid = [offer("fd", "moneyline", "home", None),
             offer("fd", "moneyline", "away", None)]
    mismatched_capture = [offer("dk", "moneyline", "home", None),
                          offer("dk", "moneyline", "away", None,
                                at=T0 - timedelta(minutes=1),
                                updated=T0 - timedelta(minutes=1))]
    mismatched_update = [offer("mgm", "spread", "home", -3.5),
                         offer("mgm", "spread", "away", 3.5,
                               updated=T0 - timedelta(minutes=1))]
    unmirrored = [offer("rivers", "spread", "home", -3.5),
                  offer("rivers", "spread", "away", 4.5)]
    unknown_update = [offer("espn", "moneyline", "home", None, updated=None),
                      offer("espn", "moneyline", "away", None, updated=None)]
    filtered = fresh_paired_main_offers(valid + mismatched_capture + mismatched_update
                                        + unmirrored + unknown_update, now)
    assert [(o.book, o.side) for o in filtered] == [("fd", "home"), ("fd", "away")]
    # A newer incomplete capture cannot revive an older complete price.
    later = offer("fd", "moneyline", "home", None, at=T0 + timedelta(minutes=1),
                  updated=T0 + timedelta(minutes=1))
    assert fresh_paired_main_offers(valid + [later], now) == []
    assert fresh_paired_main_offers(valid, T0 + timedelta(hours=2)) == []
    in_flight = [offer("fd", "moneyline", "home", None,
                       updated=T0 + timedelta(seconds=2)),
                 offer("fd", "moneyline", "away", None,
                       updated=T0 + timedelta(seconds=2))]
    assert len(fresh_paired_main_offers(in_flight, now)) == 2


def test_history_has_one_consensus_per_snapshot_oldest_first() -> None:
    later = T0 + timedelta(hours=2)
    offers = [
        SideOffer("dk", "spread", "home", 1.5, -110, T0),
        SideOffer("dk", "spread", "away", -1.5, -110, T0),
        SideOffer("dk", "spread", "home", 2.5, -110, later),
        SideOffer("dk", "spread", "away", -2.5, -110, later),
    ]
    history = consensus_history(offers)
    assert [(when, c.spread_home) for when, c in history] == [(T0, 1.5), (later, 2.5)]
