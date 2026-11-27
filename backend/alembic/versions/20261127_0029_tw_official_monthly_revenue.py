"""Official TW monthly-revenue raw fetches, observations, revisions (#362 M1).

Additive only — does not rewrite existing research-result rows.

Revision ID: 20261127_0029
Revises: 20261114_0028
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20261127_0029"
down_revision = "20261114_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tw_fundamental_raw_fetches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("board", sa.String(length=8), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("request_method", sa.String(length=8), nullable=False),
        sa.Column("request_headers_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("etag", sa.String(length=256), nullable=True),
        sa.Column("last_modified", sa.String(length=128), nullable=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=True),
        sa.Column("body_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_body", sa.LargeBinary(), nullable=True),
        sa.Column("reporting_period_yyyymm", sa.String(length=6), nullable=True),
        sa.Column("report_generated_date", sa.Date(), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_errors_json", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tw_fundamental_raw_fetches_board_dataset_fetched",
        "tw_fundamental_raw_fetches",
        ["board", "dataset", "fetched_at"],
        unique=False,
    )
    op.create_index(
        "ix_tw_fundamental_raw_fetches_accepted",
        "tw_fundamental_raw_fetches",
        ["board", "dataset", "validation_status", "fetched_at"],
        unique=False,
    )

    op.create_table(
        "tw_monthly_revenue_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("board", sa.String(length=8), nullable=False),
        sa.Column("company_code", sa.String(length=16), nullable=False),
        sa.Column("company_name", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=64), nullable=True),
        sa.Column("period_yyyymm", sa.String(length=6), nullable=False),
        sa.Column("report_generated_date", sa.Date(), nullable=True),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("revenue_nt_thousand", sa.Float(), nullable=True),
        sa.Column("prev_month_revenue_nt_thousand", sa.Float(), nullable=True),
        sa.Column("year_ago_revenue_nt_thousand", sa.Float(), nullable=True),
        sa.Column("mom_percent", sa.Float(), nullable=True),
        sa.Column("yoy_percent", sa.Float(), nullable=True),
        sa.Column("ytd_revenue_nt_thousand", sa.Float(), nullable=True),
        sa.Column("ytd_year_ago_revenue_nt_thousand", sa.Float(), nullable=True),
        sa.Column("ytd_yoy_percent", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("missing_fields_json", sa.JSON(), nullable=False),
        sa.Column("value_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_fetch_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_fetch_id"],
            ["tw_fundamental_raw_fetches.id"],
            name="fk_tw_monthly_revenue_obs_raw_fetch",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_id",
            "period_yyyymm",
            "basis",
            name="uq_tw_monthly_revenue_stock_period_basis",
        ),
    )
    op.create_index(
        "ix_tw_monthly_revenue_board_period",
        "tw_monthly_revenue_observations",
        ["board", "period_yyyymm"],
        unique=False,
    )

    op.create_table(
        "tw_monthly_revenue_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_value_hash", sa.String(length=64), nullable=False),
        sa.Column("new_value_hash", sa.String(length=64), nullable=False),
        sa.Column("old_values_json", sa.JSON(), nullable=False),
        sa.Column("new_values_json", sa.JSON(), nullable=False),
        sa.Column("previous_raw_fetch_id", sa.Integer(), nullable=True),
        sa.Column("new_raw_fetch_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["tw_monthly_revenue_observations.id"],
            name="fk_tw_monthly_revenue_rev_obs",
        ),
        sa.ForeignKeyConstraint(
            ["previous_raw_fetch_id"],
            ["tw_fundamental_raw_fetches.id"],
            name="fk_tw_monthly_revenue_rev_prev_raw",
        ),
        sa.ForeignKeyConstraint(
            ["new_raw_fetch_id"],
            ["tw_fundamental_raw_fetches.id"],
            name="fk_tw_monthly_revenue_rev_new_raw",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tw_monthly_revenue_revisions_obs_changed",
        "tw_monthly_revenue_revisions",
        ["observation_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tw_monthly_revenue_revisions_obs_changed",
        table_name="tw_monthly_revenue_revisions",
    )
    op.drop_table("tw_monthly_revenue_revisions")
    op.drop_index(
        "ix_tw_monthly_revenue_board_period",
        table_name="tw_monthly_revenue_observations",
    )
    op.drop_table("tw_monthly_revenue_observations")
    op.drop_index(
        "ix_tw_fundamental_raw_fetches_accepted",
        table_name="tw_fundamental_raw_fetches",
    )
    op.drop_index(
        "ix_tw_fundamental_raw_fetches_board_dataset_fetched",
        table_name="tw_fundamental_raw_fetches",
    )
    op.drop_table("tw_fundamental_raw_fetches")
