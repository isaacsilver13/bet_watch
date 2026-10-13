"""Closing line value: did you get a better number or price than the market closed at?"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.analytics.market import BookQuote, Consensus, consensus
from app.analytics.pricing import fair_american, implied_prob

MAX_CLOSE_AGE = timedelta(minutes=60)  # an earlier snapshot is not a close, just a snapshot


@dataclass(frozen=True)
class Close:
    source: str  # odds_api | espn
    line: Optional[float]  # in the bet side's orientation: home -3.5, away +3.5, over 46
    prob: Optional[float]  # no-vig probability the bet side wins at that line


@dataclass(frozen=True)
class Clv:
    points: Optional[float]  # spread / total only; positive means you beat the close
    prob: Optional[float]  # no-vig close probability minus your break-even probability
    close_line: Optional[float]
    close_price: Optional[int]  # the no-vig closing price on your side
    source: str


def close_from_consensus(
    c: Consensus, market: str, selection: str, source: str
) -> Optional[Close]:
    first = selection in ("home", "over")
    if market == "spread":
        line, prob = c.spread_home, c.home_cover_prob
        if line is not None and not first:
            line = -line
    elif market == "total":
        line, prob = c.total, c.over_prob
    else:
        line, prob = None, c.home_win_prob
        if prob is None:
            return None
    if market != "moneyline" and line is None:
        return None
    if prob is not None and not first:
        prob = 1 - prob
    return Close(source, line, prob)


def pick_close(
    snapshot: Optional[tuple[datetime, Consensus]],
    kickoff: datetime,
    espn: Optional[BookQuote],
    market: str,
    selection: str,
) -> Optional[Close]:
    """The pre-kick Odds API consensus if there is one close enough to kickoff, else ESPN's."""
    if snapshot is not None and kickoff - MAX_CLOSE_AGE <= snapshot[0] <= kickoff:
        close = close_from_consensus(snapshot[1], market, selection, "odds_api")
        if close is not None:
            return close
    if espn is not None:
        return close_from_consensus(consensus([espn]), market, selection, "espn")
    return None


def clv(market: str, selection: str, line: Optional[float], price: int, close: Close) -> Clv:
    points = None
    if market in ("spread", "total") and line is not None and close.line is not None:
        # A lower number is better for an over; for every other side a higher one is.
        gain = close.line - line if (market, selection) == ("total", "over") else line - close.line
        points = round(gain, 2)
    prob = None if close.prob is None else close.prob - implied_prob(price)
    return Clv(
        points=points,
        prob=prob,
        close_line=close.line,
        close_price=None if close.prob is None else fair_american(close.prob),
        source=close.source,
    )


def ratio_clv(price: int, close_price: int) -> float:
    """How much better than break-even the close was: p_close / p_bet - 1."""
    return implied_prob(close_price) / implied_prob(price) - 1
