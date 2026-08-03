"""allow nullable Taiwan margin-short balance columns

Revision ID: 20260803_0012
Revises: 20260712_0011
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260803_0012"
down_revision = "20260712_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("taiwan_margin_short_daily", recreate="always") as batch_op:
            batch_op.alter_column(
                "margin_balance_lots",
                existing_type=sa.Float(),
                nullable=True,
            )
            batch_op.alter_column(
                "short_margin_balance_lots",
                existing_type=sa.Float(),
                nullable=True,
            )
            batch_op.alter_column(
                "borrowed_short_balance_lots",
                existing_type=sa.Float(),
                nullable=True,
            )
        return

    op.alter_column(
        "taiwan_margin_short_daily",
        "margin_balance_lots",
        existing_type=sa.Float(),
        nullable=True,
    )
    op.alter_column(
        "taiwan_margin_short_daily",
        "short_margin_balance_lots",
        existing_type=sa.Float(),
        nullable=True,
    )
    op.alter_column(
        "taiwan_margin_short_daily",
        "borrowed_short_balance_lots",
        existing_type=sa.Float(),
        nullable=True,
    )


def downgrade() -> None:
    # Partial rows (null balances) cannot exist under the previous NOT NULL schema.
    # Drop them before restoring non-null constraints.
    op.execute(
        sa.text(
            """
            DELETE FROM taiwan_margin_short_daily
            WHERE margin_balance_lots IS NULL
               OR short_margin_balance_lots IS NULL
               OR borrowed_short_balance_lots IS NULL
            """
        )
    )

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("taiwan_margin_short_daily", recreate="always") as batch_op:
            batch_op.alter_column(
                "margin_balance_lots",
                existing_type=sa.Float(),
                nullable=False,
            )
            batch_op.alter_column(
                "short_margin_balance_lots",
                existing_type=sa.Float(),
                nullable=False,
            )
            batch_op.alter_column(
                "borrowed_short_balance_lots",
                existing_type=sa.Float(),
                nullable=False,
            )
        return

    op.alter_column(
        "taiwan_margin_short_daily",
        "borrowed_short_balance_lots",
        existing_type=sa.Float(),
        nullable=False,
    )
    op.alter_column(
        "taiwan_margin_short_daily",
        "short_margin_balance_lots",
        existing_type=sa.Float(),
        nullable=False,
    )
    op.alter_column(
        "taiwan_margin_short_daily",
        "margin_balance_lots",
        existing_type=sa.Float(),
        nullable=False,
    )
