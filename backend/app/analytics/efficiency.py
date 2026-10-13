"""Season-to-date team efficiency with league ranks, from nflverse per-game team stats.

Defense is the opponent's offense in the same game. Plays are approximated as
pass attempts + sacks taken + carries (the file has no play count).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# metric -> True when a higher value is better (used for the rank)
METRICS = {
    "off_epa_play": True,
    "def_epa_play": False,
    "off_pass_epa_play": True,
    "off_rush_epa_play": True,
    "def_pass_epa_play": False,
    "def_rush_epa_play": False,
    "yards_play": True,
    "yards_play_allowed": False,
    "points_for_pg": True,
    "points_against_pg": False,
    "turnovers_pg": False,
    "takeaways_pg": True,
    "sacks_taken_pg": False,
    "sacks_pg": True,
}


@dataclass(frozen=True)
class TeamGame:
    game_id: int
    team_id: int
    start_time: datetime
    stats: Mapping[str, float]
    points_for: Optional[int]
    points_against: Optional[int]


@dataclass(frozen=True)
class MetricValue:
    value: float
    rank: int
    of: int


def _get(stats: Mapping[str, float], key: str) -> float:
    return float(stats.get(key) or 0.0)


def _offense(stats: Mapping[str, float]) -> dict[str, float]:
    pass_plays = _get(stats, "attempts") + _get(stats, "sacks_suffered")
    rush_plays = _get(stats, "carries")
    return {
        "plays": pass_plays + rush_plays,
        "pass_plays": pass_plays,
        "rush_plays": rush_plays,
        "epa": _get(stats, "passing_epa") + _get(stats, "rushing_epa"),
        "pass_epa": _get(stats, "passing_epa"),
        "rush_epa": _get(stats, "rushing_epa"),
        "yards": _get(stats, "passing_yards") + _get(stats, "rushing_yards"),
        "turnovers": _get(stats, "passing_interceptions")
        + _get(stats, "rushing_fumbles_lost")
        + _get(stats, "sack_fumbles_lost")
        + _get(stats, "receiving_fumbles_lost"),
        "sacks_taken": _get(stats, "sacks_suffered"),
    }


def _ratio(numerator: float, denominator: float) -> Optional[float]:
    return numerator / denominator if denominator else None


def _totals(rows: Sequence[TeamGame]) -> dict[int, dict[str, float]]:
    by_game: dict[int, list[TeamGame]] = defaultdict(list)
    for row in rows:
        by_game[row.game_id].append(row)
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for game_rows in by_game.values():
        for row in game_rows:
            mine = totals[row.team_id]
            for key, value in _offense(row.stats).items():
                mine[f"off_{key}"] += value
            mine["games"] += 1
            mine["sacks"] += _get(row.stats, "def_sacks")
            if row.points_for is not None and row.points_against is not None:
                mine["points_games"] += 1
                mine["points_for"] += row.points_for
                mine["points_against"] += row.points_against
            opponents = [other for other in game_rows if other.team_id != row.team_id]
            if len(opponents) == 1:  # defense needs the other side's row
                mine["def_games"] += 1
                for key, value in _offense(opponents[0].stats).items():
                    mine[f"def_{key}"] += value
    return totals


def _metrics(t: Mapping[str, float]) -> dict[str, float]:
    raw = {
        "off_epa_play": _ratio(t["off_epa"], t["off_plays"]),
        "def_epa_play": _ratio(t["def_epa"], t["def_plays"]),
        "off_pass_epa_play": _ratio(t["off_pass_epa"], t["off_pass_plays"]),
        "off_rush_epa_play": _ratio(t["off_rush_epa"], t["off_rush_plays"]),
        "def_pass_epa_play": _ratio(t["def_pass_epa"], t["def_pass_plays"]),
        "def_rush_epa_play": _ratio(t["def_rush_epa"], t["def_rush_plays"]),
        "yards_play": _ratio(t["off_yards"], t["off_plays"]),
        "yards_play_allowed": _ratio(t["def_yards"], t["def_plays"]),
        "points_for_pg": _ratio(t["points_for"], t["points_games"]),
        "points_against_pg": _ratio(t["points_against"], t["points_games"]),
        "turnovers_pg": _ratio(t["off_turnovers"], t["games"]),
        "takeaways_pg": _ratio(t["def_turnovers"], t["def_games"]),
        "sacks_taken_pg": _ratio(t["off_sacks_taken"], t["games"]),
        "sacks_pg": _ratio(t["sacks"], t["games"]),
    }
    return {name: value for name, value in raw.items() if value is not None}


def season_efficiency(
    rows: Sequence[TeamGame], before: datetime
) -> dict[int, dict[str, MetricValue]]:
    """Per team: every metric with its league rank, using only games before `before`."""
    per_team = {
        team_id: _metrics(totals)
        for team_id, totals in _totals([r for r in rows if r.start_time < before]).items()
    }
    result: dict[int, dict[str, MetricValue]] = {team_id: {} for team_id in per_team}
    for name, higher_is_better in METRICS.items():
        values = {team_id: m[name] for team_id, m in per_team.items() if name in m}
        for team_id, value in values.items():
            better = sum(
                1 for v in values.values() if (v > value if higher_is_better else v < value)
            )
            result[team_id][name] = MetricValue(round(value, 3), better + 1, len(values))
    return result
