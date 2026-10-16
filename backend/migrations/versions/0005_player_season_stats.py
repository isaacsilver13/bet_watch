"""Per-game minutes by player and season, to pick each team's star players."""

import sqlalchemy as sa
from alembic import op

revision = "0005_player_season_stats"
down_revision = "0004_bets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_season_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player", sa.String(length=160), nullable=False),
        sa.Column("games", sa.Integer(), nullable=False),
        sa.Column("minutes_pg", sa.Float(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sport", "season", "team_id", "player", name="uq_player_season_stats"),
    )


def downgrade() -> None:
    op.drop_table("player_season_stats")
