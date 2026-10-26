"""Frozen paper allocation batches, wagers, and append-only settlements."""

import sqlalchemy as sa
from alembic import op

revision = "0011_paper_ledger"
down_revision = "0010_owner_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_allocation_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile", sa.String(20), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("forecast_sha256", sa.String(64), nullable=False),
        sa.Column("opening_cash", sa.Float(), nullable=False),
        sa.Column("allocation", sa.JSON(), nullable=False),
        sa.UniqueConstraint("profile", "slate_date", name="uq_paper_profile_slate"),
    )
    op.create_table(
        "paper_wagers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("paper_allocation_batches.id"),
                  nullable=False),
        sa.Column("candidate_id", sa.String(220), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("quote_observation_id", sa.Integer(),
                  sa.ForeignKey("offer_observations.id"), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("book", sa.String(20), nullable=False),
        sa.Column("price_american", sa.Integer(), nullable=False),
        sa.Column("win_prob", sa.Float(), nullable=False),
        sa.Column("stake", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("net_ev", sa.Float(), nullable=False),
        sa.UniqueConstraint("batch_id", "candidate_id", name="uq_paper_batch_candidate"),
    )
    op.create_index("ix_paper_wagers_game", "paper_wagers", ["game_id"])
    op.create_table(
        "paper_settlements",
        sa.Column("wager_id", sa.Integer(), sa.ForeignKey("paper_wagers.id"),
                  primary_key=True),
        sa.Column("result", sa.String(12), nullable=False),
        sa.Column("payout", sa.Float(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("game_updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("paper_settlements")
    op.drop_index("ix_paper_wagers_game", table_name="paper_wagers")
    op.drop_table("paper_wagers")
    op.drop_table("paper_allocation_batches")
