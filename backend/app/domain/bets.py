from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Market = Literal["spread", "total", "moneyline"]
Selection = Literal["home", "away", "over", "under"]
Venue = Literal["draftkings", "fanduel", "betmgm", "betrivers", "espnbet", "other"]
SIDES = {"spread": ("home", "away"), "moneyline": ("home", "away"), "total": ("over", "under")}


def _check_price(value: int) -> int:
    if abs(value) < 100:
        raise ValueError("American odds are at least +100 or -100")
    return value


class BetIn(BaseModel):
    game_id: int
    venue: Venue
    market: Market
    selection: Selection
    line: Optional[float] = Field(default=None, ge=-100, le=200)  # the bet side's own number
    price_american: int = Field(ge=-10000, le=10000)
    stake: float = Field(gt=0, le=1_000_000)
    fee: Optional[float] = Field(default=None, ge=0, le=100)  # what the book charged, if known
    placed_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _consistent(self) -> "BetIn":
        _check_price(self.price_american)
        if self.selection not in SIDES[self.market]:
            raise ValueError(f"a {self.market} bet is on {' or '.join(SIDES[self.market])}")
        if self.market == "moneyline" and self.line is not None:
            raise ValueError("a moneyline bet has no line")
        if self.market != "moneyline" and self.line is None:
            raise ValueError(f"a {self.market} bet needs its line")
        return self


class BetPatch(BaseModel):
    """Open bets only. A change to the venue or stake re-works the fee unless one is given."""

    venue: Optional[Venue] = None
    line: Optional[float] = Field(default=None, ge=-100, le=200)
    price_american: Optional[int] = Field(default=None, ge=-10000, le=10000)
    stake: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    fee: Optional[float] = Field(default=None, ge=0, le=100)
    status: Optional[Literal["void"]] = None
    note: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _price(self) -> "BetPatch":
        if self.price_american is not None:
            _check_price(self.price_american)
        return self


class BetGameView(BaseModel):
    id: int
    away: str
    home: str
    season_type: str
    week: Optional[int]
    start_time: datetime
    status: str
    away_score: Optional[int]
    home_score: Optional[int]


class BetView(BaseModel):
    id: int
    game: BetGameView
    placed_at: datetime
    venue: str
    market: str
    selection: str
    line: Optional[float]
    price_american: int
    stake: float
    fee: float
    status: str
    payout: Optional[float]
    profit: Optional[float]  # after the fee; None while the bet is open
    settled_at: Optional[datetime]
    close_line: Optional[float]
    close_price: Optional[int]
    close_source: Optional[str]
    clv_points: Optional[float]
    clv_prob: Optional[float]
    clv_pct: Optional[float]  # moneyline only: p_close / p_bet - 1
    note: Optional[str]


class SummaryRowView(BaseModel):
    key: str
    bets: int
    open: int
    settled: int
    staked: float
    fees: float
    profit: float
    roi: Optional[float]
    avg_clv_points: Optional[float]
    avg_clv_prob: Optional[float]
    clv_samples: int
    beat_close_pct: Optional[float]


class SummaryResponse(BaseModel):
    group_by: str
    total: SummaryRowView
    groups: list[SummaryRowView]
