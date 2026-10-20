"""Writes and point-in-time reads for matchup context."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity import normalize_name
from app.ingest.nflverse_stats import TeamStatRecord
from app.ingest.open_meteo import WeatherRecord
from app.ingest.records import (
    InjuryRecord,
    NbaPlayerBoxScoreRecord,
    NbaProviderGame,
    NbaTeamBoxScoreRecord,
)
from app.storage.games_repository import alias_map, aware
from app.storage.models import (
    Game,
    Injury,
    InjuryOverride,
    InjurySnapshot,
    NbaContextRefresh,
    NbaPlayerGameStat,
    NbaTeamGameStat,
    StarOverride,
    Team,
    TeamGameStat,
    Venue,
    Weather,
)

OUTDOOR_ROOFS = ("outdoors", "retractable")
EASTERN = ZoneInfo("America/New_York")  # ESPN/local NBA slates use the Eastern game date
SCHEDULED_GRACE = timedelta(hours=6)  # a "scheduled" game this far past kickoff is stale, skip it
INJURY_MAX_AGE = timedelta(hours=36)
STAR_CURRENT_SEASON_MIN_GAMES = 5
NBA_CONTEXT_SEASON_TYPES = ("REG", "POST")
NBA_CONTEXT_PROVIDER = "espn"


@dataclass
class StatsResult:
    saved: int = 0
    unmatched: list[str] = field(default_factory=list)


@dataclass
class InjuryResult:
    saved: int = 0
    unknown_teams: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InjuryFact:
    team_id: int
    player: str
    position: Optional[str]
    status: str
    detail: Optional[str]
    source: str
    reported_at: Optional[datetime]
    observed_at: datetime
    manual: bool = False


@dataclass(frozen=True)
class StarPlayer:
    player: str
    minutes_per_game: Optional[float]
    source: str
    observed_at: datetime


@dataclass
class NbaSaveResult:
    team_rows: int = 0
    player_rows: int = 0
    unmatched: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WeatherTarget:
    game_id: int
    lat: float
    lon: float
    start_time: datetime


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
                    Game.id,
                    Game.season,
                    Game.season_type,
                    Game.week,
                    Game.home_team_id,
                    Game.away_team_id,
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

    async def nba_game_complete(self, provider_game_id: str) -> bool:
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(NbaContextRefresh)
                    .where(
                        NbaContextRefresh.provider == NBA_CONTEXT_PROVIDER,
                        NbaContextRefresh.provider_game_id == provider_game_id,
                        NbaContextRefresh.status == "complete",
                    )
                )
            )

    async def pending_nba_dates(self, through: datetime, limit: int = 4) -> list[date]:
        """Uncovered dates, with never-checked then least-recently checked dates first."""
        async with self.session_factory() as session:
            complete_provider_games = select(NbaContextRefresh.provider_game_id).where(
                NbaContextRefresh.provider == NBA_CONTEXT_PROVIDER,
                NbaContextRefresh.status == "complete",
            )
            covered = select(NbaTeamGameStat.game_id).where(
                NbaTeamGameStat.provider == NBA_CONTEXT_PROVIDER,
                NbaTeamGameStat.provider_game_id.in_(complete_provider_games),
            )
            starts = (
                await session.scalars(
                    select(Game.start_time)
                    .where(
                        Game.sport == "nba",
                        Game.status == "final",
                        Game.season_type.in_(NBA_CONTEXT_SEASON_TYPES),
                        Game.start_time <= through,
                        Game.id.not_in(covered),
                    )
                    .order_by(Game.start_time)
                )
            ).all()
            lookups = (
                await session.execute(
                    select(
                        NbaContextRefresh.provider_game_id,
                        func.max(NbaContextRefresh.observed_at),
                    )
                    .where(
                        NbaContextRefresh.provider == NBA_CONTEXT_PROVIDER,
                        NbaContextRefresh.provider_game_id.like("date:%"),
                    )
                    .group_by(NbaContextRefresh.provider_game_id)
                )
            ).all()
        dates: list[date] = []
        for start in starts:
            day = aware(start).astimezone(EASTERN).date()
            if day not in dates:
                dates.append(day)
        last_checked = {
            date.fromisoformat(provider_id.removeprefix("date:")): aware(observed_at)
            for provider_id, observed_at in lookups
        }
        never = datetime.min.replace(tzinfo=timezone.utc)
        dates.sort(key=lambda day: (day in last_checked, last_checked.get(day, never), day))
        return dates[:limit]

    async def order_nba_games_for_recheck(
        self, games: Sequence[NbaProviderGame]
    ) -> list[NbaProviderGame]:
        """Oldest checked games first so bounded manual correction polls advance."""
        provider_ids = [game.provider_game_id for game in games]
        if not provider_ids:
            return []
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        NbaContextRefresh.provider_game_id,
                        func.max(NbaContextRefresh.observed_at),
                    )
                    .where(
                        NbaContextRefresh.provider == NBA_CONTEXT_PROVIDER,
                        NbaContextRefresh.provider_game_id.in_(provider_ids),
                        NbaContextRefresh.status == "complete",
                    )
                    .group_by(NbaContextRefresh.provider_game_id)
                )
            ).all()
        last_checked = {provider_id: aware(observed_at) for provider_id, observed_at in rows}
        never = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(
            games,
            key=lambda game: (
                last_checked.get(game.provider_game_id, never),
                game.provider_game_id,
            ),
        )

    async def record_nba_date_lookup(
        self, covered_at: datetime, observed_at: datetime, quota_remaining: Optional[int]
    ) -> None:
        async with self.session_factory() as session, session.begin():
            session.add(
                NbaContextRefresh(
                    provider=NBA_CONTEXT_PROVIDER,
                    provider_game_id=f"date:{covered_at.date().isoformat()}",
                    covered_date=covered_at,
                    observed_at=observed_at,
                    status="listed",
                    team_rows=0,
                    player_rows=0,
                    quota_remaining=quota_remaining,
                )
            )

    async def record_nba_request_attempt(
        self, kind: str, target: str, covered_date: date, observed_at: datetime
    ) -> None:
        """Record before I/O so failed HTTP and parser attempts remain visible in health."""
        async with self.session_factory() as session, session.begin():
            session.add(
                NbaContextRefresh(
                    provider=NBA_CONTEXT_PROVIDER,
                    provider_game_id=f"request:{kind}:{target}",
                    covered_date=datetime.combine(covered_date, time(), timezone.utc),
                    observed_at=observed_at,
                    status="requested",
                    team_rows=0,
                    player_rows=0,
                )
            )

    @staticmethod
    def _fingerprint(value: dict) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode("utf-8")).hexdigest()

    async def save_nba_box_scores(
        self,
        game: NbaProviderGame,
        team_records: Sequence[NbaTeamBoxScoreRecord],
        player_records: Sequence[NbaPlayerBoxScoreRecord],
        observed_at: datetime,
        quota_remaining: Optional[int],
    ) -> NbaSaveResult:
        """Save a complete provider game atomically; exact replays do not duplicate facts."""
        result = NbaSaveResult()
        async with self.session_factory() as session, session.begin():
            aliases = await alias_map(session, "nba")
            home_id = aliases.get(normalize_name(game.home_team))
            away_id = aliases.get(normalize_name(game.away_team))
            local_game = None
            if home_id is not None and away_id is not None:
                candidates = (
                    await session.scalars(
                        select(Game).where(
                            Game.sport == "nba",
                            Game.status == "final",
                            Game.season_type.in_(NBA_CONTEXT_SEASON_TYPES),
                            Game.home_team_id.in_((home_id, away_id)),
                            Game.away_team_id.in_((home_id, away_id)),
                        )
                    )
                ).all()
                local_game = next(
                    (
                        row
                        for row in candidates
                        if aware(row.start_time).astimezone(EASTERN).date() == game.game_date
                    ),
                    None,
                )
            covered_at = datetime.combine(game.game_date, time(), timezone.utc)
            if local_game is None:
                label = f"{game.game_date} {game.away_team}-{game.home_team}"
                result.unmatched.append(label)
                session.add(
                    NbaContextRefresh(
                        provider=NBA_CONTEXT_PROVIDER,
                        provider_game_id=game.provider_game_id,
                        covered_date=covered_at,
                        observed_at=observed_at,
                        status="unmatched",
                        team_rows=0,
                        player_rows=0,
                        quota_remaining=quota_remaining,
                        error_message=label,
                    )
                )
                return result

            resolved_teams: list[tuple[NbaTeamBoxScoreRecord, int]] = []
            resolved_players: list[tuple[NbaPlayerBoxScoreRecord, int]] = []
            team_fact_ids: set[int] = set()
            player_fact_ids: set[int] = set()
            for record in team_records:
                team_id = aliases.get(normalize_name(record.team))
                if team_id is None:
                    result.unmatched.append(f"unknown team {record.team}")
                    continue
                team_fact_ids.add(team_id)
                resolved_teams.append((record, team_id))
            for record in player_records:
                team_id = aliases.get(normalize_name(record.team))
                if team_id is None:
                    result.unmatched.append(f"unknown team {record.team}")
                    continue
                player_fact_ids.add(team_id)
                resolved_players.append((record, team_id))
            expected_team_ids = {local_game.home_team_id, local_game.away_team_id}
            complete = (
                team_fact_ids == expected_team_ids
                and player_fact_ids == expected_team_ids
                and not result.unmatched
            )
            if not complete and not result.unmatched:
                result.unmatched.append("incomplete team or player coverage")

            if complete:
                existing_team = set(
                    (
                        await session.execute(
                            select(NbaTeamGameStat.team_id, NbaTeamGameStat.fingerprint).where(
                                NbaTeamGameStat.provider == NBA_CONTEXT_PROVIDER,
                                NbaTeamGameStat.provider_game_id == game.provider_game_id,
                            )
                        )
                    ).all()
                )
                existing_player = set(
                    (
                        await session.execute(
                            select(
                                NbaPlayerGameStat.provider_player_id,
                                NbaPlayerGameStat.fingerprint,
                            ).where(
                                NbaPlayerGameStat.provider == NBA_CONTEXT_PROVIDER,
                                NbaPlayerGameStat.provider_game_id == game.provider_game_id,
                            )
                        )
                    ).all()
                )
            for record, team_id in resolved_teams if complete else []:
                fingerprint = self._fingerprint(record.stats)
                fact_key = (team_id, fingerprint)
                if fact_key in existing_team:
                    continue
                session.add(
                    NbaTeamGameStat(
                        game_id=local_game.id,
                        team_id=team_id,
                        provider=NBA_CONTEXT_PROVIDER,
                        provider_game_id=record.provider_game_id,
                        provider_team_id=record.provider_team_id,
                        fingerprint=fingerprint,
                        stats=dict(record.stats),
                        observed_at=observed_at,
                    )
                )
                existing_team.add(fact_key)
                result.team_rows += 1
            for record, team_id in resolved_players if complete else []:
                fingerprint = self._fingerprint(
                    {"player": record.player, "minutes": record.minutes, "stats": record.stats}
                )
                fact_key = (record.provider_player_id, fingerprint)
                if fact_key in existing_player:
                    continue
                session.add(
                    NbaPlayerGameStat(
                        game_id=local_game.id,
                        team_id=team_id,
                        provider=NBA_CONTEXT_PROVIDER,
                        provider_game_id=record.provider_game_id,
                        provider_team_id=record.provider_team_id,
                        provider_player_id=record.provider_player_id,
                        player=record.player,
                        minutes=record.minutes,
                        fingerprint=fingerprint,
                        stats=dict(record.stats),
                        observed_at=observed_at,
                    )
                )
                existing_player.add(fact_key)
                result.player_rows += 1
            session.add(
                NbaContextRefresh(
                    provider=NBA_CONTEXT_PROVIDER,
                    provider_game_id=game.provider_game_id,
                    covered_date=covered_at,
                    observed_at=observed_at,
                    status="complete" if complete else "partial",
                    team_rows=result.team_rows,
                    player_rows=result.player_rows,
                    quota_remaining=quota_remaining,
                    error_message="; ".join(result.unmatched) or None,
                )
            )
        return result

    async def nba_team_stats_before(
        self, season: int, before: datetime
    ) -> list[tuple[NbaTeamGameStat, Game]]:
        """Latest observation for each team/game that existed before the caller's cutoff."""
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(NbaTeamGameStat, Game)
                    .join(Game, Game.id == NbaTeamGameStat.game_id)
                    .where(
                        NbaTeamGameStat.provider == NBA_CONTEXT_PROVIDER,
                        Game.season == season,
                        Game.season_type.in_(NBA_CONTEXT_SEASON_TYPES),
                        Game.start_time < before,
                        NbaTeamGameStat.observed_at <= before,
                    )
                    .order_by(NbaTeamGameStat.observed_at.desc(), NbaTeamGameStat.id.desc())
                )
            ).all()
        latest = {}
        for stat, game_row in rows:
            latest.setdefault((stat.game_id, stat.team_id), (stat, game_row))
        return list(latest.values())

    async def star_players(
        self, team_id: int, season: int, before: datetime, limit: int = 3
    ) -> list[StarPlayer]:
        async with self.session_factory() as session:
            overrides = (
                await session.scalars(
                    select(StarOverride)
                    .where(
                        StarOverride.team_id == team_id,
                        StarOverride.starts_at <= before,
                        StarOverride.ends_at > before,
                    )
                    .order_by(StarOverride.player)
                )
            ).all()
            if overrides:
                return [
                    StarPlayer(row.player, None, "manual", aware(row.starts_at))
                    for row in overrides[:limit]
                ]

            rows = (
                await session.execute(
                    select(NbaPlayerGameStat, Game)
                    .join(Game, Game.id == NbaPlayerGameStat.game_id)
                    .where(
                        NbaPlayerGameStat.provider == NBA_CONTEXT_PROVIDER,
                        NbaPlayerGameStat.team_id == team_id,
                        NbaPlayerGameStat.observed_at <= before,
                        Game.start_time < before,
                        Game.season.in_((season, season - 1)),
                        Game.season_type.in_(NBA_CONTEXT_SEASON_TYPES),
                    )
                    .order_by(NbaPlayerGameStat.observed_at.desc(), NbaPlayerGameStat.id.desc())
                )
            ).all()
        latest = {}
        for row, game_row in rows:
            latest.setdefault((row.game_id, row.provider_player_id), (row, game_row))
        current_games = {
            row.game_id for row, game_row in latest.values() if game_row.season == season
        }
        has_prior_season = any(game_row.season == season - 1 for _, game_row in latest.values())
        chosen_season = (
            season
            if len(current_games) >= STAR_CURRENT_SEASON_MIN_GAMES or not has_prior_season
            else season - 1
        )
        players: dict[str, tuple[str, float, int, datetime]] = {}
        for row, game_row in latest.values():
            if game_row.season != chosen_season or row.minutes <= 0:
                continue
            name, total, games, seen = players.get(
                row.provider_player_id, (row.player, 0.0, 0, aware(row.observed_at))
            )
            players[row.provider_player_id] = (
                name,
                total + row.minutes,
                games + 1,
                max(seen, aware(row.observed_at)),
            )
        ranked = sorted(
            (
                StarPlayer(name, round(total / games, 1), NBA_CONTEXT_PROVIDER, seen)
                for name, total, games, seen in players.values()
            ),
            key=lambda row: (-(row.minutes_per_game or 0), row.player),
        )
        return ranked[:limit]

    async def injury_snapshot(
        self, sport: str, team_ids: set[int], as_of: datetime
    ) -> tuple[dict[int, list[InjuryFact]], Optional[datetime]]:
        """The newest report of `sport` at or before `as_of`, grouped by team.

        Scoped to the sport: each sport's job writes its own snapshots, and another sport's
        newer report must not hide this one."""
        async with self.session_factory() as session:
            observed = await session.scalar(
                select(func.max(InjurySnapshot.observed_at)).where(
                    InjurySnapshot.sport == sport,
                    InjurySnapshot.source == "espn",
                    InjurySnapshot.status == "complete",
                    InjurySnapshot.observed_at <= as_of,
                )
            )
            header_count = await session.scalar(
                select(func.count())
                .select_from(InjurySnapshot)
                .where(
                    InjurySnapshot.sport == sport,
                    InjurySnapshot.source == "espn",
                    InjurySnapshot.observed_at <= as_of,
                )
            )
            if observed is None and not header_count:  # compatibility with pre-header databases
                observed = await session.scalar(
                    select(func.max(Injury.observed_at))
                    .join(Team, Team.id == Injury.team_id)
                    .where(Team.sport == sport, Injury.observed_at <= as_of)
                )
            by_team: dict[int, list[InjuryFact]] = {team_id: [] for team_id in team_ids}
            effective_at = aware(observed) if observed is not None else None
            if observed is not None and as_of - aware(observed) <= INJURY_MAX_AGE:
                rows = await session.scalars(
                    select(Injury)
                    .where(Injury.observed_at == observed, Injury.team_id.in_(team_ids))
                    .order_by(Injury.status, Injury.player)
                )
                for row in rows.all():
                    by_team[row.team_id].append(
                        InjuryFact(
                            row.team_id,
                            row.player,
                            row.position,
                            row.status,
                            row.detail,
                            row.source,
                            row.reported_at,
                            aware(row.observed_at),
                            False,
                        )
                    )
            overrides = (
                await session.scalars(
                    select(InjuryOverride).where(
                        InjuryOverride.team_id.in_(team_ids),
                        InjuryOverride.starts_at <= as_of,
                        InjuryOverride.ends_at > as_of,
                    )
                )
            ).all()
            for override in overrides:
                normalized = normalize_name(override.player)
                by_team[override.team_id] = [
                    row
                    for row in by_team[override.team_id]
                    if normalize_name(row.player) != normalized
                ]
                by_team[override.team_id].append(
                    InjuryFact(
                        override.team_id,
                        override.player,
                        override.position,
                        override.status,
                        override.note,
                        override.source,
                        None,
                        aware(override.starts_at),
                        True,
                    )
                )
            return by_team, effective_at

    async def save_injuries(
        self, sport: str, records: Sequence[InjuryRecord], observed_at: datetime
    ) -> InjuryResult:
        """Append one successful snapshot, including an explicit zero-row all-clear."""
        result = InjuryResult()
        async with self.session_factory() as session, session.begin():
            existing = await session.scalar(
                select(InjurySnapshot.id).where(
                    InjurySnapshot.sport == sport,
                    InjurySnapshot.source == "espn",
                    InjurySnapshot.observed_at == observed_at,
                )
            )
            if existing is not None:
                return result
            teams = dict(
                (
                    await session.execute(select(Team.espn_id, Team.id).where(Team.sport == sport))
                ).all()
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
            session.add(
                InjurySnapshot(
                    sport=sport,
                    source="espn",
                    observed_at=observed_at,
                    status="partial" if result.unknown_teams else "complete",
                    row_count=result.saved,
                )
            )
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

    async def save_weather(self, game_id: int, record: WeatherRecord, fetched_at: datetime) -> None:
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
