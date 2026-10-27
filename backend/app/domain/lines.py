from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.domain.slate import LineView


class BookLine(BaseModel):
    book: str
    venue: Optional[str]  # the bet-form venue for this book, if it is one we track
    observed_at: datetime
    spread_home: Optional[float] = None
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    ml_home: Optional[int] = None
    ml_away: Optional[int] = None


class BestView(BaseModel):
    book: str
    venue: Optional[str]
    price: int
    line: Optional[float]  # the number that price is for, from that side's point of view


class ConsensusView(BaseModel):
    spread_home: Optional[float]
    total: Optional[float]
    home_cover_prob: Optional[float]
    over_prob: Optional[float]
    home_win_prob: Optional[float]
    books: int


class Coverage(BaseModel):
    """How many of the books in scope quote a market, and how many do so at the consensus line."""

    quoting: int
    at_line: int


class HistoryPoint(BaseModel):
    observed_at: datetime
    spread_home: Optional[float]
    total: Optional[float]
    home_win_prob: Optional[float]


class LinesResponse(BaseModel):
    game_id: int
    event_id: Optional[str]
    reference: dict[str, LineView]  # ESPN (else nflverse) open / current / close
    books: list[BookLine]  # latest snapshot only
    best: dict[str, BestView]
    consensus: Optional[ConsensusView]
    coverage: dict[str, Coverage] = {}  # spread | total | moneyline
    history: list[HistoryPoint]  # one consensus per snapshot, oldest first
    snapshot_at: Optional[datetime]
    quota_remaining: Optional[int]  # shown only to an admin
    can_refresh: bool


class RefreshResponse(BaseModel):
    refreshed: bool
    reason: Optional[str] = None  # recent | quota | throttled when nothing was fetched
    state: Optional[str] = None  # done | partial | empty
    snapshot_at: Optional[datetime] = None
    quota_remaining: Optional[int] = None
