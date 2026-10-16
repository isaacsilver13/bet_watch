"""A team's star players (the most minutes per game) and whether the injury report has them out.

Pure functions. Players are identified by `name_key`, so "Jabari Smith Jr." on the injury report
matches "Jabari Smith" in the minutes file."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.analytics.injuries import InjuredPlayer, name_key

STAR_COUNT = 3
MIN_GAMES = 5  # a one-game cameo of 40 minutes is not a star
SETTLED_GAMES = 10  # until a team has played this many, last season's minutes decide
OUT = frozenset({"Out", "Doubtful", "Injured Reserve", "Suspension"})
SOFT = frozenset({"Questionable", "Day-To-Day"})  # ESPN's NBA report says Day-To-Day


@dataclass(frozen=True)
class PlayerMinutes:
    team_id: int
    player: str  # name_key
    games: int
    minutes_pg: float


@dataclass(frozen=True)
class StarFlag:
    out: list[str]  # display names, sorted
    questionable: list[str]


def pick_stars(
    team_id: int,
    current: Sequence[PlayerMinutes],
    previous: Sequence[PlayerMinutes],
    games_played: int,
) -> set[str]:
    """Name keys of the team's top-minutes players. Before the team has played SETTLED_GAMES the
    previous season decides, except that a player already in this season's rows belongs to the
    team those rows say (a trade)."""
    if games_played >= SETTLED_GAMES:
        pool = [p for p in current if p.team_id == team_id]
    else:
        moved = {p.player: p.team_id for p in current}
        pool = [p for p in previous if moved.get(p.player, p.team_id) == team_id]
    ranked = sorted(
        (p for p in pool if p.games >= MIN_GAMES), key=lambda p: (-p.minutes_pg, p.player)
    )
    return {p.player for p in ranked[:STAR_COUNT]}


def star_flag(stars: set[str], injured: Sequence[InjuredPlayer]) -> StarFlag:
    out, soft = [], []
    for item in injured:
        if name_key(item.player) not in stars:
            continue
        if item.status in OUT:
            out.append(item.player)
        elif item.status in SOFT:
            soft.append(item.player)
    return StarFlag(sorted(out), sorted(soft))
