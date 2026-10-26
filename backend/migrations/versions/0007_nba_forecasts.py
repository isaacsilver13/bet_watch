"""Durable, idempotent NBA daily forecast intake."""

import sqlalchemy as sa
from alembic import op

revision = "0007_nba_forecasts"
down_revision = "0006_model_picks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nba_forecasts",
        sa.Column("slate_date", sa.Date(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("nba_forecasts")
