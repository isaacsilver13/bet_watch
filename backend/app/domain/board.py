from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.domain.lines import BestView, BookLine, ConsensusView, Coverage
from app.domain.slate import SlateGame, WeekRef


class BookRef(BaseModel):
    book: str
    venue: Optional[str]


class BoardMovement(BaseModel):
    """Book consensus at the first saved snapshot, so it can sit beside the latest one."""

    first_at: datetime
    spread_home: Optional[float]
    total: Optional[float]
    snapshots: int


class BoardMarket(BaseModel):
    snapshot_at: Optional[datetime]  # newest pregame snapshot for this game
    books: list[BookLine]  # the books in scope, from that one snapshot
    snapshot_books: list[BookRef]  # every book in that snapshot, before any book filter
    consensus: Optional[ConsensusView]  # book-derived no-vig, over the books in scope
    best: dict[str, BestView]  # same line only; sides without a comparable quote are absent
    coverage: dict[str, Coverage]  # spread | total | moneyline
    movement: Optional[BoardMovement]


class ContextFlag(BaseModel):
    kind: str  # qb | short_week | off_bye
    team: str
    text: str
    as_of: Optional[datetime]


class BoardGame(BaseModel):
    game: SlateGame
    market: BoardMarket
    flags: list[ContextFlag]


class BoardResponse(BaseModel):
    sport: str
    season: int
    season_type: str
    week: int
    weeks: list[WeekRef]
    snapshot_at: Optional[datetime]  # newest snapshot across the week
    books_available: list[BookRef]
    books_selected: list[str]  # venue keys in scope; empty means every book
    can_refresh: bool
    games: list[BoardGame]
