"""TW research cross-pod coordination leases (#329/#330)

Revision ID: 20261110_0022
Revises: 20261109_0021
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20261110_0022"
down_revision = "20261109_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tw_research_scans",
        sa.Column(
            "scan_lease_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "tw_research_scans",
        sa.Column(
            "budget_windows_opened",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.create_table(
        "tw_research_history_flights",
        sa.Column("flight_key", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("compatibility_key", sa.String(length=256), nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=False),
        sa.Column(
            "generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("flight_key"),
    )
    op.create_index(
        "ix_tw_research_history_flights_stock",
        "tw_research_history_flights",
        ["stock_id", "state"],
        unique=False,
    )

    op.create_table(
        "tw_research_coordination_locks",
        sa.Column("lock_name", sa.String(length=128), nullable=False),
        sa.Column("holder_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("lock_name"),
    )
    op.execute(
        sa.text(
            "INSERT INTO tw_research_coordination_locks (lock_name, holder_token, lease_expires_at) "
            "VALUES ('scan_admission', NULL, NULL)"
        )
    )


def downgrade() -> None:
    op.drop_table("tw_research_coordination_locks")
    op.drop_index(
        "ix_tw_research_history_flights_stock",
        table_name="tw_research_history_flights",
    )
    op.drop_table("tw_research_history_flights")
    op.drop_column("tw_research_scans", "budget_windows_opened")
    op.drop_column("tw_research_scans", "scan_lease_generation")
