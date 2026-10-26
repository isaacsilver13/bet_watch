"""Price the NBA model's line-free predictions against every book. Pure functions, no I/O.

Moneylines with EV over 3% are the `targeted` picks (the only market with a measured edge, and only
against early prices). Spreads are a `watchlist`: unproven, tracked for CLV only."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.analytics.fees import fee_for
from app.analytics.market import BookQuote, Consensus
from app.analytics.pricing import decimal_odds
from app.analytics.spread_curve import net_spread_ev, spread_mass
from app.domain.venues import venue_for_book

TARGETED_MIN_EV = 0.03
GRADED = ("won", "lost", "push")


@dataclass(frozen=True)
class Pick:
    market: str  # moneyline | spread
    side: str  # home | away
    line: Optional[float]  # the side's own number: home -7.5, away +7.5
    book: str  # venue key (fanduel) when known, else the feed's title
    price_american: int
    model_prob: float
    market_prob_novig: Optional[float]  # consensus no-vig probability of this side
    ev: float
    kelly_frac: float
    tier: str  # targeted | watchlist


def expected_value(prob: float, american: int, fee: float, stake: float) -> float:
    """Return per unit staked, after a flat per-bet fee charged on a bet of `stake`."""
    return prob * decimal_odds(american) - 1 - fee / stake


def kelly(
    ev: float, american: int, fraction: float, cap: float, decisive_prob: float = 1.0
) -> float:
    """Fractional Kelly share of bankroll, clipped to [0, cap]. Without a fee, ev / b is the
    textbook (p(b+1) - 1) / b, as in the nba repo's experiment.py."""
    return min(max(fraction * ev / ((decimal_odds(american) - 1) * decisive_prob), 0.0), cap)


def home_cover_prob(pred_margin: float, sigma: float, spread_home: float) -> float:
    """P(home margin + spread_home > 0), with integer-score push mass retained."""
    return spread_mass(pred_margin, sigma, spread_home).home_cover


def _best(
    quotes: Sequence[BookQuote], field: str, prob: float, on: date, stake: float,
    loss_prob: Optional[float] = None,
) -> Optional[tuple[str, int, float]]:
    """(book, price, ev) of the book whose price is worth most to us after its fee. Offers carry
    the feed's title ("FanDuel"); fees are keyed by venue ("fanduel"). Only Illinois venues can be
    picked: offshore books (Bovada, BetOnline...) still shape the consensus but can't be bet."""
    priced = []
    for q in quotes:
        price = getattr(q, field)
        venue = venue_for_book(q.book)
        if price is None or venue is None:
            continue
        fee = fee_for(venue, stake, on)
        ev = (expected_value(prob, price, fee, stake) if loss_prob is None else
              net_spread_ev(prob, loss_prob, price, fee, stake))
        priced.append((venue, price, ev))
    return max(priced, key=lambda row: row[2]) if priced else None


def game_picks(
    pred_margin: float,
    p_home_win: float,
    sigma: float,
    quotes: Sequence[BookQuote],
    market: Consensus,
    on: date,
    stake: float,
    fraction: float,
    cap: float,
) -> list[Pick]:
    picks = []
    home_ml = market.home_win_prob
    for side, field, prob, market_prob in (
        ("home", "ml_home", p_home_win, home_ml),
        ("away", "ml_away", 1 - p_home_win, None if home_ml is None else 1 - home_ml),
    ):
        best = _best(quotes, field, prob, on, stake)
        if best and best[2] > TARGETED_MIN_EV:
            book, price, ev = best
            picks.append(Pick("moneyline", side, None, book, price, prob, market_prob, ev,
                              kelly(ev, price, fraction, cap), "targeted"))
    spread = market.spread_home
    if spread is None:
        return picks
    # Like best_prices: only books at the consensus number are compared.
    at_line = [q for q in quotes if q.spread_home == spread]
    mass = spread_mass(pred_margin, sigma, spread)
    home_mkt = market.home_cover_prob
    for side, field, line, prob, loss_prob, market_prob in (
        ("home", "spread_home_price", spread, mass.home_cover, mass.away_cover, home_mkt),
        ("away", "spread_away_price", -spread, mass.away_cover, mass.home_cover,
         None if home_mkt is None else 1 - home_mkt),
    ):
        best = _best(at_line, field, prob, on, stake, loss_prob)
        if best and best[2] > 0:
            book, price, ev = best
            picks.append(Pick("spread", side, line, book, price, prob, market_prob, ev,
                              kelly(ev, price, fraction, cap, 1 - mass.push), "watchlist"))
    return picks


@dataclass(frozen=True)
class PickFacts:
    tier: str
    price_american: int
    result: Optional[str]
    clv: Optional[float]


@dataclass(frozen=True)
class TrackRecord:
    tier: str
    picks: int
    graded: int
    won: int
    lost: int
    push: int
    flat_roi: Optional[float]  # one unit per pick, before fees
    avg_clv: Optional[float]
    clv_samples: int
    beat_close_pct: Optional[float]


def track_record(facts: Sequence[PickFacts]) -> list[TrackRecord]:
    """One row per tier, targeted first."""
    rows = []
    for tier in ("targeted", "watchlist"):
        mine = [f for f in facts if f.tier == tier]
        graded = [f for f in mine if f.result in GRADED]
        units = sum(
            decimal_odds(f.price_american) - 1 if f.result == "won"
            else -1.0 if f.result == "lost" else 0.0
            for f in graded
        )
        clvs = [f.clv for f in mine if f.clv is not None]
        rows.append(TrackRecord(
            tier=tier,
            picks=len(mine),
            graded=len(graded),
            won=sum(f.result == "won" for f in graded),
            lost=sum(f.result == "lost" for f in graded),
            push=sum(f.result == "push" for f in graded),
            flat_roi=units / len(graded) if graded else None,
            avg_clv=sum(clvs) / len(clvs) if clvs else None,
            clv_samples=len(clvs),
            beat_close_pct=100 * sum(c > 0 for c in clvs) / len(clvs) if clvs else None,
        ))
    return rows
