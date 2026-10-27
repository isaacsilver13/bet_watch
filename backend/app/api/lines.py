"""A game's prices across books, and the manual refresh that fetches fresh ones."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market import (
    BestPrice,
    best_prices,
    book_quotes,
    consensus,
    consensus_history,
    coverage,
)
from app.api import routes
from app.api.slate import get_now, get_session_factory, load_slate_games
from app.config import load_settings
from app.domain.lines import (
    BestView,
    BookLine,
    ConsensusView,
    Coverage,
    HistoryPoint,
    LinesResponse,
    RefreshResponse,
)
from app.domain.models import Sport
from app.domain.venues import venue_for_book
from app.ingest.odds_snapshot import PROVIDER, fetch_snapshot, ingest_snapshot
from app.providers.errors import OddsProviderError
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.games_repository import aware
from app.storage.lines_repository import side_offers
from app.storage.models import Game
from app.storage.repository import OddsRepository

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
REFRESH_COOLDOWN = timedelta(minutes=10)  # one bulk call covers every game; asking again is waste
HISTORY_DAYS = 21
HISTORY_POINTS = 60


@dataclass
class Runtime:
    odds: Optional[TheOddsApiClient]  # None unless PROVIDER_MODE=live with a key
    raw_dir: Path
    min_quota: int


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    settings = load_settings()
    return Runtime(
        TheOddsApiClient.from_settings(settings),
        Path(settings.raw_data_dir),
        settings.provider_min_quota_remaining,
    )


def _best(best: dict[str, BestPrice]) -> dict[str, BestView]:
    return {
        name: BestView(book=b.book, venue=venue_for_book(b.book), price=b.price, line=b.line)
        for name, b in best.items()
    }


def _coverage(quotes, middle) -> dict[str, Coverage]:
    return {k: Coverage(quoting=q, at_line=a) for k, (q, a) in coverage(quotes, middle).items()}


async def _game_row(session: AsyncSession, game_id: int) -> Game:
    game = await session.get(Game, game_id)
    if game is None:
        raise HTTPException(404, detail="Game not found")
    return game


@router.get("/games/{game_id}/lines", response_model=LinesResponse)
async def get_lines(
    game_id: int,
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    runtime: Runtime = Depends(get_runtime),
    now: datetime = Depends(get_now),
) -> LinesResponse:
    async with session_factory() as session:
        game = await _game_row(session, game_id)
        slate_game = (await load_slate_games(session, Game.id == game_id))[0]
        kickoff = aware(game.start_time)
        # The odds feed also returns games in progress: once this game has started, the
        # prices shown stop at kickoff instead of following the live market.
        offers = await side_offers(
            session,
            game,
            since=now - timedelta(days=HISTORY_DAYS),
            until=kickoff if now >= kickoff else None,
        )
    is_admin = routes._may_spend(request)
    quota = await OddsRepository(session_factory).latest_quota(PROVIDER) if is_admin else None

    latest = max((o.observed_at for o in offers), default=None)
    quotes = book_quotes(o for o in offers if o.observed_at == latest)
    middle = consensus(quotes)
    history = consensus_history(offers)[-HISTORY_POINTS:]
    return LinesResponse(
        game_id=game_id,
        event_id=game.event_id,
        reference=slate_game.lines,
        books=[BookLine(venue=venue_for_book(q.book), **vars(q)) for q in quotes],
        # Only books mapped to a supported bet-log venue can be displayed as
        # the best actionable price. Keep every book in the raw quote list and
        # consensus for research and line movement.
        best=_best(best_prices([q for q in quotes if venue_for_book(q.book)], middle)),
        consensus=ConsensusView(**vars(middle)) if quotes else None,
        coverage=_coverage(quotes, middle),
        history=[
            HistoryPoint(
                observed_at=when,
                spread_home=point.spread_home,
                total=point.total,
                home_win_prob=point.home_win_prob,
            )
            for when, point in history
        ],
        snapshot_at=latest,
        quota_remaining=quota,
        can_refresh=runtime.odds is not None and is_admin,
    )


@router.post("/games/{game_id}/refresh", response_model=RefreshResponse)
async def refresh_game(
    game_id: int,
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    runtime: Runtime = Depends(get_runtime),
    now: datetime = Depends(get_now),
) -> RefreshResponse:
    """One paid bulk call (3 credits) that refreshes every NFL game's prices, not just this one."""
    async with session_factory() as session:
        game = await _game_row(session, game_id)
    if runtime.odds is None:
        raise HTTPException(
            409, detail="Refresh needs PROVIDER_MODE=live and THE_ODDS_API_KEY in .env."
        )
    if not routes._may_spend(request):
        raise HTTPException(403, detail="Refresh needs the admin token.")
    repo = OddsRepository(session_factory)
    sport = Sport(game.sport)
    latest = await repo.latest_snapshot_at(PROVIDER, sport)
    if latest is not None and now - aware(latest) < REFRESH_COOLDOWN:
        return RefreshResponse(refreshed=False, reason="recent", snapshot_at=aware(latest))
    if not routes._allow_expensive_refresh(request):
        return RefreshResponse(refreshed=False, reason="throttled")
    try:
        quota = await runtime.odds.fetch_quota_remaining()
    except OddsProviderError as error:
        await repo.record_refresh_failure(
            PROVIDER, now, now, error.error_type, str(error), sport,
            runtime.odds.quota_remaining,
        )
        raise HTTPException(
            502, detail=f"Unable to verify provider quota: {error.error_type}"
        ) from error
    if quota <= runtime.min_quota:
        return RefreshResponse(refreshed=False, reason="quota", quota_remaining=quota)

    kickoff = aware(game.start_time)
    slot_key = f"odds_snapshot:{sport.value}:manual:{now:%Y%m%dT%H%M%S}:{game_id}"
    try:
        path, remaining = await fetch_snapshot(runtime.odds, runtime.raw_dir, slot_key, now, sport)
    except OddsProviderError as error:
        await repo.record_refresh_failure(
            PROVIDER, now, now, error.error_type, str(error), sport, runtime.odds.quota_remaining
        )
        detail = f"The Odds API request failed: {error.error_type}"
        raise HTTPException(502, detail=detail) from error
    result = await ingest_snapshot(
        session_factory, path, (kickoff, kickoff + timedelta(minutes=1)), remaining, sport
    )
    return RefreshResponse(
        refreshed=True,
        state=result.state,
        snapshot_at=now.replace(microsecond=0),
        quota_remaining=remaining,
    )
