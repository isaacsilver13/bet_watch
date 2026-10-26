"""Persist one NBA report snapshot per slate for idempotent delivery retries."""

import sqlalchemy as sa
from alembic import op

revision = "0008_nba_reports"
down_revision = "0007_nba_forecasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nba_reports",
        sa.Column("slate_date", sa.Date(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("nba_reports")
