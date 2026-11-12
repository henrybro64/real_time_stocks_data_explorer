"""Add fenced job/scan claim columns to research runs (#341)

Revision ID: 20261112_0027
Revises: 20261112_0026
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20261112_0027"
down_revision = "20261112_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tw_research_research_runs",
        sa.Column("job_claim_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tw_research_research_runs",
        sa.Column("job_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tw_research_research_runs",
        sa.Column("pending_first_pass_job_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tw_research_research_runs",
        sa.Column("phase2_claim_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tw_research_research_runs",
        sa.Column("phase2_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tw_research_research_runs",
        sa.Column("pending_technical_scan_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tw_research_research_runs", "pending_technical_scan_id")
    op.drop_column("tw_research_research_runs", "phase2_claim_expires_at")
    op.drop_column("tw_research_research_runs", "phase2_claim_token")
    op.drop_column("tw_research_research_runs", "pending_first_pass_job_id")
    op.drop_column("tw_research_research_runs", "job_claim_expires_at")
    op.drop_column("tw_research_research_runs", "job_claim_token")
