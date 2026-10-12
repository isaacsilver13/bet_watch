"""Venues, teams, aliases, games, reference lines, and job slots for the matchup hub."""

import sqlalchemy as sa
from alembic import op

revision = "0002_matchup_core"
down_revision = "0001_odds_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "venues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("espn_id", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("tz", sa.String(length=60), nullable=True),
        sa.Column("roof", sa.String(length=20), nullable=True),
        sa.Column("surface", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_id"),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("abbr", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("espn_id", sa.String(length=40), nullable=True),
        sa.Column("conference", sa.String(length=60), nullable=True),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sport", "abbr", name="uq_teams_sport_abbr"),
    )
    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("alias", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sport", "alias", name="uq_team_aliases_sport_alias"),
    )
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("season_type", sa.String(length=10), nullable=False),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        sa.Column("neutral", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("espn_id", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=180), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_id"),
    )
    op.create_index(
        "ix_games_sport_season_week", "games", ["sport", "season", "season_type", "week"]
    )
    op.create_index("ix_games_start_time", "games", ["start_time"])
    op.create_table(
        "game_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("spread_home", sa.Float(), nullable=True),
        sa.Column("spread_home_price", sa.Integer(), nullable=True),
        sa.Column("spread_away_price", sa.Integer(), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("over_price", sa.Integer(), nullable=True),
        sa.Column("under_price", sa.Integer(), nullable=True),
        sa.Column("ml_home", sa.Integer(), nullable=True),
        sa.Column("ml_away", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "source", "kind", name="uq_game_lines_game_source_kind"),
    )
    op.create_table(
        "job_slots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job", sa.String(length=60), nullable=False),
        sa.Column("slot_key", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.Column("raw_path", sa.String(length=400), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job", "slot_key", name="uq_job_slots_job_key"),
    )


def downgrade() -> None:
    op.drop_table("job_slots")
    op.drop_table("game_lines")
    op.drop_index("ix_games_start_time", table_name="games")
    op.drop_index("ix_games_sport_season_week", table_name="games")
    op.drop_table("games")
    op.drop_table("team_aliases")
    op.drop_table("teams")
    op.drop_table("venues")
