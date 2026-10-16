"""Source-neutral game and line records that every ingest parser produces."""

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class LineRecord:
    kind: str  # open | current | close
    spread_home: Optional[float] = None  # home team's line; negative = home favored
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    ml_home: Optional[int] = None
    ml_away: Optional[int] = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self) if f.name != "kind")


@dataclass(frozen=True)
class GameRecord:
    espn_id: str
    season: int
    season_type: str  # PRE | REG | POST
    week: Optional[int]
    start_time: datetime
    status: str  # scheduled | in_progress | final | postponed | canceled
    home_name: str  # any alias (abbr, full name); resolved via team_aliases
    away_name: str
    home_score: Optional[int]
    away_score: Optional[int]
    neutral: bool
    venue_espn_id: Optional[str] = None
    venue_name: Optional[str] = None
    venue_indoor: Optional[bool] = None
    home_qb: Optional[str] = None  # nflverse starter, once the game is played
    away_qb: Optional[str] = None
    lines: tuple[LineRecord, ...] = ()


@dataclass(frozen=True)
class InjuryRecord:
    team_espn_id: str
    player: str
    position: Optional[str]
    status: str  # Out | Doubtful | Questionable | Injured Reserve | Suspension | ...
    detail: Optional[str]
    reported_at: Optional[datetime]


@dataclass(frozen=True)
class NamedInjury:
    """An injury from a report that names the team (an alias: a full name, an nflverse code)
    instead of carrying ESPN's team id."""

    team: str
    player: str
    position: Optional[str]
    status: str  # Out | Doubtful | Questionable | Probable
    detail: Optional[str]
    reported_at: Optional[datetime] = None
