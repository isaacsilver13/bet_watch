"""Revocable browser sessions for the single owner."""

import sqlalchemy as sa
from alembic import op

revision = "0010_owner_sessions"
down_revision = "0009_nba_digest_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_sessions",
        sa.Column("token_sha256", sa.String(length=64), primary_key=True),
        sa.Column("credential_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("owner_sessions")
