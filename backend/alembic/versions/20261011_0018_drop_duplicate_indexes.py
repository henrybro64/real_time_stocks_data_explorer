"""drop indexes that duplicate unique constraints

Revision ID: 20261011_0018
Revises: 20261011_0017
Create Date: 2026-10-11

The unique constraints already index these columns. The extra lookup indexes
(#274) only add write cost on the daily Taiwan refresh and ranking capture.
"""

from alembic import op


revision = "20261011_0018"
down_revision = "20261011_0017"
branch_labels = None
depends_on = None

_DUPLICATE_INDEXES = (
    ("ix_taiwan_institutional_daily_lookup", "taiwan_institutional_daily", ["market", "symbol", "date"]),
    ("ix_taiwan_margin_short_daily_lookup", "taiwan_margin_short_daily", ["market", "symbol", "date"]),
    ("ix_daily_price_bars_lookup", "daily_price_bars", ["market", "symbol", "date"]),
    (
        "ix_ranking_snapshots_lookup",
        "ranking_snapshots",
        ["market", "tw_board", "ranking_type", "bucket_start", "capture_scope"],
    ),
)


def upgrade() -> None:
    for index_name, table_name, _columns in _DUPLICATE_INDEXES:
        op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    for index_name, table_name, columns in reversed(_DUPLICATE_INDEXES):
        op.create_index(index_name, table_name, columns)
