"""add cumulative dividends to realized trades

Revision ID: 20260903_0015
Revises: 20260902_0014
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260903_0015"
down_revision = "20260902_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("realized_trades", recreate="always") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "cumulative_dividends",
                    sa.Numeric(20, 6),
                    server_default="0",
                    nullable=False,
                ),
            )
            batch_op.create_check_constraint(
                "ck_realized_trades_cumulative_dividends_non_negative",
                "cumulative_dividends >= 0",
            )
        return

    op.add_column(
        "realized_trades",
        sa.Column(
            "cumulative_dividends",
            sa.Numeric(20, 6),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_realized_trades_cumulative_dividends_non_negative",
        "realized_trades",
        "cumulative_dividends >= 0",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("realized_trades", recreate="always") as batch_op:
            batch_op.drop_constraint(
                "ck_realized_trades_cumulative_dividends_non_negative",
                type_="check",
            )
            batch_op.drop_column("cumulative_dividends")
        return

    op.drop_constraint(
        "ck_realized_trades_cumulative_dividends_non_negative",
        "realized_trades",
        type_="check",
    )
    op.drop_column("realized_trades", "cumulative_dividends")
