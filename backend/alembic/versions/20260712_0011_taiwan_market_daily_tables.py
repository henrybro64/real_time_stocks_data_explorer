"""add Taiwan institutional and margin-short daily tables

Revision ID: 20260712_0011
Revises: 20260705_0010
Create Date: 2026-07-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260712_0011"
down_revision = "20260705_0010"
branch_labels = None
depends_on = None


def _common_daily_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
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
    ]


def _create_taiwan_daily_table(
    *,
    table_name: str,
    unique_name: str,
    index_name: str,
    extra_columns: list[sa.Column],
) -> None:
    op.create_table(
        table_name,
        *_common_daily_columns(),
        *extra_columns,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market", "symbol", "date", name=unique_name),
    )
    op.create_index(index_name, table_name, ["market", "symbol", "date"], unique=False)


def upgrade() -> None:
    _create_taiwan_daily_table(
        table_name="taiwan_institutional_daily",
        unique_name="uq_taiwan_institutional_daily_market_symbol_date",
        index_name="ix_taiwan_institutional_daily_lookup",
        extra_columns=[
            sa.Column("foreign_net_shares", sa.Integer(), nullable=False),
            sa.Column("investment_trust_net_shares", sa.Integer(), nullable=False),
            sa.Column("dealer_self_net_shares", sa.Integer(), nullable=True),
            sa.Column("dealer_hedge_net_shares", sa.Integer(), nullable=True),
            sa.Column("dealer_net_shares", sa.Integer(), nullable=False),
            sa.Column("total_net_shares", sa.Integer(), nullable=False),
        ],
    )
    _create_taiwan_daily_table(
        table_name="taiwan_margin_short_daily",
        unique_name="uq_taiwan_margin_short_daily_market_symbol_date",
        index_name="ix_taiwan_margin_short_daily_lookup",
        extra_columns=[
            sa.Column("close_price", sa.Float(), nullable=True),
            sa.Column("margin_balance_lots", sa.Float(), nullable=False),
            sa.Column("short_margin_balance_lots", sa.Float(), nullable=False),
            sa.Column("borrowed_short_balance_lots", sa.Float(), nullable=False),
            sa.Column("borrowed_short_balance_change_lots", sa.Float(), nullable=True),
            sa.Column(
                "borrowed_short_balance_change_amount_10k",
                sa.Integer(),
                nullable=True,
            ),
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_taiwan_margin_short_daily_lookup",
        table_name="taiwan_margin_short_daily",
    )
    op.drop_table("taiwan_margin_short_daily")
    op.drop_index(
        "ix_taiwan_institutional_daily_lookup",
        table_name="taiwan_institutional_daily",
    )
    op.drop_table("taiwan_institutional_daily")
