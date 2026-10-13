"""Response models for the matchup context endpoint."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.domain.slate import TeamView


class TallyView(BaseModel):
    win: int
    loss: int
    push: int


class RecordSetView(BaseModel):
    su: TallyView
    ats: TallyView
    ou: TallyView  # win = over, loss = under


class MetricView(BaseModel):
    value: float
    rank: int
    of: int


class SituationView(BaseModel):
    rest_days: Optional[int]  # days between games; the NFL's short week and bye, the NBA's b2b
    short_week: bool
    off_bye: bool
    travel_miles: Optional[int]
    tz_crossed: Optional[int]
    previous_opponent: Optional[str]
    back_to_back: bool = False  # NBA only from here down
    three_in_four: bool = False
    four_in_six: bool = False
    games_last_7: Optional[int] = None


class InjuryView(BaseModel):
    player: str
    position: Optional[str]
    status: str
    detail: Optional[str]
    reported_at: Optional[datetime]


class QbFlagView(BaseModel):
    player: str
    status: str


class TeamContext(BaseModel):
    team: TeamView
    records: dict[str, RecordSetView]  # season, last_n, home, away, favorite, underdog
    efficiency: dict[str, MetricView]
    situation: SituationView
    injuries: list[InjuryView]
    qb_flag: Optional[QbFlagView]


class MeetingView(BaseModel):
    game_id: int
    start_time: datetime
    home: str
    away: str
    home_score: int
    away_score: int
    spread_home: Optional[float]
    total: Optional[float]


class HeadToHeadView(BaseModel):
    meetings: list[MeetingView]  # oldest first
    home_su: TallyView  # from the viewed game's home team
    home_ats: TallyView


class ContextResponse(BaseModel):
    game_id: int
    sport: str
    last_n: int
    divisional: bool
    primetime: bool
    home: TeamContext
    away: TeamContext
    head_to_head: HeadToHeadView
    data_as_of: dict[str, Optional[datetime]]  # records, efficiency, situational, injuries
