"""Writes for the matchup context tables: team game stats, injury snapshots and weather."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.injuries import name_key
from app.identity import normalize_name
from app.ingest.nba_stats import GameLogRecord, PlayerStatRecord
from app.ingest.nflverse_stats import TeamStatRecord
from app.ingest.open_meteo import WeatherRecord
from app.ingest.records import InjuryRecord, NamedInjury
from app.storage.games_repository import alias_map, aware
from app.storage.models import (
    Game,
    Injury,
    PlayerSeasonStat,
    Team,
    TeamGameStat,
    Venue,
    Weather,
)

OUTDOOR_ROOFS = ("outdoors", "retractable")
EASTERN = ZoneInfo("America/New_York")  # stats.nba.com dates a game by its Eastern date
SCHEDULED_GRACE = timedelta(hours=6)  # a "scheduled" game this far past kickoff is stale, skip it


@dataclass
class StatsResult:
    saved: int = 0
    unmatched: list[str] = field(default_factory=list)


@dataclass
class InjuryResult:
    saved: int = 0
    unknown_teams: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WeatherTarget:
    game_id: int
    lat: float
    lon: float
    start_time: datetime


OFFICIAL_SOURCES = ("nba_official", "nflverse")  # these beat ESPN for a player both list
OFFICIAL_MAX_AGE = timedelta(days=7)  # NFL reports land weekly; the NBA's every hour
UPCOMING_GRACE = timedelta(hours=12)


@dataclass(frozen=True)
class ReportedInjury:
    """One player on the merged report. `source` is the report this row came from."""

    player: str
    position: Optional[str]
    status: str
    detail: Optional[str]
    reported_at: Optional[datetime]
    source: str


async def merged_injuries(
    session: AsyncSession, sport: str, team_ids: set[int], as_of: datetime
) -> tuple[dict[int, list[ReportedInjury]], Optional[datetime]]:
    """Each source's newest report of `sport` at or before `as_of`, merged by player.

    ESPN, nflverse (NFL) and the league's PDF (NBA) each write their own snapshots (rows that
    share observed_at). The official reports beat ESPN for any player they list; ESPN fills in
    the rest. Scoped to the sport: another sport's newer report must not hide this one. An
    official report older than OFFICIAL_MAX_AGE is ignored: the jobs write nothing out of season,
    so last season's final report would otherwise outrank ESPN's current one for months."""
    newest = (
        await session.execute(
            select(Injury.source, func.max(Injury.observed_at))
            .join(Team, Team.id == Injury.team_id)
            .where(
                Team.sport == sport,
                Injury.observed_at <= as_of,
                or_(
                    Injury.source.not_in(OFFICIAL_SOURCES),
                    Injury.observed_at >= as_of - OFFICIAL_MAX_AGE,
                ),
            )
            .group_by(Injury.source)
        )
    ).all()
    by_team: dict[int, list[ReportedInjury]] = {team_id: [] for team_id in team_ids}
    if not newest:
        return by_team, None
    merged: dict[tuple[int, str], ReportedInjury] = {}
    for source, observed in sorted(newest, key=lambda item: item[0] in OFFICIAL_SOURCES):
        rows = await session.scalars(
            select(Injury).where(
                Injury.source == source,
                Injury.observed_at == observed,
                Injury.team_id.in_(team_ids),
            )
        )
        for row in rows.all():
            key = (row.team_id, name_key(row.player))
            earlier = merged.get(key)
            merged[key] = ReportedInjury(
                player=row.player,
                position=row.position or (earlier.position if earlier else None),
                status=row.status,
                detail=row.detail,
                reported_at=aware(row.reported_at) if row.reported_at else None,
                source=source,
            )
    for (team_id, _), item in merged.items():
        by_team[team_id].append(item)
    for items in by_team.values():
        items.sort(key=lambda item: (item.status, item.player))
    return by_team, aware(max(observed for _, observed in newest))


class ContextRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def save_team_stats(
        self, sport: str, records: Sequence[TeamStatRecord], fetched_at: datetime
    ) -> StatsResult:
        result = StatsResult()
        seasons = {record.season for record in records}
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, sport)
            regular: dict[tuple, int] = {}
            postseason: dict[tuple, int] = {}
            game_rows = await session.execute(
                select(
                    Game.id, Game.season, Game.season_type, Game.week,
                    Game.home_team_id, Game.away_team_id,
                ).where(Game.sport == sport, Game.season.in_(seasons))
            )
            for game_id, season, kind, week, home_id, away_id in game_rows.all():
                pair = frozenset((home_id, away_id))
                if kind == "REG":
                    regular[(season, week, pair)] = game_id
                elif kind == "POST":
                    postseason[(season, pair)] = game_id  # nflverse postseason weeks differ
            existing = {
                (row.game_id, row.team_id): row
                for row in (
                    await session.scalars(
                        select(TeamGameStat)
                        .join(Game, Game.id == TeamGameStat.game_id)
                        .where(Game.sport == sport, Game.season.in_(seasons))
                    )
                ).all()
            }
            for record in records:
                team_id = aliases.get(normalize_name(record.team))
                opponent_id = aliases.get(normalize_name(record.opponent))
                game_id = None
                if team_id is not None and opponent_id is not None:
                    pair = frozenset((team_id, opponent_id))
                    game_id = (
                        regular.get((record.season, record.week, pair))
                        if record.season_type == "REG"
                        else postseason.get((record.season, pair))
                    )
                if game_id is None:
                    label = f"{record.season} {record.season_type} wk{record.week}"
                    result.unmatched.append(f"{label} {record.team}-{record.opponent}")
                    continue
                row = existing.get((game_id, team_id))
                if row is None:
                    row = TeamGameStat(game_id=game_id, team_id=team_id)
                    session.add(row)
                    existing[(game_id, team_id)] = row
                row.stats = dict(record.stats)
                row.fetched_at = fetched_at
                result.saved += 1
        return result

    async def save_game_log_stats(
        self, sport: str, records: Sequence[GameLogRecord], fetched_at: datetime
    ) -> StatsResult:
        """Attach each team-game row to its game by season, Eastern date and the two teams."""
        result = StatsResult()
        seasons = {record.season for record in records}
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, sport)
            games: dict[tuple, int] = {}
            game_rows = await session.execute(
                select(
                    Game.id, Game.season, Game.start_time, Game.home_team_id, Game.away_team_id
                ).where(Game.sport == sport, Game.season.in_(seasons))
            )
            for game_id, season, start, home_id, away_id in game_rows.all():
                day = aware(start).astimezone(EASTERN).date()
                games[(season, day, frozenset((home_id, away_id)))] = game_id
            existing = {
                (row.game_id, row.team_id): row
                for row in (
                    await session.scalars(
                        select(TeamGameStat)
                        .join(Game, Game.id == TeamGameStat.game_id)
                        .where(Game.sport == sport, Game.season.in_(seasons))
                    )
                ).all()
            }
            for record in records:
                team_id = aliases.get(normalize_name(record.team))
                opponent_id = aliases.get(normalize_name(record.opponent))
                game_id = None
                if team_id is not None and opponent_id is not None:
                    pair = frozenset((team_id, opponent_id))
                    game_id = games.get((record.season, record.game_date, pair))
                if game_id is None:
                    result.unmatched.append(f"{record.game_date} {record.team}-{record.opponent}")
                    continue
                row = existing.get((game_id, team_id))
                if row is None:
                    row = TeamGameStat(game_id=game_id, team_id=team_id)
                    session.add(row)
                    existing[(game_id, team_id)] = row
                row.stats = dict(record.stats)
                row.fetched_at = fetched_at
                result.saved += 1
        return result

    async def save_player_stats(
        self, sport: str, season: int, records: Sequence[PlayerStatRecord], fetched_at: datetime
    ) -> StatsResult:
        """Replace the season's minutes with this file's rows. A file that resolves to no team
        leaves the table alone, so a bad fetch never wipes the stars."""
        result = StatsResult()
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, sport)
            rows: dict[tuple[int, str], PlayerSeasonStat] = {}
            for record in records:
                team_id = aliases.get(normalize_name(record.team))
                if team_id is None:
                    if record.team not in result.unmatched:
                        result.unmatched.append(record.team)
                    continue
                key = (team_id, name_key(record.player))
                rows[key] = PlayerSeasonStat(
                    sport=sport, season=season, team_id=team_id, player=key[1],
                    games=record.games, minutes_pg=record.minutes_pg, fetched_at=fetched_at,
                )
            if rows:
                await session.execute(
                    delete(PlayerSeasonStat).where(
                        PlayerSeasonStat.sport == sport, PlayerSeasonStat.season == season
                    )
                )
                session.add_all(rows.values())
            result.saved = len(rows)
        return result

    async def injury_snapshot(
        self, sport: str, team_ids: set[int], as_of: datetime
    ) -> tuple[dict[int, list[ReportedInjury]], Optional[datetime]]:
        async with self.session_factory() as session:
            return await merged_injuries(session, sport, team_ids, as_of)

    async def save_official_injuries(
        self, sport: str, source: str, records: Sequence[NamedInjury], observed_at: datetime
    ) -> InjuryResult:
        """Append one snapshot from a league report that names its teams (an alias)."""
        result = InjuryResult()
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, sport)
            for record in records:
                team_id = aliases.get(normalize_name(record.team))
                if team_id is None:
                    if record.team not in result.unknown_teams:
                        result.unknown_teams.append(record.team)
                    continue
                session.add(
                    Injury(
                        team_id=team_id,
                        player=record.player[:160],
                        position=record.position,
                        status=record.status,
                        detail=record.detail[:300] if record.detail else None,
                        source=source,
                        reported_at=record.reported_at,
                        observed_at=observed_at,
                    )
                )
                result.saved += 1
        return result

    async def upcoming_week(self, sport: str, now: datetime) -> Optional[tuple[int, int]]:
        """(season, week) of the first regular-season game that is not over yet, or None."""
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Game.season, Game.week)
                    .where(
                        Game.sport == sport,
                        Game.season_type == "REG",
                        Game.week.is_not(None),
                        Game.status.in_(("scheduled", "in_progress")),
                        Game.start_time >= now - UPCOMING_GRACE,
                    )
                    .order_by(Game.start_time)
                    .limit(1)
                )
            ).first()
        return (row[0], row[1]) if row else None

    async def save_injuries(
        self, sport: str, records: Sequence[InjuryRecord], observed_at: datetime
    ) -> InjuryResult:
        """Append one snapshot: every row shares observed_at, and the newest snapshot wins."""
        result = InjuryResult()
        async with self.session_factory() as session, session.begin():
            teams = dict(
                (await session.execute(select(Team.espn_id, Team.id).where(Team.sport == sport)))
                .all()
            )
            for record in records:
                team_id = teams.get(record.team_espn_id)
                if team_id is None:
                    if record.team_espn_id not in result.unknown_teams:
                        result.unknown_teams.append(record.team_espn_id)
                    continue
                session.add(
                    Injury(
                        team_id=team_id,
                        player=record.player,
                        position=record.position,
                        status=record.status,
                        detail=record.detail,
                        source="espn",
                        reported_at=record.reported_at,
                        observed_at=observed_at,
                    )
                )
                result.saved += 1
        return result

    async def weather_targets(
        self, sport: str, now: datetime, horizon: timedelta
    ) -> list[WeatherTarget]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Game.id, Venue.lat, Venue.lon, Game.start_time)
                .join(Venue, Game.venue_id == Venue.id)
                .where(
                    Game.sport == sport,
                    Game.status == "scheduled",
                    Game.start_time >= now - SCHEDULED_GRACE,
                    Game.start_time <= now + horizon,
                    Venue.roof.in_(OUTDOOR_ROOFS),
                    Venue.lat.is_not(None),
                    Venue.lon.is_not(None),
                )
                .order_by(Game.start_time)
            )
            return [WeatherTarget(*row) for row in rows.all()]

    async def save_weather(
        self, game_id: int, record: WeatherRecord, fetched_at: datetime
    ) -> None:
        async with self.session_factory() as session, session.begin():
            row: Optional[Weather] = await session.scalar(
                select(Weather).where(Weather.game_id == game_id)
            )
            if row is None:
                row = Weather(game_id=game_id)
                session.add(row)
            row.fetched_at = fetched_at
            row.temp_f = record.temp_f
            row.wind_mph = record.wind_mph
            row.precip_pct = record.precip_pct
            row.conditions = record.conditions
