"""initial schema for users, watchlist, portfolio

Revision ID: 20260323_0001
Revises:
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260323_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "stock_id", name="uq_watchlist_items_user_stock"),
    )
    op.create_index(op.f("ix_watchlist_items_user_id"), "watchlist_items", ["user_id"], unique=False)

    op.create_table(
        "portfolio_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("average_cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "stock_id", name="uq_portfolio_positions_user_stock"),
    )
    op.create_index(op.f("ix_portfolio_positions_user_id"), "portfolio_positions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_portfolio_positions_user_id"), table_name="portfolio_positions")
    op.drop_table("portfolio_positions")
    op.drop_index(op.f("ix_watchlist_items_user_id"), table_name="watchlist_items")
    op.drop_table("watchlist_items")
    op.drop_table("users")
