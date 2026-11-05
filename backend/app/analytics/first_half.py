"""Season-to-date first-half profile per team, with league ranks, from nflverse play-by-play.

Pure: no I/O, and only games that kicked off before `before` count. A team's first-half defense
is the opponent's offense in the same game. Only final games count, because the point of the
halftime splits is what happened next: led at the half and won, trailed and came back.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# metric -> True when a higher value is better (used for the rank)
METRICS = {
    "h1_points_for_pg": True,
    "h1_points_against_pg": False,
    "h1_margin_pg": True,
    "h1_off_epa_play": True,
    "h1_def_epa_play": False,
    "h1_off_pass_epa_play": True,
    "h1_off_rush_epa_play": True,
    "h1_def_pass_epa_play": False,
    "h1_def_rush_epa_play": False,
    "h1_turnovers_pg": False,
    "h1_takeaways_pg": True,
}


@dataclass(frozen=True)
class HalfGame:
    game_id: int
    team_id: int
    start_time: datetime
    points_for_1h: int
    points_against_1h: int
    points_for: Optional[int]  # final score; None until the game is final
    points_against: Optional[int]
    scored_first: Optional[bool]
    stats: Mapping[str, float]  # this team's first-half offense


@dataclass(frozen=True)
class MetricValue:
    value: float
    rank: int
    of: int


@dataclass(frozen=True)
class Outcome:
    """Final results of the games in one halftime state; a tie at the end is a push."""

    win: int = 0
    loss: int = 0
    push: int = 0


@dataclass(frozen=True)
class FirstHalf:
    games: int
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    second_half_margin_pg: Optional[float] = None
    recent_margin_pg: Optional[float] = None  # first-half margin over the last `last_n` games
    led: Outcome = Outcome()
    tied: Outcome = Outcome()
    trailed: Outcome = Outcome()
    scored_first_games: int = 0  # games this team scored first in
    scored_first_known: int = 0  # games where anyone had scored by the final play
    scored_first_record: Outcome = Outcome()


def _ratio(numerator: float, denominator: float) -> Optional[float]:
    return numerator / denominator if denominator else None


def _stat(stats: Mapping[str, float], key: str) -> float:
    return float(stats.get(key) or 0.0)


def _outcome(games: Sequence[HalfGame]) -> Outcome:
    win = sum(1 for g in games if g.points_for > g.points_against)  # type: ignore[operator]
    loss = sum(1 for g in games if g.points_for < g.points_against)  # type: ignore[operator]
    return Outcome(win, loss, len(games) - win - loss)


def _team_metrics(games: Sequence[HalfGame], opponents: Mapping[int, HalfGame]) -> dict[str, float]:
    mine: dict[str, float] = defaultdict(float)
    for game in games:
        for key, value in game.stats.items():
            mine[f"off_{key}"] += float(value)
        opponent = opponents.get(game.game_id)
        if opponent is not None:  # defense needs the other side's row
            mine["def_games"] += 1
            for key, value in opponent.stats.items():
                mine[f"def_{key}"] += float(value)
    n = len(games)
    raw = {
        "h1_points_for_pg": _ratio(sum(g.points_for_1h for g in games), n),
        "h1_points_against_pg": _ratio(sum(g.points_against_1h for g in games), n),
        "h1_margin_pg": _ratio(sum(g.points_for_1h - g.points_against_1h for g in games), n),
        "h1_off_epa_play": _ratio(mine["off_epa"], mine["off_plays"]),
        "h1_def_epa_play": _ratio(mine["def_epa"], mine["def_plays"]),
        "h1_off_pass_epa_play": _ratio(mine["off_pass_epa"], mine["off_pass_plays"]),
        "h1_off_rush_epa_play": _ratio(mine["off_rush_epa"], mine["off_rush_plays"]),
        "h1_def_pass_epa_play": _ratio(mine["def_pass_epa"], mine["def_pass_plays"]),
        "h1_def_rush_epa_play": _ratio(mine["def_rush_epa"], mine["def_rush_plays"]),
        "h1_turnovers_pg": _ratio(mine["off_turnovers"], n),
        "h1_takeaways_pg": _ratio(mine["def_turnovers"], mine["def_games"]),
    }
    return {name: value for name, value in raw.items() if value is not None}


def season_first_half(
    rows: Sequence[HalfGame], before: datetime, last_n: int = 5
) -> dict[int, FirstHalf]:
    """Per team: first-half metrics with league ranks and the halftime-to-final splits, using
    only final games before `before`. The ranks cover every team with a qualifying game."""
    final = [
        r for r in rows
        if r.start_time < before and r.points_for is not None and r.points_against is not None
    ]
    by_game: dict[int, dict[int, HalfGame]] = defaultdict(dict)
    by_team: dict[int, list[HalfGame]] = defaultdict(list)
    for row in final:
        by_game[row.game_id][row.team_id] = row
        by_team[row.team_id].append(row)

    per_team: dict[int, dict[str, float]] = {}
    for team_id, games in by_team.items():
        opponents = {
            g.game_id: other
            for g in games
            for other_id, other in by_game[g.game_id].items()
            if other_id != team_id
        }
        per_team[team_id] = _team_metrics(games, opponents)

    ranked: dict[int, dict[str, MetricValue]] = {team_id: {} for team_id in per_team}
    for name, higher_is_better in METRICS.items():
        values = {team_id: m[name] for team_id, m in per_team.items() if name in m}
        for team_id, value in values.items():
            better = sum(
                1 for v in values.values() if (v > value if higher_is_better else v < value)
            )
            ranked[team_id][name] = MetricValue(round(value, 3), better + 1, len(values))

    result: dict[int, FirstHalf] = {}
    for team_id, games in by_team.items():
        ordered = sorted(games, key=lambda g: g.start_time)
        recent = ordered[-last_n:]
        known = [g for g in games if g.scored_first is not None]
        first = [g for g in known if g.scored_first]
        result[team_id] = FirstHalf(
            games=len(games),
            metrics=ranked[team_id],
            second_half_margin_pg=_ratio(
                sum(
                    (g.points_for - g.points_for_1h) - (g.points_against - g.points_against_1h)  # type: ignore[operator]
                    for g in games
                ),
                len(games),
            ),
            recent_margin_pg=_ratio(
                sum(g.points_for_1h - g.points_against_1h for g in recent), len(recent)
            ),
            led=_outcome([g for g in games if g.points_for_1h > g.points_against_1h]),
            tied=_outcome([g for g in games if g.points_for_1h == g.points_against_1h]),
            trailed=_outcome([g for g in games if g.points_for_1h < g.points_against_1h]),
            scored_first_games=len(first),
            scored_first_known=len(known),
            scored_first_record=_outcome(first),
        )
    return result
