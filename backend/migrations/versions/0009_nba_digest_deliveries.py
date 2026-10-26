"""Freeze NBA digest requests and record ambiguous sends durably."""

import sqlalchemy as sa
from alembic import op

revision = "0009_nba_digest_deliveries"
down_revision = "0008_nba_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nba_digest_deliveries",
        sa.Column(
            "slate_date", sa.Date(), sa.ForeignKey("nba_reports.slate_date"), primary_key=True
        ),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), unique=True, nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(length=160)),
        sa.Column("failure_type", sa.String(length=80)),
    )


def downgrade() -> None:
    op.drop_table("nba_digest_deliveries")
