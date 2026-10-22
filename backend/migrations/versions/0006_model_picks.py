"""NBA model picks, priced each morning and graded against the close."""

import sqlalchemy as sa
from alembic import op

revision = "0006_model_picks"
down_revision = "0005_player_season_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_picks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=12), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("book", sa.String(length=40), nullable=False),
        sa.Column("price_american", sa.Integer(), nullable=False),
        sa.Column("model_prob", sa.Float(), nullable=False),
        sa.Column("market_prob_novig", sa.Float(), nullable=True),
        sa.Column("ev", sa.Float(), nullable=False),
        sa.Column("kelly_frac", sa.Float(), nullable=False),
        sa.Column("tier", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_line", sa.Float(), nullable=True),
        sa.Column("close_price", sa.Integer(), nullable=True),
        sa.Column("close_source", sa.String(length=12), nullable=True),
        sa.Column("clv_points", sa.Float(), nullable=True),
        sa.Column("clv", sa.Float(), nullable=True),
        sa.Column("result", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "market", "side", name="uq_model_picks_game_market_side"),
    )
    op.create_index("ix_model_picks_slate_date", "model_picks", ["slate_date"])


def downgrade() -> None:
    op.drop_index("ix_model_picks_slate_date", table_name="model_picks")
    op.drop_table("model_picks")
