"""Week slate: games with reference lines (ESPN, else nflverse) and movement since open."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import load_settings
from app.domain.slate import LineView, SlateGame, SlateResponse, TeamView, WeekRef
from app.storage.database import create_engine, create_session_factory
from app.storage.games_repository import LINE_FIELDS, aware
from app.storage.models import Game, GameLine, Team, Venue

router = APIRouter(prefix="/api/v1")
SOURCE_PRIORITY = ("espn", "nflverse")
SEASON_TYPE_ORDER = {"PRE": 0, "REG": 1, "POST": 2}
NO_DATA = (
    "No games loaded yet. Run `python -m app.jobs seed`, `python -m app.jobs nfl_history` "
    "and `python -m app.jobs espn_scoreboard`."
)


@lru_cache(maxsize=1)
def _default_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    settings = load_settings()
    if not settings.persistence_enabled:
        return None
    return create_session_factory(create_engine(settings))


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    factory = _default_session_factory()
    if factory is None:
        raise HTTPException(503, detail="Matchup data needs PERSISTENCE_ENABLED=true and Postgres.")
    return factory


def get_now() -> datetime:
    return datetime.now(timezone.utc)


def pick_lines(rows: Sequence[GameLine]) -> dict[str, GameLine]:
    rank = {source: index for index, source in enumerate(SOURCE_PRIORITY)}
    chosen: dict[str, GameLine] = {}
    for row in sorted(rows, key=lambda r: rank.get(r.source, len(rank))):
        chosen.setdefault(row.kind, row)
    return chosen


def movement(lines: dict[str, GameLine], field: str) -> Optional[float]:
    latest = lines.get("close") or lines.get("current")
    opener = lines.get("open")
    if latest is None or opener is None:
        return None
    now_value, open_value = getattr(latest, field), getattr(opener, field)
    if now_value is None or open_value is None:
        return None
    return round(now_value - open_value, 2)


async def resolve_week(
    session: AsyncSession, sport: str, now: datetime
) -> Optional[tuple[int, str, int]]:
    columns = select(Game.season, Game.season_type, Game.week).where(
        Game.sport == sport, Game.week.is_not(None)
    )
    row = (
        await session.execute(
            columns.where(Game.start_time >= now - timedelta(hours=12))
            .order_by(Game.start_time)
            .limit(1)
        )
    ).first()
    if row is None:
        row = (await session.execute(columns.order_by(Game.start_time.desc()).limit(1))).first()
    return (row[0], row[1], row[2]) if row else None


@router.get("/slate", response_model=SlateResponse)
async def get_slate(
    sport: str = Query("nfl", pattern="^nfl$"),
    season: Optional[int] = Query(None, ge=2000, le=2100),
    season_type: Optional[str] = Query(None, pattern="^(REG|POST)$"),
    week: Optional[int] = Query(None, ge=1, le=25),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> SlateResponse:
    async with session_factory() as session:
        if season is None or week is None:
            resolved = await resolve_week(session, sport, now)
            if resolved is None:
                raise HTTPException(404, detail=NO_DATA)
            season, season_type, week = resolved
        season_type = season_type or "REG"

        week_rows = (
            await session.execute(
                select(Game.season_type, Game.week)
                .where(Game.sport == sport, Game.season == season, Game.week.is_not(None))
                .distinct()
            )
        ).all()
        weeks = sorted(
            (WeekRef(season_type=kind, week=number) for kind, number in week_rows),
            key=lambda ref: (SEASON_TYPE_ORDER.get(ref.season_type, 9), ref.week),
        )

        home, away = aliased(Team), aliased(Team)
        rows = (
            await session.execute(
                select(Game, home, away, Venue.name)
                .join(home, Game.home_team_id == home.id)
                .join(away, Game.away_team_id == away.id)
                .outerjoin(Venue, Game.venue_id == Venue.id)
                .where(
                    Game.sport == sport,
                    Game.season == season,
                    Game.season_type == season_type,
                    Game.week == week,
                )
                .order_by(Game.start_time, Game.id)
            )
        ).all()
        game_ids = [row[0].id for row in rows]
        line_rows = (
            (await session.scalars(select(GameLine).where(GameLine.game_id.in_(game_ids)))).all()
            if game_ids
            else []
        )

    lines_by_game: dict[int, list[GameLine]] = defaultdict(list)
    for line in line_rows:
        lines_by_game[line.game_id].append(line)

    games = []
    for game, home_team, away_team, venue_name in rows:
        lines = pick_lines(lines_by_game[game.id])
        games.append(
            SlateGame(
                id=game.id,
                espn_id=game.espn_id,
                season=game.season,
                season_type=game.season_type,
                week=game.week,
                start_time=aware(game.start_time),
                status=game.status,
                home=TeamView(id=home_team.id, abbr=home_team.abbr, name=home_team.name),
                away=TeamView(id=away_team.id, abbr=away_team.abbr, name=away_team.name),
                home_score=game.home_score,
                away_score=game.away_score,
                venue=venue_name,
                neutral=game.neutral,
                lines={
                    kind: LineView(
                        source=row.source,
                        captured_at=aware(row.captured_at),
                        **{name: getattr(row, name) for name in LINE_FIELDS},
                    )
                    for kind, row in lines.items()
                },
                spread_move=movement(lines, "spread_home"),
                total_move=movement(lines, "total"),
                odds_event_id=game.event_id,
            )
        )
    return SlateResponse(
        sport=sport, season=season, season_type=season_type, week=week, weeks=weeks, games=games
    )
