"""Durable research_run orchestration ledger (#341)

Revision ID: 20261112_0025
Revises: 20261111_0024
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20261112_0025"
down_revision = "20261111_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tw_research_research_runs",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("research_run_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("trading_day", sa.String(length=10), nullable=True),
        sa.Column("first_pass_job_id", sa.String(length=64), nullable=True),
        sa.Column("first_pass_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("candidate_set_id", sa.String(length=64), nullable=True),
        sa.Column("candidate_set_version", sa.Integer(), nullable=True),
        sa.Column("technical_scan_id", sa.String(length=64), nullable=True),
        sa.Column("gates_json", sa.JSON(), nullable=False),
        sa.Column("dimensions_json", sa.JSON(), nullable=False),
        sa.Column("notes_json", sa.JSON(), nullable=False),
        sa.Column(
            "schedule_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint(
            "research_run_id",
            name="uq_tw_research_research_runs_run_id",
        ),
    )
    op.create_index(
        "ix_tw_research_research_runs_updated",
        "tw_research_research_runs",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tw_research_research_runs_updated",
        table_name="tw_research_research_runs",
    )
    op.drop_table("tw_research_research_runs")
