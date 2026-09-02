"""add realized trades table

Revision ID: 20260902_0014
Revises: 20260816_0013
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260902_0014"
down_revision = "20260816_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realized_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("buy_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("sell_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("shares", sa.Numeric(20, 6), nullable=False),
        sa.Column("buy_date", sa.Date(), nullable=True),
        sa.Column("sell_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("buy_price > 0", name="ck_realized_trades_buy_price_positive"),
        sa.CheckConstraint("sell_price > 0", name="ck_realized_trades_sell_price_positive"),
        sa.CheckConstraint("shares > 0", name="ck_realized_trades_shares_positive"),
        sa.CheckConstraint(
            "currency IN ('TWD', 'USD')",
            name="ck_realized_trades_currency",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_realized_trades_user_sell_date",
        "realized_trades",
        ["user_id", "sell_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_realized_trades_user_id"),
        "realized_trades",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_realized_trades_user_id"), table_name="realized_trades")
    op.drop_index("ix_realized_trades_user_sell_date", table_name="realized_trades")
    op.drop_table("realized_trades")
