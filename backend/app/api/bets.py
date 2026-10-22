"""Log bets, edit open ones, and read profit and closing-line value."""

from dataclasses import asdict
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.analytics.bet_stats import summarize
from app.analytics.clv import ratio_clv
from app.analytics.grading import profit
from app.analytics.picks import PickFacts, track_record
from app.api.slate import get_now, get_session_factory
from app.domain.bets import (
    BetGameView,
    BetIn,
    BetPatch,
    BetView,
    ModelPicksResponse,
    ModelPickView,
    SummaryResponse,
    SummaryRowView,
    TrackRecordView,
)
from app.storage.bets_repository import (
    OPEN,
    BetNotFound,
    BetNotOpen,
    BetRow,
    BetsRepository,
    GameNotFound,
)
from app.storage.games_repository import aware
from app.storage.models import Bet, Game, ModelPick, Team

router = APIRouter(prefix="/api/v1")
Group = Literal["venue", "sport", "market"]


def _game_view(game: Game, home: str, away: str) -> BetGameView:
    return BetGameView(
        id=game.id,
        away=away,
        home=home,
        season_type=game.season_type,
        week=game.week,
        start_time=aware(game.start_time),
        status=game.status,
        away_score=game.away_score,
        home_score=game.home_score,
    )


def _view(row: BetRow) -> BetView:
    bet, game = row.bet, row.game
    graded = bet.status != OPEN and bet.payout is not None
    return BetView(
        id=bet.id,
        game=_game_view(game, row.home, row.away),
        placed_at=aware(bet.placed_at),
        venue=bet.venue,
        market=bet.market,
        selection=bet.selection,
        line=bet.line,
        price_american=bet.price_american,
        stake=bet.stake,
        fee=bet.fee,
        status=bet.status,
        payout=bet.payout,
        profit=profit(bet.payout, bet.stake, bet.fee) if graded else None,
        settled_at=aware(bet.settled_at) if bet.settled_at else None,
        close_line=bet.close_line,
        close_price=bet.close_price,
        close_source=bet.close_source,
        clv_points=bet.clv_points,
        clv_prob=bet.clv_prob,
        clv_pct=(
            ratio_clv(bet.price_american, bet.close_price)
            if bet.market == "moneyline" and bet.close_price is not None
            else None
        ),
        note=bet.note,
    )


def _repo(session_factory: async_sessionmaker[AsyncSession]) -> BetsRepository:
    return BetsRepository(session_factory)


@router.post("/bets", response_model=BetView, status_code=201)
async def create_bet(
    data: BetIn,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> BetView:
    repo = _repo(session_factory)
    try:
        bet_id = await repo.create(data, now)
    except GameNotFound as error:
        raise HTTPException(404, detail="Game not found") from error
    return _view(await repo.get(bet_id))


@router.get("/bets", response_model=list[BetView])
async def list_bets(
    status: Literal["open", "settled", "all"] = Query("all"),
    game_id: Optional[int] = Query(None),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[BetView]:
    conditions = []
    if status == "open":
        conditions.append(Bet.status == OPEN)
    elif status == "settled":
        conditions.append(Bet.status != OPEN)
    if game_id is not None:
        conditions.append(Game.id == game_id)
    return [_view(row) for row in await _repo(session_factory).rows(*conditions)]


@router.get("/bets/summary", response_model=SummaryResponse)
async def bets_summary(
    group_by: Group = Query("venue"),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> SummaryResponse:
    total, groups = summarize(await _repo(session_factory).facts(), group_by)
    return SummaryResponse(
        group_by=group_by,
        total=SummaryRowView(**asdict(total)),
        groups=[SummaryRowView(**asdict(g)) for g in groups],
    )


@router.patch("/bets/{bet_id}", response_model=BetView)
async def patch_bet(
    bet_id: int,
    data: BetPatch,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> BetView:
    repo = _repo(session_factory)
    try:
        await repo.patch(bet_id, data)
    except BetNotFound as error:
        raise HTTPException(404, detail="Bet not found") from error
    except BetNotOpen as error:
        raise HTTPException(409, detail="Only open bets can be changed") from error
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    return _view(await repo.get(bet_id))


@router.get("/model-picks", response_model=ModelPicksResponse)
async def model_picks(
    limit: int = Query(100, ge=1, le=1000),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> ModelPicksResponse:
    """The NBA model's picks: each tier's running record over every pick, and the newest picks."""
    home, away = aliased(Team), aliased(Team)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ModelPick, Game, home.abbr, away.abbr)
                .join(Game, ModelPick.game_id == Game.id)
                .join(home, Game.home_team_id == home.id)
                .join(away, Game.away_team_id == away.id)
                .order_by(ModelPick.slate_date.desc(), ModelPick.id.desc())
            )
        ).all()
    record = track_record([PickFacts(p.tier, p.price_american, p.result, p.clv) for p, *_ in rows])
    picks = [
        ModelPickView(
            game=_game_view(game, home_abbr, away_abbr),
            **{name: getattr(pick, name) for name in ModelPickView.model_fields if name != "game"},
        )
        for pick, game, home_abbr, away_abbr in rows[:limit]
    ]
    return ModelPicksResponse(record=[TrackRecordView(**asdict(r)) for r in record], picks=picks)
