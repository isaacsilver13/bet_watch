"""The bet log."""

import sqlalchemy as sa
from alembic import op

revision = "0004_bets"
down_revision = "0003_matchup_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(length=20), nullable=False),
        sa.Column("market", sa.String(length=12), nullable=False),
        sa.Column("selection", sa.String(length=8), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("price_american", sa.Integer(), nullable=False),
        sa.Column("stake", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("payout", sa.Float(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_line", sa.Float(), nullable=True),
        sa.Column("close_price", sa.Integer(), nullable=True),
        sa.Column("close_source", sa.String(length=12), nullable=True),
        sa.Column("clv_points", sa.Float(), nullable=True),
        sa.Column("clv_prob", sa.Float(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bets_game", "bets", ["game_id"])
    op.create_index("ix_bets_status", "bets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_bets_status", table_name="bets")
    op.drop_index("ix_bets_game", table_name="bets")
    op.drop_table("bets")
