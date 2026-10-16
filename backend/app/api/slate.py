"""Week slate: games with reference lines (ESPN, else nflverse) and movement since open."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import load_settings
from app.domain.slate import (
    DaySlateResponse,
    LineView,
    SlateGame,
    SlateResponse,
    TeamView,
    WeatherView,
    WeekRef,
)
from app.storage.badges import bet_badges, slate_stars
from app.storage.database import create_engine, create_session_factory
from app.storage.games_repository import LINE_FIELDS, aware
from app.storage.models import Game, GameLine, Team, Venue, Weather

router = APIRouter(prefix="/api/v1")
SOURCE_PRIORITY = ("espn", "nflverse")
SEASON_TYPE_ORDER = {"PRE": 0, "REG": 1, "POST": 2}
EASTERN = ZoneInfo("America/New_York")  # an NBA "day" is the US Eastern date, as ESPN has it
NO_NBA_DATA = (
    "No NBA games loaded yet. Run `python -m app.jobs seed`, then `python -m app.jobs nba_history`."
)
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


async def load_slate_games(
    session: AsyncSession, *conditions: ColumnElement, now: Optional[datetime] = None
) -> list[SlateGame]:
    """Games matching `conditions`, in kickoff order, with lines, venue roof, weather and the
    star and bet badges."""
    home, away = aliased(Team), aliased(Team)
    rows = (
        await session.execute(
            select(Game, home, away, Venue.name, Venue.roof)
            .join(home, Game.home_team_id == home.id)
            .join(away, Game.away_team_id == away.id)
            .outerjoin(Venue, Game.venue_id == Venue.id)
            .where(*conditions)
            .order_by(Game.start_time, Game.id)
        )
    ).all()
    game_ids = [row[0].id for row in rows]
    lines_by_game: dict[int, list[GameLine]] = defaultdict(list)
    weather_by_game: dict[int, Weather] = {}
    if game_ids:
        for line in (
            await session.scalars(select(GameLine).where(GameLine.game_id.in_(game_ids)))
        ).all():
            lines_by_game[line.game_id].append(line)
        for weather in (
            await session.scalars(select(Weather).where(Weather.game_id.in_(game_ids)))
        ).all():
            weather_by_game[weather.game_id] = weather
    badges = await bet_badges(session, game_ids)
    flagged = await slate_stars(session, [row[0] for row in rows], now or get_now())

    games = []
    for game, home_team, away_team, venue_name, venue_roof in rows:
        lines = pick_lines(lines_by_game[game.id])
        weather = weather_by_game.get(game.id)
        games.append(
            SlateGame(
                id=game.id,
                sport=game.sport,
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
                roof=venue_roof,
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
                weather=None
                if weather is None
                else WeatherView(
                    temp_f=weather.temp_f,
                    wind_mph=weather.wind_mph,
                    precip_pct=weather.precip_pct,
                    conditions=weather.conditions,
                    fetched_at=aware(weather.fetched_at),
                ),
                star=flagged.get(game.id),
                bets=badges.get(game.id),
            )
        )
    return games


@router.get("/slate", response_model=SlateResponse)
async def get_slate(
    sport: str = Query("nfl", pattern="^nfl$"),
    season: Optional[int] = Query(None, ge=2000, le=2100),
    season_type: Optional[str] = Query(None, pattern="^(PRE|REG|POST)$"),
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
        games = await load_slate_games(
            session,
            Game.sport == sport,
            Game.season == season,
            Game.season_type == season_type,
            Game.week == week,
            now=now,
        )
    return SlateResponse(
        sport=sport, season=season, season_type=season_type, week=week, weeks=weeks, games=games
    )


def _eastern_day(value: datetime) -> date:
    return aware(value).astimezone(EASTERN).date()


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC bounds of an Eastern calendar day (UTC so SQLite and Postgres agree)."""
    start = datetime.combine(day, time(0), EASTERN).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0), EASTERN).astimezone(timezone.utc)
    return start, end


async def resolve_day(session: AsyncSession, sport: str, now: datetime) -> Optional[date]:
    """Today if it has games, else the next day with games, else the last day with games."""
    first = await session.scalar(
        select(Game.start_time)
        .where(Game.sport == sport, Game.start_time >= now - timedelta(hours=6))
        .order_by(Game.start_time)
        .limit(1)
    )
    if first is None:
        first = await session.scalar(
            select(Game.start_time)
            .where(Game.sport == sport)
            .order_by(Game.start_time.desc())
            .limit(1)
        )
    return _eastern_day(first) if first is not None else None


@router.get("/slate/days", response_model=DaySlateResponse)
async def get_day_slate(
    sport: str = Query("nba", pattern="^nba$"),
    day: Optional[date] = Query(None, alias="date"),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> DaySlateResponse:
    async with session_factory() as session:
        if day is None:
            day = await resolve_day(session, sport, now)
            if day is None:
                raise HTTPException(404, detail=NO_NBA_DATA)
        start, end = _day_bounds(day)
        earlier = await session.scalar(
            select(Game.start_time)
            .where(Game.sport == sport, Game.start_time < start)
            .order_by(Game.start_time.desc())
            .limit(1)
        )
        later = await session.scalar(
            select(Game.start_time)
            .where(Game.sport == sport, Game.start_time >= end)
            .order_by(Game.start_time)
            .limit(1)
        )
        games = await load_slate_games(
            session, Game.sport == sport, Game.start_time >= start, Game.start_time < end, now=now
        )
    return DaySlateResponse(
        sport=sport,
        date=day,
        previous=_eastern_day(earlier) if earlier is not None else None,
        next=_eastern_day(later) if later is not None else None,
        games=games,
    )
