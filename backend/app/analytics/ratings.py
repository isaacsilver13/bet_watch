"""Season-to-date NBA team ratings and four factors with league ranks. Pure: no I/O.

Each game's numbers are weighted by its possessions, so a team's season offensive rating is
total points scored per 100 possessions. Pace is weighted by minutes played (overtime counts
more). Only games before `before` are used, so a finished game shows what was known at tip-off.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.analytics.efficiency import MetricValue

# metric -> (stats.nba.com field, True when a higher value is better). Pace is ranked fastest first.
METRICS = {
    "off_rtg": ("OFF_RATING", True),
    "def_rtg": ("DEF_RATING", False),
    "pace": ("PACE", True),
    "efg": ("EFG_PCT", True),
    "tov_pct": ("TM_TOV_PCT", False),
    "oreb_pct": ("OREB_PCT", True),
    "ftr": ("FTA_RATE", True),
    "opp_efg": ("OPP_EFG_PCT", False),
    "opp_tov_pct": ("OPP_TOV_PCT", True),
    "opp_ftr": ("OPP_FTA_RATE", False),
    "opp_oreb_pct": ("OPP_OREB_PCT", False),
}
NET = "net_rtg"
DIGITS = {"off_rtg": 2, "def_rtg": 2, NET: 2, "pace": 1}  # everything else is a fraction: 3


@dataclass(frozen=True)
class RatedGame:
    game_id: int
    team_id: int
    start_time: datetime
    stats: Mapping[str, float]


def _weighted(rows: Sequence[RatedGame], field: str, weight: str) -> Optional[float]:
    pairs = [(r.stats[field], r.stats.get(weight) or 0.0) for r in rows if field in r.stats]
    total = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total if total else None


def _team_metrics(rows: Sequence[RatedGame]) -> dict[str, float]:
    values: dict[str, float] = {}
    for name, (field, _) in METRICS.items():
        weight = "MIN" if name == "pace" else "POSS"
        value = _weighted(rows, field, weight)
        if value is not None:
            values[name] = value
    if "off_rtg" in values and "def_rtg" in values:
        values[NET] = values["off_rtg"] - values["def_rtg"]
    return values


def season_ratings(
    rows: Sequence[RatedGame], before: datetime
) -> dict[int, dict[str, MetricValue]]:
    """Per team: every metric with its league rank, using only games before `before`."""
    by_team: dict[int, list[RatedGame]] = defaultdict(list)
    for row in rows:
        if row.start_time < before:
            by_team[row.team_id].append(row)
    per_team = {team_id: _team_metrics(games) for team_id, games in by_team.items()}
    higher_is_better = {name: better for name, (_, better) in METRICS.items()}
    higher_is_better[NET] = True
    result: dict[int, dict[str, MetricValue]] = {team_id: {} for team_id in per_team}
    for name, better_high in higher_is_better.items():
        values = {t: m[name] for t, m in per_team.items() if name in m}
        for team_id, value in values.items():
            ahead = sum(1 for v in values.values() if (v > value if better_high else v < value))
            digits = DIGITS.get(name, 3)
            result[team_id][name] = MetricValue(round(value, digits), ahead + 1, len(values))
    return result
