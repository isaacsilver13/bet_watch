"""Point-in-time free NBA context and explicit injury snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0005_free_nba_context"
down_revision = "0004_bets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nba_team_game_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_game_id", sa.String(60), nullable=False),
        sa.Column("provider_team_id", sa.String(60), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_game_id",
            "team_id",
            "fingerprint",
            name="uq_nba_team_stat_fact",
        ),
    )
    op.create_index(
        "ix_nba_team_stats_game_observed", "nba_team_game_stats", ["game_id", "observed_at"]
    )
    op.create_table(
        "nba_player_game_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_game_id", sa.String(60), nullable=False),
        sa.Column("provider_team_id", sa.String(60), nullable=False),
        sa.Column("provider_player_id", sa.String(60), nullable=False),
        sa.Column("player", sa.String(160), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_game_id",
            "provider_player_id",
            "fingerprint",
            name="uq_nba_player_stat_fact",
        ),
    )
    op.create_index(
        "ix_nba_player_stats_team_observed", "nba_player_game_stats", ["team_id", "observed_at"]
    )
    op.create_table(
        "nba_context_refreshes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_game_id", sa.String(60), nullable=False),
        sa.Column("covered_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("team_rows", sa.Integer(), nullable=False),
        sa.Column("player_rows", sa.Integer(), nullable=False),
        sa.Column("quota_remaining", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nba_context_refresh_game", "nba_context_refreshes", ["provider_game_id", "status"]
    )
    op.create_table(
        "star_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player", sa.String(160), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_star_overrides_team_window", "star_overrides", ["team_id", "starts_at", "ends_at"]
    )
    op.create_table(
        "injury_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sport", "source", "observed_at", name="uq_injury_snapshot_poll"),
    )
    op.create_table(
        "injury_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player", sa.String(160), nullable=False),
        sa.Column("position", sa.String(10), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_injury_overrides_team_window", "injury_overrides", ["team_id", "starts_at", "ends_at"]
    )


def downgrade() -> None:
    op.drop_table("injury_overrides")
    op.drop_table("injury_snapshots")
    op.drop_table("star_overrides")
    op.drop_table("nba_context_refreshes")
    op.drop_table("nba_player_game_stats")
    op.drop_table("nba_team_game_stats")
