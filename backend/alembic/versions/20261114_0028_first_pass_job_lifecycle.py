"""First-pass job archive/deletion lifecycle columns + audit (#355).

Additive only — does not touch existing job rows.

Revision ID: 20261114_0028
Revises: 20261112_0027
Create Date: 2026-11-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20261114_0028"
down_revision = "20261112_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tw_research_first_pass_jobs",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tw_research_first_pass_jobs",
        sa.Column("deletion_state", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "tw_research_first_pass_jobs",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tw_research_first_pass_jobs",
        sa.Column("deletion_error", sa.String(length=256), nullable=True),
    )
    op.create_index(
        "ix_tw_research_first_pass_jobs_user_archived",
        "tw_research_first_pass_jobs",
        ["user_id", "archived_at", "updated_at"],
    )
    op.create_table(
        "tw_research_deletion_audit",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_tw_research_deletion_audit_user_requested",
        "tw_research_deletion_audit",
        ["user_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tw_research_deletion_audit_user_requested",
        table_name="tw_research_deletion_audit",
    )
    op.drop_table("tw_research_deletion_audit")
    op.drop_index(
        "ix_tw_research_first_pass_jobs_user_archived",
        table_name="tw_research_first_pass_jobs",
    )
    op.drop_column("tw_research_first_pass_jobs", "deletion_error")
    op.drop_column("tw_research_first_pass_jobs", "deletion_requested_at")
    op.drop_column("tw_research_first_pass_jobs", "deletion_state")
    op.drop_column("tw_research_first_pass_jobs", "archived_at")
