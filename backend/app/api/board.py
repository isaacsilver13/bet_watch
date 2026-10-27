"""The NFL week board: every game's per-book prices and context flags in one read-only call.

It reads only what the scheduled jobs and manual refreshes already stored. Opening it never
calls the odds provider.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.injuries import InjuredPlayer, qb_flag
from app.analytics.market import (
    SideOffer,
    best_prices,
    book_quotes,
    consensus,
    consensus_history,
)
from app.analytics.situational import Stop, situation
from app.api import routes
from app.api.lines import HISTORY_DAYS, Runtime, _best, _coverage, get_runtime
from app.api.matchup import COUNTED_SEASON_TYPES
from app.api.slate import get_now, get_session_factory, week_slate
from app.domain.board import (
    BoardGame,
    BoardMarket,
    BoardMovement,
    BoardResponse,
    BookRef,
    ContextFlag,
)
from app.domain.lines import BookLine, ConsensusView
from app.domain.slate import SlateGame
from app.domain.venues import VENUES, venue_for_book
from app.storage.context_repository import merged_injuries
from app.storage.games_repository import aware
from app.storage.lines_repository import side_offers_for_games
from app.storage.models import Game

router = APIRouter(prefix="/api/v1")


def _market(
    game: SlateGame, offers: list[SideOffer], now: datetime, venues: set[str]
) -> BoardMarket:
    # The odds feed also returns games in progress: stop at kickoff once a game has started.
    if now >= game.start_time:
        offers = [o for o in offers if o.observed_at <= game.start_time]
    latest = max((o.observed_at for o in offers), default=None)
    snapshot = book_quotes(o for o in offers if o.observed_at == latest)
    quotes = [q for q in snapshot if not venues or venue_for_book(q.book) in venues]
    middle = consensus(quotes)
    history = consensus_history(offers)
    return BoardMarket(
        snapshot_at=latest,
        books=[BookLine(venue=venue_for_book(q.book), **vars(q)) for q in quotes],
        snapshot_books=[BookRef(book=q.book, venue=venue_for_book(q.book)) for q in snapshot],
        consensus=ConsensusView(**vars(middle)) if quotes else None,
        # Same rule as the game page: only books we can log a bet at can be "best".
        best=_best(best_prices([q for q in quotes if venue_for_book(q.book)], middle)),
        coverage=_coverage(quotes, middle),
        movement=None if not history else BoardMovement(
            first_at=history[0][0], spread_home=history[0][1].spread_home,
            total=history[0][1].total, snapshots=len(history),
        ),
    )


async def _flags(
    session: AsyncSession, games: list[SlateGame], now: datetime
) -> dict[int, list[ContextFlag]]:
    """Starting-QB and rest flags for games still to be played, from two batched queries."""
    upcoming = [g for g in games if g.status == "scheduled" and g.start_time > now]
    if not upcoming:
        return {}
    sport = upcoming[0].sport
    teams = {t.id for g in upcoming for t in (g.home, g.away)}
    injuries, injuries_at = await merged_injuries(session, sport, teams, now)
    prior: dict[int, list[Game]] = defaultdict(list)  # team -> its finished games, newest first
    finished = await session.scalars(
        select(Game)
        .where(
            Game.sport == sport,
            Game.status == "final",
            Game.season_type.in_(COUNTED_SEASON_TYPES),
            Game.start_time < max(g.start_time for g in upcoming),
            or_(Game.home_team_id.in_(teams), Game.away_team_id.in_(teams)),
        )
        .order_by(Game.start_time.desc())
    )
    for played in finished.all():
        prior[played.home_team_id].append(played)
        prior[played.away_team_id].append(played)

    out: dict[int, list[ContextFlag]] = {}
    for game in upcoming:
        flags = []
        for team in (game.away, game.home):
            last = next((p for p in prior[team.id] if aware(p.start_time) < game.start_time), None)
            last_qb = None
            if last is not None:
                last_qb = last.home_qb if last.home_team_id == team.id else last.away_qb
            injured = [InjuredPlayer(i.player, i.position, i.status) for i in injuries[team.id]]
            if flag := qb_flag(last_qb, injured):
                flags.append(ContextFlag(
                    kind="qb", team=team.abbr, as_of=injuries_at,
                    text=f"{team.abbr} QB {flag.player} listed {flag.status}",
                ))
            stop = None
            if last is not None:
                stop = Stop(aware(last.start_time), last.season, None, None, None)
            spot = situation(stop, game.start_time, game.season, None, None, None)
            if spot.short_week or spot.off_bye:
                what = "short week" if spot.short_week else "off a bye"
                flags.append(ContextFlag(
                    kind="short_week" if spot.short_week else "off_bye", team=team.abbr,
                    as_of=None, text=f"{team.abbr} {what} ({spot.rest_days} days rest)",
                ))
        out[game.id] = flags
    return out


@router.get("/board", response_model=BoardResponse)
async def get_board(
    request: Request,
    sport: str = Query("nfl", pattern="^nfl$"),
    season: Optional[int] = Query(None, ge=2000, le=2100),
    season_type: Optional[str] = Query(None, pattern="^(PRE|REG|POST)$"),
    week: Optional[int] = Query(None, ge=1, le=25),
    books: Optional[str] = Query(None, description="Comma-separated venue keys to compare"),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    runtime: Runtime = Depends(get_runtime),
    now: datetime = Depends(get_now),
) -> BoardResponse:
    venues = {key for key in (books or "").lower().split(",") if key}
    unknown = venues - set(VENUES)
    if unknown:
        raise HTTPException(422, detail=f"Unknown book: {', '.join(sorted(unknown))}")
    async with session_factory() as session:
        slate = await week_slate(session, sport, season, season_type, week, now)
        rows = (
            await session.scalars(select(Game).where(Game.id.in_([g.id for g in slate.games])))
        ).all()
        by_id = {row.id: row for row in rows}
        offers = await side_offers_for_games(
            session, [by_id[g.id] for g in slate.games], since=now - timedelta(days=HISTORY_DAYS)
        )
        flags = await _flags(session, slate.games, now)

    games = [
        BoardGame(game=g, market=_market(g, offers[g.id], now, venues), flags=flags.get(g.id, []))
        for g in slate.games
    ]
    seen = {(b.book, b.venue) for g in games for b in g.market.snapshot_books}
    snapshots = [g.market.snapshot_at for g in games if g.market.snapshot_at]
    return BoardResponse(
        sport=slate.sport, season=slate.season, season_type=slate.season_type, week=slate.week,
        weeks=slate.weeks,
        snapshot_at=max(snapshots, default=None),
        books_available=[BookRef(book=b, venue=v) for b, v in sorted(seen, key=str)],
        books_selected=sorted(venues),
        can_refresh=runtime.odds is not None and routes._may_spend(request),
        games=games,
    )
