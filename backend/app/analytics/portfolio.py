"""Deterministic paper-bet selection from already validated, immutable candidates.

This module does not turn spread watchlist rows or market-curve kinks into picks.
Callers must supply candidates whose forecast and quote have passed their own gates.
No account, database, provider, or bet-placement I/O occurs here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import floor, isfinite
from typing import Optional

from app.analytics.fees import fee_for
from app.analytics.pricing import decimal_odds
from app.domain.venues import venue_for_book


@dataclass(frozen=True)
class PaperPolicy:
    version: str = "paper-v1"
    min_net_ev: float = 0.03
    kelly_fraction: float = 0.25
    max_bet_fraction: float = 0.03
    max_game_fraction: float = 0.05
    max_day_fraction: float = 0.10
    reference_stake: float = 25.0
    min_stake: float = 1.0

    def __post_init__(self) -> None:
        shares = (self.kelly_fraction, self.max_bet_fraction,
                  self.max_game_fraction, self.max_day_fraction)
        if (not self.version or not 0 <= self.min_net_ev < 1
                or any(not isfinite(value) or not 0 < value <= 1 for value in shares)
                or not 0 < self.max_bet_fraction <= self.max_game_fraction <= self.max_day_fraction
                or not isfinite(self.reference_stake) or self.reference_stake <= 0
                or not isfinite(self.min_stake) or self.min_stake <= 0):
            raise ValueError("invalid paper portfolio policy")


DEFAULT_PAPER_POLICY = PaperPolicy()


@dataclass(frozen=True)
class PaperCandidate:
    candidate_id: str  # stable forecast + quote identity, supplied by the caller
    game_id: int
    book: str  # normalized eligible venue key
    price_american: int
    win_prob: float
    loss_prob: float
    forecast_id: str
    quote_id: str
    market: str = "moneyline"
    tier: str = "targeted"

    def __post_init__(self) -> None:
        if (not self.candidate_id or self.game_id <= 0 or not self.book
                or not self.forecast_id or not self.quote_id
                or self.market != "moneyline" or self.tier != "targeted"
                or venue_for_book(self.book) != self.book
                or not isinstance(self.price_american, int)
                or abs(self.price_american) < 100
                or not isfinite(self.win_prob) or not isfinite(self.loss_prob)
                or not 0 <= self.win_prob <= 1 or not 0 <= self.loss_prob <= 1
                or self.win_prob + self.loss_prob > 1 + 1e-9):
            raise ValueError("invalid paper candidate")
        decimal_odds(self.price_american)


@dataclass(frozen=True)
class PaperBet:
    candidate_id: str
    game_id: int
    stake: float
    fee: float
    net_ev: float
    kelly_fraction: float  # full Kelly before quartering and exposure caps


@dataclass(frozen=True)
class DayAllocation:
    profile: str
    policy: PaperPolicy
    opening_bankroll: float
    bets: tuple[PaperBet, ...]
    excluded: tuple[tuple[str, str], ...]  # candidate ID, reason

    @property
    def policy_version(self) -> str:
        return self.policy.version


def _net_ev(candidate: PaperCandidate, stake: float, on: date) -> float:
    fee = fee_for(candidate.book, stake, on)
    return (candidate.win_prob * (decimal_odds(candidate.price_american) - 1)
            - candidate.loss_prob - fee / stake)


def _full_kelly(candidate: PaperCandidate) -> float:
    """Exact no-fee Kelly fraction for win/loss/push probabilities."""
    decisive = candidate.win_prob + candidate.loss_prob
    if decisive == 0:
        return 0.0
    b = decimal_odds(candidate.price_american) - 1
    return max(0.0, (candidate.win_prob * b - candidate.loss_prob) / (b * decisive))


def allocate_day(
    candidates: Sequence[PaperCandidate], on: date, bankroll: float,
    policy: PaperPolicy = DEFAULT_PAPER_POLICY, top_n: Optional[int] = None,
) -> DayAllocation:
    """Rank by net EV at a fixed reference stake, then size against one opening bankroll.

    Same-day stakes reserve cash simultaneously. Fee and EV are checked again at
    the actual stake; a fee-waiver discontinuity may therefore exclude a bet.
    The quarter-Kelly stake is based on no-fee outcome probabilities and is a
    conservative approximation when a fixed per-bet fee applies.
    """
    if not isfinite(bankroll) or bankroll <= 0 or top_n is not None and top_n < 1:
        raise ValueError("bankroll must be positive and top_n must be at least one")
    ids = [candidate.candidate_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique")
    ranked = sorted(
        candidates,
        key=lambda c: (-_net_ev(c, policy.reference_stake, on), c.game_id, c.candidate_id),
    )
    bets: list[PaperBet] = []
    excluded: list[tuple[str, str]] = []
    game_reserved: dict[int, float] = {}  # stake plus fee
    day_reserved = 0.0
    for candidate in ranked:
        reference_ev = _net_ev(candidate, policy.reference_stake, on)
        if reference_ev < policy.min_net_ev:
            excluded.append((candidate.candidate_id, "below_reference_ev"))
            continue
        if top_n is not None and len(bets) >= top_n:
            excluded.append((candidate.candidate_id, "outside_top_n"))
            continue
        full_kelly = _full_kelly(candidate)
        available_cost = min(
            bankroll * policy.max_bet_fraction,
            bankroll * policy.max_game_fraction - game_reserved.get(candidate.game_id, 0.0),
            bankroll * policy.max_day_fraction - day_reserved,
            bankroll - day_reserved,
        )
        proposed = min(
            bankroll * policy.kelly_fraction * full_kelly,
            available_cost,
        )
        stake = floor((proposed + 1e-9) * 100) / 100
        while stake > 0 and stake + fee_for(candidate.book, stake, on) > available_cost + 1e-9:
            stake = round(stake - 0.01, 2)
        if stake < policy.min_stake:
            excluded.append((candidate.candidate_id, "below_minimum_or_cap"))
            continue
        actual_ev = _net_ev(candidate, stake, on)
        if actual_ev < policy.min_net_ev:
            excluded.append((candidate.candidate_id, "below_actual_ev"))
            continue
        fee = fee_for(candidate.book, stake, on)
        bets.append(PaperBet(candidate.candidate_id, candidate.game_id, stake,
                             fee, actual_ev, full_kelly))
        cost = stake + fee
        game_reserved[candidate.game_id] = game_reserved.get(candidate.game_id, 0.0) + cost
        day_reserved += cost
    profile = "all" if top_n is None else f"top_{top_n}"
    return DayAllocation(profile, policy, bankroll, tuple(bets), tuple(excluded))


def allocate_shadow_profiles(
    candidates: Sequence[PaperCandidate], on: date,
    bankrolls: Optional[Mapping[str, float]] = None,
    policy: PaperPolicy = DEFAULT_PAPER_POLICY,
) -> tuple[DayAllocation, ...]:
    """Independent all-eligible and top-1..10 paper variants, $1,000 each by default."""
    bankrolls = bankrolls or {}
    variants = (("all", None), *((f"top_{n}", n) for n in range(1, 11)))
    return tuple(
        allocate_day(candidates, on, bankrolls.get(name, 1000.0), policy, n)
        for name, n in variants
    )


def candidates_from_report(report: dict) -> tuple[PaperCandidate, ...]:
    """Only exact quote-linked targeted moneylines can enter paper allocations."""
    if report.get("kind") != "nba_daily_report" or not isinstance(report.get("picks"), list):
        raise ValueError("expected a saved NBA daily report")
    if any(pick.get("tier") == "targeted" and not pick.get("market")
           for pick in report["picks"]):
        raise ValueError("targeted pick lacks a market for paper allocation")
    selected = [p for p in report["picks"]
                if p.get("tier") == "targeted" and p.get("market") == "moneyline"]
    if not selected:
        return ()
    forecast = report.get("forecast_sha256")
    slate = report.get("slate_date")
    if not isinstance(forecast, str) or len(forecast) != 64 or not isinstance(slate, str):
        raise ValueError("report lacks exact forecast provenance")
    candidates = []
    for pick in selected:
        quote_id = pick.get("quote_observation_id")
        probability = pick.get("paper_win_prob")
        book = venue_for_book(str(pick.get("book", "")))
        if (not isinstance(quote_id, int) or quote_id <= 0 or probability is None
                or book is None):
            raise ValueError("targeted pick lacks exact paper candidate provenance")
        candidates.append(PaperCandidate(
            candidate_id=f"nba:{slate}:{forecast}:{quote_id}",
            game_id=int(pick["game_id"]), book=book,
            price_american=int(pick["price_american"]),
            win_prob=float(probability), loss_prob=1 - float(probability),
            forecast_id=forecast, quote_id=str(quote_id),
        ))
    ids = [candidate.candidate_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("targeted picks reuse a quote observation")
    return tuple(candidates)
