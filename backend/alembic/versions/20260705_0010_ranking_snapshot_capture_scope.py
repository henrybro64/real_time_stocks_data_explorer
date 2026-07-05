"""add ranking snapshot capture scope

Revision ID: 20260705_0010
Revises: 20260705_0009
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260705_0010"
down_revision = "20260705_0009"
branch_labels = None
depends_on = None


def _alter_ranking_snapshots_for_capture_scope(*, reverse: bool) -> None:
    if reverse:
        capture_scope = sa.Column(
            "capture_scope",
            sa.String(length=32),
            nullable=False,
            server_default="on_demand",
        )
        old_unique = sa.UniqueConstraint(
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            name="uq_ranking_snapshots_market_board_type_bucket",
        )
        new_unique = sa.UniqueConstraint(
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            "capture_scope",
            name="uq_ranking_snapshots_market_board_type_bucket_scope",
        )
        old_index_columns = ["market", "tw_board", "ranking_type", "bucket_start"]
        new_index_columns = [
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            "capture_scope",
        ]
    else:
        capture_scope = sa.Column(
            "capture_scope",
            sa.String(length=32),
            nullable=False,
            server_default="on_demand",
        )
        old_unique = sa.UniqueConstraint(
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            name="uq_ranking_snapshots_market_board_type_bucket",
        )
        new_unique = sa.UniqueConstraint(
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            "capture_scope",
            name="uq_ranking_snapshots_market_board_type_bucket_scope",
        )
        old_index_columns = ["market", "tw_board", "ranking_type", "bucket_start"]
        new_index_columns = [
            "market",
            "tw_board",
            "ranking_type",
            "bucket_start",
            "capture_scope",
        ]

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ranking_snapshots", recreate="always") as batch_op:
            if reverse:
                batch_op.drop_index("ix_ranking_snapshots_lookup")
                batch_op.create_index(
                    "ix_ranking_snapshots_lookup",
                    old_index_columns,
                    unique=False,
                )
                batch_op.drop_constraint(
                    "uq_ranking_snapshots_market_board_type_bucket_scope",
                    type_="unique",
                )
                batch_op.create_unique_constraint(
                    "uq_ranking_snapshots_market_board_type_bucket",
                    old_index_columns,
                )
                batch_op.drop_column("capture_scope")
            else:
                batch_op.add_column(capture_scope)
                batch_op.drop_constraint(
                    "uq_ranking_snapshots_market_board_type_bucket",
                    type_="unique",
                )
                batch_op.create_unique_constraint(
                    "uq_ranking_snapshots_market_board_type_bucket_scope",
                    new_index_columns,
                )
                batch_op.drop_index("ix_ranking_snapshots_lookup")
                batch_op.create_index(
                    "ix_ranking_snapshots_lookup",
                    new_index_columns,
                    unique=False,
                )
        return

    if reverse:
        op.drop_index("ix_ranking_snapshots_lookup", table_name="ranking_snapshots")
        op.create_index(
            "ix_ranking_snapshots_lookup",
            "ranking_snapshots",
            old_index_columns,
            unique=False,
        )
        op.drop_constraint(
            "uq_ranking_snapshots_market_board_type_bucket_scope",
            "ranking_snapshots",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_ranking_snapshots_market_board_type_bucket",
            "ranking_snapshots",
            old_index_columns,
        )
        op.drop_column("ranking_snapshots", "capture_scope")
        return

    op.add_column(
        "ranking_snapshots",
        capture_scope,
    )
    op.drop_constraint(
        "uq_ranking_snapshots_market_board_type_bucket",
        "ranking_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ranking_snapshots_market_board_type_bucket_scope",
        "ranking_snapshots",
        new_index_columns,
    )
    op.drop_index("ix_ranking_snapshots_lookup", table_name="ranking_snapshots")
    op.create_index(
        "ix_ranking_snapshots_lookup",
        "ranking_snapshots",
        new_index_columns,
        unique=False,
    )


def upgrade() -> None:
    _alter_ranking_snapshots_for_capture_scope(reverse=False)


def downgrade() -> None:
    _alter_ranking_snapshots_for_capture_scope(reverse=True)
