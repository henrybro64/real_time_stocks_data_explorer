"""persist catalogs, holiday calendars, and fetched history months

Revision ID: 20261011_0017
Revises: 20261011_0016
Create Date: 2026-10-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261011_0017"
down_revision = "20261011_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_snapshots",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_table(
        "daily_price_month_fetches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("bar_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market",
            "symbol",
            "month_start",
            name="uq_daily_price_month_fetches_market_symbol_month",
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_price_month_fetches")
    op.drop_table("reference_snapshots")
