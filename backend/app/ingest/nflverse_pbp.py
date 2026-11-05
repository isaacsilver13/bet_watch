"""nflverse play-by-play (play_by_play_<season>.csv, CC-BY-4.0), reduced to each team's first half.

One record per team per game: the score at halftime, who scored first, and the team's first-half
offense (plays, EPA, success, pass/rush split, turnovers, sacks). A team's first-half defense is
the opponent's offense in the same game, as in `analytics/efficiency.py`.

Verified 2026-11-04: one file per season (about 42 MB for 8 weeks, 372 columns, so it is parsed
from disk row by row, never held in memory), `week` keeps counting through the postseason,
`season_type` is REG or POST, team codes are nflverse's (Rams are LA). `total_home_score` and
`total_away_score` are the score at the START of a play, so the halftime score is the one on the
first Half2 row (the second-half kickoff), not the last Half1 row, which would miss a final-play
score. A game with no Half2 row has not reached halftime and produces no record.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{season}.csv"
)
SEASON_TYPES = {"REG", "POST"}

COLUMNS = (
    "game_id", "season", "season_type", "week", "home_team", "away_team", "game_half",
    "posteam", "defteam", "total_home_score", "total_away_score", "epa", "success",
    "pass", "rush", "yards_gained", "interception", "fumble_lost", "sack",
)


@dataclass(frozen=True)
class HalfRecord:
    season: int
    season_type: str  # REG | POST
    week: int  # nflverse numbering: the postseason continues after week 18
    team: str  # nflverse code, resolved through team_aliases
    opponent: str
    points_for_1h: int
    points_against_1h: int
    scored_first: Optional[bool]  # None when no one has scored yet
    stats: dict[str, float]  # this team's first-half offense


def _number(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip() in {"", "NA"}:
        return None
    return float(value)


def _flag(value: Optional[str]) -> bool:
    number = _number(value)
    return number is not None and number > 0


class _Offense:
    """Running first-half offense for one team in one game."""

    def __init__(self) -> None:
        self.plays = self.pass_plays = self.rush_plays = 0
        self.epa = self.pass_epa = self.rush_epa = 0.0
        self.success = self.yards = self.turnovers = self.sacks_taken = 0.0

    def add(self, row: dict[str, str]) -> None:
        is_pass, is_rush = _flag(row["pass"]), _flag(row["rush"])
        if not (is_pass or is_rush):
            return  # kickoffs, punts, field goals, kneels and penalties-only rows
        self.turnovers += 1 if _flag(row["interception"]) or _flag(row["fumble_lost"]) else 0
        self.sacks_taken += 1 if _flag(row["sack"]) else 0
        epa = _number(row["epa"])
        if epa is None:
            return
        self.plays += 1
        self.epa += epa
        self.success += _number(row["success"]) or 0.0
        self.yards += _number(row["yards_gained"]) or 0.0
        if is_pass:
            self.pass_plays += 1
            self.pass_epa += epa
        else:
            self.rush_plays += 1
            self.rush_epa += epa

    def stats(self) -> dict[str, float]:
        return {
            "plays": self.plays, "epa": self.epa, "success": self.success, "yards": self.yards,
            "pass_plays": self.pass_plays, "pass_epa": self.pass_epa,
            "rush_plays": self.rush_plays, "rush_epa": self.rush_epa,
            "turnovers": self.turnovers, "sacks_taken": self.sacks_taken,
        }


class _Game:
    def __init__(self, row: dict[str, str]) -> None:
        self.season = int(row["season"])
        self.season_type = row["season_type"]
        self.week = int(row["week"])
        self.home, self.away = row["home_team"], row["away_team"]
        self.offense = {self.home: _Offense(), self.away: _Offense()}
        self.halftime: Optional[tuple[int, int]] = None
        self.first_scorer: Optional[str] = None

    def add(self, row: dict[str, str]) -> None:
        home_score = int(_number(row["total_home_score"]) or 0)
        away_score = int(_number(row["total_away_score"]) or 0)
        if self.first_scorer is None and (home_score or away_score):
            self.first_scorer = self.home if home_score else self.away
        half = row["game_half"]
        if half == "Half2" and self.halftime is None:
            self.halftime = (home_score, away_score)
        team = row["posteam"]
        if half == "Half1" and team in self.offense:
            self.offense[team].add(row)

    def records(self) -> list[HalfRecord]:
        if self.halftime is None:
            return []
        home_1h, away_1h = self.halftime
        out = []
        for team, opponent, mine, theirs in (
            (self.home, self.away, home_1h, away_1h),
            (self.away, self.home, away_1h, home_1h),
        ):
            out.append(
                HalfRecord(
                    season=self.season, season_type=self.season_type, week=self.week,
                    team=team, opponent=opponent, points_for_1h=mine, points_against_1h=theirs,
                    scored_first=None if self.first_scorer is None else self.first_scorer == team,
                    stats=self.offense[team].stats(),
                )
            )
        return out


def parse_pbp_halves(lines: Iterable[str]) -> list[HalfRecord]:
    """`lines` is an open text file or any iterable of CSV lines (header first)."""
    reader = csv.DictReader(lines)
    games: dict[str, _Game] = {}
    for row in reader:
        if row["season_type"] not in SEASON_TYPES or not row["game_id"]:
            continue
        game = games.get(row["game_id"])
        if game is None:
            game = games[row["game_id"]] = _Game(row)
        game.add({name: row.get(name, "") for name in COLUMNS})
    return [record for game in games.values() for record in game.records()]
