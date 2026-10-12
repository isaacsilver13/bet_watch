"""Upsert source-neutral game records, track missing closes, and link odds events to games."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity import normalize_name
from app.ingest.records import GameRecord, LineRecord
from app.storage.models import EventRecord, Game, GameLine, Team, TeamAlias, Venue

LINE_FIELDS = (
    "spread_home",
    "spread_home_price",
    "spread_away_price",
    "total",
    "over_price",
    "under_price",
    "ml_home",
    "ml_away",
)


def aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything we store is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class UpsertResult:
    games: int = 0
    lines: int = 0
    skipped: list[str] = field(default_factory=list)


@dataclass
class LinkResult:
    linked: int = 0
    unlinked: list[str] = field(default_factory=list)


async def alias_map(session: AsyncSession, sport: str) -> dict[str, int]:
    rows = await session.execute(
        select(TeamAlias.alias, TeamAlias.team_id).where(TeamAlias.sport == sport)
    )
    return dict(rows.all())


async def _upsert_line(
    session: AsyncSession, game_id: int, source: str, line: LineRecord, captured_at: datetime
) -> None:
    row = await session.scalar(
        select(GameLine).where(
            GameLine.game_id == game_id, GameLine.source == source, GameLine.kind == line.kind
        )
    )
    if row is not None and line.kind == "open":
        return  # the opener never changes; keep the first one seen
    if row is None:
        row = GameLine(game_id=game_id, source=source, kind=line.kind)
        session.add(row)
    for name in LINE_FIELDS:
        setattr(row, name, getattr(line, name))
    row.captured_at = captured_at


class GamesRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def upsert_games(
        self, sport: str, source: str, records: Sequence[GameRecord], captured_at: datetime
    ) -> UpsertResult:
        result = UpsertResult()
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, sport)
            home_venues = dict(
                (await session.execute(select(Team.id, Team.venue_id).where(Team.sport == sport)))
                .all()
            )
            games = {
                g.espn_id: g
                for g in (await session.scalars(select(Game).where(Game.sport == sport))).all()
            }
            venues = {
                v.espn_id: v
                for v in (await session.scalars(select(Venue).where(Venue.espn_id.is_not(None))))
                .all()
            }
            for record in records:
                home_id = aliases.get(normalize_name(record.home_name))
                away_id = aliases.get(normalize_name(record.away_name))
                if home_id is None or away_id is None:
                    result.skipped.append(record.espn_id)
                    continue
                venue_id = await self._venue_id(session, record, venues, home_venues.get(home_id))
                game = games.get(record.espn_id)
                is_new = game is None
                # ESPN owns sides, venue and neutral once a game exists; history only fills gaps.
                owns_layout = is_new or source == "espn"
                if not owns_layout and (game.home_team_id, game.away_team_id) != (home_id, away_id):
                    # Sides disagree, so its spread_home would flip sign against this game.
                    result.skipped.append(record.espn_id)
                    continue
                if game is None:
                    game = Game(espn_id=record.espn_id, sport=sport)
                    session.add(game)
                    games[record.espn_id] = game
                game.season = record.season
                game.season_type = record.season_type
                game.week = record.week
                game.start_time = record.start_time
                if owns_layout:
                    game.home_team_id = home_id
                    game.away_team_id = away_id
                    game.neutral = record.neutral
                    if venue_id is not None or is_new:
                        game.venue_id = venue_id
                if is_new or source == "espn" or record.status == "final":
                    game.status = record.status
                if record.home_score is not None:
                    game.home_score = record.home_score
                if record.away_score is not None:
                    game.away_score = record.away_score
                if record.home_qb:
                    game.home_qb = record.home_qb
                if record.away_qb:
                    game.away_qb = record.away_qb
                game.updated_at = captured_at
                await session.flush()
                for line in record.lines:
                    await _upsert_line(session, game.id, source, line, captured_at)
                    result.lines += 1
                result.games += 1
        return result

    @staticmethod
    async def _venue_id(
        session: AsyncSession,
        record: GameRecord,
        venues: dict[str, Venue],
        home_venue_id: Optional[int],
    ) -> Optional[int]:
        if record.venue_espn_id:
            venue = venues.get(record.venue_espn_id)
            if venue is None:
                roof = None
                if record.venue_indoor is not None:
                    roof = "dome" if record.venue_indoor else "outdoors"
                venue = Venue(
                    espn_id=record.venue_espn_id,
                    name=record.venue_name or "Unknown venue",
                    roof=roof,
                )
                session.add(venue)
                await session.flush()
                venues[record.venue_espn_id] = venue
            return venue.id
        return None if record.neutral else home_venue_id

    async def save_lines(
        self, espn_id: str, source: str, lines: Sequence[LineRecord], captured_at: datetime
    ) -> bool:
        async with self.session_factory() as session, session.begin():
            game_id = await session.scalar(select(Game.id).where(Game.espn_id == espn_id))
            if game_id is None:
                return False
            for line in lines:
                await _upsert_line(session, game_id, source, line, captured_at)
        return True

    async def finals_missing_close(self, sport: str, since: datetime) -> list[str]:
        has_close = select(GameLine.game_id).where(
            GameLine.source == "espn", GameLine.kind == "close"
        )
        async with self.session_factory() as session:
            rows = await session.scalars(
                select(Game.espn_id)
                .where(
                    Game.sport == sport,
                    Game.status == "final",
                    Game.start_time >= since,
                    Game.id.not_in(has_close),
                )
                .order_by(Game.start_time)
            )
            return list(rows.all())

    async def link_events(
        self, sport: str, max_gap: timedelta = timedelta(hours=36)
    ) -> LinkResult:
        result = LinkResult()
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, sport)
            linked = select(Game.event_id).where(Game.event_id.is_not(None))
            events = (
                await session.scalars(
                    select(EventRecord)
                    .where(EventRecord.sport == sport, EventRecord.id.not_in(linked))
                    .order_by(EventRecord.start_time)
                )
            ).all()
            for event in events:
                home_id = aliases.get(normalize_name(event.home_team))
                away_id = aliases.get(normalize_name(event.away_team))
                matches = []
                if home_id is not None and away_id is not None:
                    # Some feeds flip home/away for neutral-site games, so accept either order.
                    candidates = (
                        await session.scalars(
                            select(Game).where(
                                Game.sport == sport,
                                Game.event_id.is_(None),
                                (
                                    (Game.home_team_id == home_id) & (Game.away_team_id == away_id)
                                )
                                | (
                                    (Game.home_team_id == away_id) & (Game.away_team_id == home_id)
                                ),
                            )
                        )
                    ).all()
                    matches = [
                        game
                        for game in candidates
                        if abs(aware(game.start_time) - aware(event.start_time)) <= max_gap
                    ]
                if len(matches) == 1:
                    matches[0].event_id = event.id
                    result.linked += 1
                else:
                    result.unlinked.append(event.id)
        return result
