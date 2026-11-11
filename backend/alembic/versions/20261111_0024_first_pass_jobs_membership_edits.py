"""First-pass jobs + editable sector membership columns (#335/#336)

Revision ID: 20261111_0024
Revises: 20261111_0023
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261111_0024"
down_revision = "20261111_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tw_research_memberships",
        sa.Column(
            "base_preset_version",
            sa.String(length=64),
            server_default="starter",
            nullable=False,
        ),
    )
    op.add_column(
        "tw_research_memberships",
        sa.Column(
            "disabled_theme_keys_json",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tw_research_memberships",
        sa.Column(
            "sector_additions_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tw_research_memberships",
        sa.Column(
            "sector_removals_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tw_research_memberships",
        sa.Column(
            "custom_sectors_json",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )

    op.create_table(
        "tw_research_first_pass_jobs",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("universe_version", sa.String(length=128), nullable=False),
        sa.Column("universe_digest", sa.String(length=64), nullable=False),
        sa.Column("membership_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_pass_version", sa.String(length=64), nullable=False),
        sa.Column("stock_ids_json", sa.JSON(), nullable=False),
        sa.Column("selector_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("cursor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserve_cursor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserve_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("notes_json", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_tw_research_first_pass_jobs_user_updated",
        "tw_research_first_pass_jobs",
        ["user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "tw_research_first_pass_symbol_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["tw_research_first_pass_jobs.job_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "stock_id",
            name="uq_tw_research_first_pass_symbol_job_stock",
        ),
    )
    op.create_index(
        "ix_tw_research_first_pass_symbol_job",
        "tw_research_first_pass_symbol_results",
        ["job_id", "stock_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tw_research_first_pass_symbol_job",
        table_name="tw_research_first_pass_symbol_results",
    )
    op.drop_table("tw_research_first_pass_symbol_results")
    op.drop_index(
        "ix_tw_research_first_pass_jobs_user_updated",
        table_name="tw_research_first_pass_jobs",
    )
    op.drop_table("tw_research_first_pass_jobs")
    op.drop_column("tw_research_memberships", "custom_sectors_json")
    op.drop_column("tw_research_memberships", "sector_removals_json")
    op.drop_column("tw_research_memberships", "sector_additions_json")
    op.drop_column("tw_research_memberships", "disabled_theme_keys_json")
    op.drop_column("tw_research_memberships", "base_preset_version")
