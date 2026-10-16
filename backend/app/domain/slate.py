from datetime import date, datetime
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


class WeatherView(BaseModel):
    temp_f: Optional[float]
    wind_mph: Optional[float]
    precip_pct: Optional[float]
    conditions: Optional[str]
    fetched_at: datetime


class StarFlagView(BaseModel):
    out: list[str]  # stars who are Out or Doubtful
    questionable: list[str]


class SlateStars(BaseModel):
    home: StarFlagView
    away: StarFlagView


class BetBadge(BaseModel):
    total: int
    open: int
    net: Optional[float]  # profit after fees over settled bets; null while none is settled


class SlateGame(BaseModel):
    id: int
    sport: str
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
    roof: Optional[str]  # outdoors | dome | retractable
    neutral: bool
    lines: dict[str, LineView]
    spread_move: Optional[float]
    total_move: Optional[float]
    odds_event_id: Optional[str]
    weather: Optional[WeatherView] = None  # outdoor games only
    star: Optional[SlateStars] = None  # an upcoming NBA game with a star out or questionable
    bets: Optional[BetBadge] = None  # present only when the game carries a bet


class WeekRef(BaseModel):
    season_type: str
    week: int


class DaySlateResponse(BaseModel):
    """A sport with no weeks (the NBA): one Eastern calendar day of games."""

    sport: str
    date: date
    previous: Optional[date]  # the nearest earlier day with games
    next: Optional[date]  # the nearest later day with games
    games: list[SlateGame]


class SlateResponse(BaseModel):
    sport: str
    season: int
    season_type: str
    week: int
    weeks: list[WeekRef]
    games: list[SlateGame]
