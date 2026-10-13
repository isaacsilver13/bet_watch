"""Team game stats, injury snapshots, weather, and nflverse starting quarterbacks."""

import sqlalchemy as sa
from alembic import op

revision = "0003_matchup_context"
down_revision = "0002_matchup_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("home_qb", sa.String(length=120), nullable=True))
    op.add_column("games", sa.Column("away_qb", sa.String(length=120), nullable=True))
    op.create_table(
        "team_game_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "team_id", name="uq_team_game_stats_game_team"),
    )
    op.create_table(
        "injuries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player", sa.String(length=160), nullable=False),
        sa.Column("position", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_injuries_observed_team", "injuries", ["observed_at", "team_id"])
    op.create_table(
        "weather",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temp_f", sa.Float(), nullable=True),
        sa.Column("wind_mph", sa.Float(), nullable=True),
        sa.Column("precip_pct", sa.Float(), nullable=True),
        sa.Column("conditions", sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id"),
    )


def downgrade() -> None:
    op.drop_table("weather")
    op.drop_index("ix_injuries_observed_team", table_name="injuries")
    op.drop_table("injuries")
    op.drop_table("team_game_stats")
    op.drop_column("games", "away_qb")
    op.drop_column("games", "home_qb")
