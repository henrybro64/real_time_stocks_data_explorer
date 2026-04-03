"""add portfolio assets table

Revision ID: 20260324_0003
Revises: 20260323_0002
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_0003"
down_revision = "20260323_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_portfolio_assets_user_id"),
        "portfolio_assets",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_portfolio_assets_user_id"),
        table_name="portfolio_assets",
    )
    op.drop_table("portfolio_assets")
