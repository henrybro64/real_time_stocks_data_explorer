"""add ranking snapshots

Revision ID: 20260705_0009
Revises: 20260526_0008
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260705_0009"
down_revision = "20260526_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ranking_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("tw_board", sa.String(length=8), nullable=True),
        sa.Column("ranking_type", sa.String(length=32), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_freshness", sa.Text(), nullable=False),
        sa.Column("is_market_open", sa.Boolean(), nullable=False),
        sa.Column("warning_message", sa.Text(), nullable=True),
        sa.Column("unsupported_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            name="uq_ranking_snapshots_market_board_type_bucket",
        ),
    )
    op.create_index(
        op.f("ix_ranking_snapshots_bucket_start"),
        "ranking_snapshots",
        ["bucket_start"],
        unique=False,
    )
    op.create_index(
        "ix_ranking_snapshots_lookup",
        "ranking_snapshots",
        ["market", "tw_board", "ranking_type", "bucket_start"],
        unique=False,
    )

    op.create_table(
        "ranking_snapshot_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("last_price", sa.Float(), nullable=True),
        sa.Column("change_percent", sa.Float(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("volume_delta", sa.Integer(), nullable=True),
        sa.Column("volume_delta_percent", sa.Float(), nullable=True),
        sa.Column("net_buy", sa.Integer(), nullable=True),
        sa.Column("net_sell", sa.Integer(), nullable=True),
        sa.Column("data_time", sa.String(length=64), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["ranking_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "rank",
            name="uq_ranking_snapshot_items_snapshot_rank",
        ),
    )
    op.create_index(
        op.f("ix_ranking_snapshot_items_snapshot_id"),
        "ranking_snapshot_items",
        ["snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ranking_snapshot_items_snapshot_id"),
        table_name="ranking_snapshot_items",
    )
    op.drop_table("ranking_snapshot_items")
    op.drop_index(
        "ix_ranking_snapshots_lookup",
        table_name="ranking_snapshots",
    )
    op.drop_index(
        op.f("ix_ranking_snapshots_bucket_start"),
        table_name="ranking_snapshots",
    )
    op.drop_table("ranking_snapshots")
