"""add daily price bars table for cached history (#269)

Revision ID: 20261011_0016
Revises: 20260903_0015
Create Date: 2026-10-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261011_0016"
down_revision = "20260903_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_price_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market",
            "symbol",
            "date",
            name="uq_daily_price_bars_market_symbol_date",
        ),
    )
    op.create_index(
        "ix_daily_price_bars_lookup",
        "daily_price_bars",
        ["market", "symbol", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_daily_price_bars_lookup", table_name="daily_price_bars")
    op.drop_table("daily_price_bars")
