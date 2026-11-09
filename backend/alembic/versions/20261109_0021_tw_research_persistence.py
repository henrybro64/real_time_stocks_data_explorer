"""persist Taiwan research scans and immutable export snapshots (#318)

Revision ID: 20261109_0021
Revises: 20261011_0020
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20261109_0021"
down_revision = "20261011_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tw_research_scans",
        sa.Column("scan_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("trading_day", sa.String(length=10), nullable=False),
        sa.Column("universe_version", sa.String(length=128), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("enrichment_version", sa.String(length=64), nullable=False),
        sa.Column("selector_json", sa.JSON(), nullable=False),
        sa.Column("stock_ids_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserve_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserve_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrich_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enrich_reserve_ids_json", sa.JSON(), nullable=False),
        sa.Column("enrich_reserve_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enrich_lease_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("upstream_month_fetches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_upstream_month_fetches", sa.Integer(), nullable=False),
        sa.Column("enrichments_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_enrichments_per_run", sa.Integer(), nullable=False),
        sa.Column("day_pass_ids_json", sa.JSON(), nullable=False),
        sa.Column("qualified_ids_json", sa.JSON(), nullable=False),
        sa.Column("notes_json", sa.JSON(), nullable=False),
        sa.Column("active_snapshot_id", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("scan_id"),
    )
    op.create_index(
        "ix_tw_research_scans_universe_trading_day",
        "tw_research_scans",
        ["universe_version", "trading_day", "status"],
        unique=False,
    )

    op.create_table(
        "tw_research_symbol_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_id", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("trading_day", sa.String(length=10), nullable=False),
        sa.Column("universe_version", sa.String(length=128), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("readiness", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["scan_id"], ["tw_research_scans.scan_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scan_id",
            "stock_id",
            name="uq_tw_research_symbol_results_scan_stock",
        ),
        sa.UniqueConstraint(
            "trading_day",
            "universe_version",
            "calculation_version",
            "stock_id",
            "scan_id",
            name="uq_tw_research_checkpoint_identity",
        ),
    )
    op.create_index(
        "ix_tw_research_symbol_results_scan",
        "tw_research_symbol_results",
        ["scan_id", "stock_id"],
        unique=False,
    )

    op.create_table(
        "tw_research_export_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        # Nullable + SET NULL so retention can drop scan rows without deleting
        # immutable verified snapshots (#318).
        sa.Column("scan_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("universe_version", sa.String(length=128), nullable=False),
        sa.Column("trading_day", sa.String(length=10), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("enrichment_version", sa.String(length=64), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("ledger_json", sa.JSON(), nullable=False),
        sa.Column("chunk_digests_json", sa.JSON(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["tw_research_scans.scan_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "user_id",
            "content_digest",
            name="uq_tw_research_export_user_content_digest",
        ),
    )
    op.create_index(
        "ix_tw_research_export_latest",
        "tw_research_export_snapshots",
        ["universe_version", "trading_day", "quality_status", "finalized_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tw_research_export_latest", table_name="tw_research_export_snapshots")
    op.drop_table("tw_research_export_snapshots")
    op.drop_index("ix_tw_research_symbol_results_scan", table_name="tw_research_symbol_results")
    op.drop_table("tw_research_symbol_results")
    op.drop_index("ix_tw_research_scans_universe_trading_day", table_name="tw_research_scans")
    op.drop_table("tw_research_scans")
