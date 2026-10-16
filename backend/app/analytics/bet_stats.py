"""Profit, ROI after fees and closing-line value across a set of bets. Pure functions."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

GRADED = ("won", "lost", "push")


@dataclass(frozen=True)
class BetFacts:
    venue: str
    sport: str
    market: str
    status: str  # open | won | lost | push | void
    stake: float
    fee: float
    payout: Optional[float]
    clv_points: Optional[float]
    clv_prob: Optional[float]


@dataclass(frozen=True)
class SummaryRow:
    key: str
    bets: int
    open: int
    settled: int  # won, lost or pushed; a voided bet is neither staked nor graded
    staked: float
    fees: float
    profit: float  # after fees
    roi: Optional[float]  # profit / staked
    avg_clv_points: Optional[float]
    avg_clv_prob: Optional[float]
    clv_samples: int
    beat_close_pct: Optional[float]


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _beat_close(fact: BetFacts) -> bool:
    """The line decides when the line moved; otherwise the price does."""
    if fact.clv_points:
        return fact.clv_points > 0
    return fact.clv_prob is not None and fact.clv_prob > 0


def _row(key: str, facts: Sequence[BetFacts]) -> SummaryRow:
    graded = [f for f in facts if f.status in GRADED]
    staked = sum(f.stake for f in graded)
    profit = sum((f.payout or 0.0) - f.stake - f.fee for f in graded)
    measured = [f for f in facts if f.status != "void" and (
        f.clv_points is not None or f.clv_prob is not None)]
    return SummaryRow(
        key=key,
        bets=len(facts),
        open=sum(1 for f in facts if f.status == "open"),
        settled=len(graded),
        staked=round(staked, 2),
        fees=round(sum(f.fee for f in graded), 2),
        profit=round(profit, 2),
        roi=profit / staked if staked else None,
        avg_clv_points=_mean([f.clv_points for f in measured if f.clv_points is not None]),
        avg_clv_prob=_mean([f.clv_prob for f in measured if f.clv_prob is not None]),
        clv_samples=len(measured),
        beat_close_pct=(
            100 * sum(1 for f in measured if _beat_close(f)) / len(measured) if measured else None
        ),
    )


def summarize(facts: Sequence[BetFacts], group_by: str) -> tuple[SummaryRow, list[SummaryRow]]:
    """(all bets, one row per venue / sport / market), the groups sorted by key."""
    groups: dict[str, list[BetFacts]] = defaultdict(list)
    for fact in facts:
        groups[getattr(fact, group_by)].append(fact)
    return _row("all", facts), [_row(key, groups[key]) for key in sorted(groups)]
