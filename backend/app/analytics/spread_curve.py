"""Experimental spread distribution and market-curve arithmetic; no picks are issued here.

Basketball scores have integer margins. A normal residual model is discretized into
one-point bins so whole-number spreads retain their push probability. Market points
are conditional on a decisive result, as two-way spread prices are.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import floor, isfinite
from statistics import NormalDist, median
from typing import Optional

from app.analytics.market import SideOffer
from app.analytics.pricing import decimal_odds, implied_prob, novig


@dataclass(frozen=True)
class SpreadMass:
    home_cover: float
    away_cover: float
    push: float


def spread_mass(pred_margin: float, sigma: float, spread_home: float) -> SpreadMass:
    """Win/loss/push mass for home margin plus home spread, using integer-score bins."""
    if not all(isfinite(value) for value in (pred_margin, sigma, spread_home)) or sigma <= 0:
        raise ValueError("margin, sigma and line must be finite; sigma must be positive")
    normal = NormalDist(mu=pred_margin, sigma=sigma)
    push_margin = -spread_home
    first_home_win = floor(push_margin) + 1
    home = 1 - normal.cdf(first_home_win - 0.5)
    push = (normal.cdf(push_margin + 0.5) - normal.cdf(push_margin - 0.5)
            if float(push_margin).is_integer() else 0.0)
    return SpreadMass(home, max(0.0, 1 - home - push), push)


def net_spread_ev(win: float, loss: float, american: int, fee: float, stake: float) -> float:
    """Profit per unit staked; a push returns stake but still incurs the stated fee."""
    if stake <= 0 or fee < 0 or not 0 <= win <= 1 or not 0 <= loss <= 1 or win + loss > 1 + 1e-9:
        raise ValueError("invalid stake, fee or outcome probabilities")
    return win * (decimal_odds(american) - 1) - loss - fee / stake


@dataclass(frozen=True)
class PairedSpread:
    book: str
    observed_at: datetime
    spread_home: float
    home_price: int
    away_price: int
    provider_updated_at: datetime

    @property
    def home_prob_novig(self) -> float:
        return novig(implied_prob(self.home_price), implied_prob(self.away_price))[0]


def paired_spreads(
    offers: Sequence[SideOffer], as_of: datetime, max_age: timedelta = timedelta(minutes=10)
) -> list[PairedSpread]:
    """Mirrored pairs with matching fresh capture and provider market-update times."""
    if max_age < timedelta(0):
        raise ValueError("max_age must be nonnegative")
    grouped: dict[tuple[str, datetime, float], dict[str, SideOffer]] = defaultdict(dict)
    for offer in offers:
        updated_at = offer.provider_updated_at
        if (offer.market != "spread" or offer.line is None or not isfinite(offer.line)
                or updated_at is None or updated_at > as_of or as_of - updated_at > max_age
                or offer.observed_at > as_of or as_of - offer.observed_at > max_age):
            continue
        home_line = offer.line if offer.side == "home" else -offer.line
        if offer.side in {"home", "away"}:
            grouped[(offer.book, offer.observed_at, home_line)][offer.side] = offer
    latest: dict[tuple[str, float], PairedSpread] = {}
    for (book, observed_at, home_line), sides in grouped.items():
        if "home" not in sides or "away" not in sides:
            continue
        home, away = sides["home"], sides["away"]
        if home.provider_updated_at != away.provider_updated_at:
            continue
        pair = PairedSpread(
            book, observed_at, home_line, home.price, away.price, home.provider_updated_at
        )
        key = (book, home_line)
        if key not in latest or observed_at > latest[key].observed_at:
            latest[key] = pair
    return sorted(latest.values(), key=lambda pair: (pair.spread_home, pair.book))


@dataclass(frozen=True)
class CurvePoint:
    spread_home: float
    home_prob_novig: float
    books: int


def monotone_curve(
    pairs: Sequence[PairedSpread], exclude_book: Optional[str] = None
) -> list[CurvePoint]:
    """Median no-vig probabilities by threshold, projected to nondecreasing order with PAVA."""
    grouped: dict[float, list[float]] = defaultdict(list)
    for pair in pairs:
        if pair.book != exclude_book:
            grouped[pair.spread_home].append(pair.home_prob_novig)
    lines = sorted(grouped)
    values = [median(grouped[line]) for line in lines]
    weights = [len(grouped[line]) for line in lines]
    blocks: list[tuple[int, int, float, int]] = []  # start, end, weighted sum, weight
    for index, (value, weight) in enumerate(zip(values, weights)):
        blocks.append((index, index + 1, value * weight, weight))
        while len(blocks) >= 2 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            left, right = blocks[-2:]
            blocks[-2:] = [(left[0], right[1], left[2] + right[2], left[3] + right[3])]
    projected = [0.0] * len(lines)
    for start, end, total, weight in blocks:
        projected[start:end] = [total / weight] * (end - start)
    return [
        CurvePoint(line, prob, weights[index])
        for index, (line, prob) in enumerate(zip(lines, projected))
    ]


def leave_one_book_out_gap(
    candidate: PairedSpread, pairs: Sequence[PairedSpread]
) -> Optional[float]:
    """Candidate probability minus interpolated peer curve; None unless peers bracket the line.

    A gap is a review signal, never evidence of positive expected value on its own.
    """
    curve = monotone_curve(pairs, exclude_book=candidate.book)
    lower = [point for point in curve if point.spread_home < candidate.spread_home]
    upper = [point for point in curve if point.spread_home > candidate.spread_home]
    if not lower or not upper:
        return None
    left, right = lower[-1], upper[0]
    share = (candidate.spread_home - left.spread_home) / (right.spread_home - left.spread_home)
    expected = left.home_prob_novig + share * (right.home_prob_novig - left.home_prob_novig)
    return candidate.home_prob_novig - expected
