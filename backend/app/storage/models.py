from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProviderRefreshRun(Base):
    __tablename__ = "provider_refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    sport: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    quota_remaining: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    league: Mapped[str] = mapped_column(String(120), nullable=False)
    home_team: Mapped[str] = mapped_column(String(160), nullable=False)
    away_team: Mapped[str] = mapped_column(String(160), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderEventMapping(Base):
    __tablename__ = "provider_event_mappings"
    __table_args__ = (
        Index("ix_provider_event_mappings_provider_raw", "provider", "provider_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OfferObservation(Base):
    __tablename__ = "offer_observations"
    __table_args__ = (
        Index(
            "ix_offer_observations_event_market_bookmaker_time",
            "event_id",
            "market_type",
            "bookmaker",
            "observed_at",
        ),
        Index("ix_offer_observations_event_market", "event_id", "market_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(String(80), nullable=False)
    selection: Mapped[str] = mapped_column(String(180), nullable=False)
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_american: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_decimal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    player_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    provider_offer_id: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    provider_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deep_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    espn_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tz: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    roof: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # outdoors|dome|retract.
    surface: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("sport", "abbr", name="uq_teams_sport_abbr"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    abbr: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    espn_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    conference: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)  # e.g. "AFC West"
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"), nullable=True)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("sport", "alias", name="uq_team_aliases_sport_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)  # identity.normalize_name()
    source: Mapped[str] = mapped_column(String(40), nullable=False)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_sport_season_week", "sport", "season", "season_type", "week"),
        Index("ix_games_start_time", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[str] = mapped_column(String(10), nullable=False)  # PRE|REG|POST
    week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"), nullable=True)
    neutral: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_qb: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # nflverse starter
    away_qb: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    espn_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(ForeignKey("events.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameLine(Base):
    __tablename__ = "game_lines"
    __table_args__ = (
        UniqueConstraint("game_id", "source", "kind", name="uq_game_lines_game_source_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # espn|nflverse
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # open|current|close
    spread_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread_home_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    spread_away_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    over_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    under_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ml_home: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ml_away: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobSlot(Base):
    __tablename__ = "job_slots"
    __table_args__ = (UniqueConstraint("job", "slot_key", name="uq_job_slots_job_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(60), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    raw_path: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TeamGameStat(Base):
    __tablename__ = "team_game_stats"
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_team_game_stats_game_team"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False)  # curated nflverse columns
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NbaTeamGameStat(Base):
    """Append-only NBA context observation; fingerprints make exact replays idempotent."""

    __tablename__ = "nba_team_game_stats"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_game_id",
            "team_id",
            "fingerprint",
            name="uq_nba_team_stat_fact",
        ),
        Index("ix_nba_team_stats_game_observed", "game_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_game_id: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_team_id: Mapped[str] = mapped_column(String(60), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NbaPlayerGameStat(Base):
    __tablename__ = "nba_player_game_stats"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_game_id",
            "provider_player_id",
            "fingerprint",
            name="uq_nba_player_stat_fact",
        ),
        Index("ix_nba_player_stats_team_observed", "team_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_game_id: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_team_id: Mapped[str] = mapped_column(String(60), nullable=False)
    provider_player_id: Mapped[str] = mapped_column(String(60), nullable=False)
    player: Mapped[str] = mapped_column(String(160), nullable=False)
    minutes: Mapped[float] = mapped_column(Float, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NbaContextRefresh(Base):
    __tablename__ = "nba_context_refreshes"
    __table_args__ = (Index("ix_nba_context_refresh_game", "provider_game_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_game_id: Mapped[str] = mapped_column(String(60), nullable=False)
    covered_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    team_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    player_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_remaining: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class StarOverride(Base):
    __tablename__ = "star_overrides"
    __table_args__ = (Index("ix_star_overrides_team_window", "team_id", "starts_at", "ends_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class Injury(Base):
    """One row per reportable player per snapshot; a snapshot is every row sharing observed_at."""

    __tablename__ = "injuries"
    __table_args__ = (Index("ix_injuries_observed_team", "observed_at", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InjurySnapshot(Base):
    """A successful poll, including the meaningful zero-row/all-clear case."""

    __tablename__ = "injury_snapshots"
    __table_args__ = (
        UniqueConstraint("sport", "source", "observed_at", name="uq_injury_snapshot_poll"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)


class InjuryOverride(Base):
    __tablename__ = "injury_overrides"
    __table_args__ = (Index("ix_injury_overrides_team_window", "team_id", "starts_at", "ends_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Weather(Base):
    """Latest forecast for the kickoff hour; one row per outdoor game."""

    __tablename__ = "weather"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), unique=True, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temp_f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wind_mph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precip_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    conditions: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)


class Bet(Base):
    """One single bet. `line` is the bet side's own number (home -2.5, away +2.5, over 44.5)."""

    __tablename__ = "bets"
    __table_args__ = (Index("ix_bets_game", "game_id"), Index("ix_bets_status", "status"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(12), nullable=False)  # spread|total|moneyline
    selection: Mapped[str] = mapped_column(String(8), nullable=False)  # home|away|over|under
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_american: Mapped[int] = mapped_column(Integer, nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # fixed at entry
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    payout: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # total returned
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    close_line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # no-vig fair price
    close_source: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # odds_api|espn
    clv_points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clv_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
