"""add portfolio history snapshots

Revision ID: 20260505_0006
Revises: 20260406_0005
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260505_0006"
down_revision = "20260406_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_history_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_window", sa.String(length=8), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("usd_twd_rate", sa.Float(), nullable=False),
        sa.Column("holdings_value_usd", sa.Float(), nullable=False),
        sa.Column("holdings_value_twd", sa.Float(), nullable=False),
        sa.Column("combined_assets_usd", sa.Float(), nullable=False),
        sa.Column("combined_assets_twd", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "snapshot_hour",
            "source_window",
            name="uq_portfolio_history_user_hour_window",
        ),
    )
    op.create_index(
        op.f("ix_portfolio_history_snapshots_snapshot_hour"),
        "portfolio_history_snapshots",
        ["snapshot_hour"],
        unique=False,
    )
    op.create_index(
        op.f("ix_portfolio_history_snapshots_user_id"),
        "portfolio_history_snapshots",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_portfolio_history_user_hour",
        "portfolio_history_snapshots",
        ["user_id", "snapshot_hour"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_portfolio_history_snapshots_user_id"),
        table_name="portfolio_history_snapshots",
    )
    op.drop_index(
        "ix_portfolio_history_user_hour",
        table_name="portfolio_history_snapshots",
    )
    op.drop_index(
        op.f("ix_portfolio_history_snapshots_snapshot_hour"),
        table_name="portfolio_history_snapshots",
    )
    op.drop_table("portfolio_history_snapshots")
