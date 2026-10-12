from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TeamView(BaseModel):
    id: int
    abbr: str
    name: str


class LineView(BaseModel):
    source: str
    spread_home: Optional[float] = None
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    ml_home: Optional[int] = None
    ml_away: Optional[int] = None
    captured_at: datetime


class SlateGame(BaseModel):
    id: int
    espn_id: str
    season: int
    season_type: str
    week: Optional[int]
    start_time: datetime
    status: str
    home: TeamView
    away: TeamView
    home_score: Optional[int]
    away_score: Optional[int]
    venue: Optional[str]
    neutral: bool
    lines: dict[str, LineView]
    spread_move: Optional[float]
    total_move: Optional[float]
    odds_event_id: Optional[str]


class WeekRef(BaseModel):
    season_type: str
    week: int


class SlateResponse(BaseModel):
    sport: str
    season: int
    season_type: str
    week: int
    weeks: list[WeekRef]
    games: list[SlateGame]
