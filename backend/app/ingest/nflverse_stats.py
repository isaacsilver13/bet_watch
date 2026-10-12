"""nflverse weekly team stats (stats_team_week_<season>.csv, CC-BY-4.0): one row per team per game.

Verified 2026-10-12: one file per season; `week` keeps counting through the postseason (19-22),
`season_type` is REG or POST, team codes are nflverse's (Rams are LA, Raiders LV). The file has
EPA for passing and rushing but no play count or success rate, so plays are approximated later.
"""

import csv
import io
from dataclasses import dataclass
from typing import Optional

TEAM_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/stats_team/"
    "stats_team_week_{season}.csv"
)
SEASON_TYPES = {"REG", "POST"}
# The curated columns we keep; the file has 138 and most are never used.
STAT_FIELDS = (
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "sack_fumbles_lost",
    "passing_first_downs",
    "passing_epa",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_fumbles_lost",
    "rushing_first_downs",
    "rushing_epa",
    "receiving_fumbles_lost",
    "receiving_first_downs",
    "def_sacks",
    "def_interceptions",
    "def_qb_hits",
    "penalties",
    "penalty_yards",
)


@dataclass(frozen=True)
class TeamStatRecord:
    season: int
    season_type: str  # REG | POST
    week: int  # nflverse numbering: the postseason continues after week 18
    team: str  # nflverse code, resolved through team_aliases
    opponent: str
    stats: dict[str, float]


def _number(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip() in {"", "NA"}:
        return None
    return float(value)


def parse_team_stats_csv(text: str) -> list[TeamStatRecord]:
    records = []
    for row in csv.DictReader(io.StringIO(text)):
        if row["season_type"] not in SEASON_TYPES:
            continue
        numbers = {name: _number(row.get(name)) for name in STAT_FIELDS}
        records.append(
            TeamStatRecord(
                season=int(row["season"]),
                season_type=row["season_type"],
                week=int(row["week"]),
                team=row["team"],
                opponent=row["opponent_team"],
                stats={name: value for name, value in numbers.items() if value is not None},
            )
        )
    return records
