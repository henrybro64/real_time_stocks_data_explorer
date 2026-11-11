"""TW research membership, first-pass snapshots, candidate sets (#335/#336)

Revision ID: 20261111_0023
Revises: 20261110_0022
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261111_0023"
down_revision = "20261110_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tw_research_memberships",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("theme_keys_json", sa.JSON(), nullable=False),
        sa.Column("extra_stock_ids_json", sa.JSON(), nullable=False),
        sa.Column("compute_subset_ids_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "tw_research_first_pass_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("universe_version", sa.String(length=128), nullable=False),
        sa.Column("first_pass_version", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("stock_ids_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "user_id",
            "content_digest",
            name="uq_tw_research_first_pass_user_digest",
        ),
    )
    op.create_index(
        "ix_tw_research_first_pass_user_created",
        "tw_research_first_pass_snapshots",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "tw_research_candidate_sets",
        sa.Column("candidate_set_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("first_pass_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("first_pass_digest", sa.String(length=64), nullable=False),
        sa.Column("stock_ids_json", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("selection_reasons_json", sa.JSON(), nullable=False),
        sa.Column("decision_date", sa.String(length=10), nullable=True),
        sa.Column("notes_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["first_pass_snapshot_id"],
            ["tw_research_first_pass_snapshots.snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("candidate_set_id"),
        sa.UniqueConstraint(
            "user_id",
            "version",
            name="uq_tw_research_candidate_sets_user_version",
        ),
    )
    op.create_index(
        "ix_tw_research_candidate_sets_user_created",
        "tw_research_candidate_sets",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tw_research_candidate_sets_user_created",
        table_name="tw_research_candidate_sets",
    )
    op.drop_table("tw_research_candidate_sets")
    op.drop_index(
        "ix_tw_research_first_pass_user_created",
        table_name="tw_research_first_pass_snapshots",
    )
    op.drop_table("tw_research_first_pass_snapshots")
    op.drop_table("tw_research_memberships")
