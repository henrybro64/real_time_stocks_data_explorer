"""widen ranking_snapshots.tw_board for US universes

Revision ID: 20260816_0013
Revises: 20260803_0012
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260816_0013"
down_revision = "20260803_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ranking_snapshots", recreate="always") as batch_op:
            batch_op.alter_column(
                "tw_board",
                existing_type=sa.String(length=8),
                type_=sa.String(length=16),
                existing_nullable=True,
            )
        return

    op.alter_column(
        "ranking_snapshots",
        "tw_board",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ranking_snapshots", recreate="always") as batch_op:
            batch_op.alter_column(
                "tw_board",
                existing_type=sa.String(length=16),
                type_=sa.String(length=8),
                existing_nullable=True,
            )
        return

    op.alter_column(
        "ranking_snapshots",
        "tw_board",
        existing_type=sa.String(length=16),
        type_=sa.String(length=8),
        existing_nullable=True,
    )
