"""Illinois per-bet fees, by book and date. A bet stores the fee it was charged at entry, so
changing a rule here never rewrites history: add a new dated rule instead of editing an old one."""

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class FeeRule:
    venue: str
    fee: float
    waive_at_stake: Optional[float]  # no fee at or above this stake (all bets here are singles)
    effective_from: date


# ponytail: the start dates are the Illinois per-wager tax start; check each book's notice and
# add a new dated rule when a book changes its policy.
FEE_RULES = (
    FeeRule("fanduel", 0.50, 25.0, date(2025, 7, 1)),
    FeeRule("draftkings", 0.50, 50.0, date(2025, 7, 1)),
)


def fee_for(venue: str, stake: float, on: date, rules: tuple = FEE_RULES) -> float:
    """The fee for a single bet of `stake` at `venue`, under the rule in force on `on`."""
    in_force = [r for r in rules if r.venue == venue and r.effective_from <= on]
    if not in_force:
        return 0.0
    rule = max(in_force, key=lambda r: r.effective_from)
    if rule.waive_at_stake is not None and stake >= rule.waive_at_stake:
        return 0.0
    return rule.fee
