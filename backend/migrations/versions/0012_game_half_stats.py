"""First-half scores and offense per team and game, from nflverse play-by-play."""

import sqlalchemy as sa
from alembic import op

revision = "0012_game_half_stats"
down_revision = "0011_paper_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_half_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("points_for_1h", sa.Integer(), nullable=False),
        sa.Column("points_against_1h", sa.Integer(), nullable=False),
        sa.Column("scored_first", sa.Boolean(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "team_id", name="uq_game_half_stats_game_team"),
    )


def downgrade() -> None:
    op.drop_table("game_half_stats")
