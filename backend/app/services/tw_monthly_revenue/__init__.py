"""Official TWSE/TPEx latest-month revenue collection (#362 Milestone 1)."""

from app.services.tw_monthly_revenue.service import (
    PARSER_VERSION,
    BoardIngestResult,
    collect_latest_monthly_revenue,
    get_monthly_revenue_for_stock,
    ingest_board_monthly_revenue,
)

__all__ = [
    "PARSER_VERSION",
    "BoardIngestResult",
    "collect_latest_monthly_revenue",
    "get_monthly_revenue_for_stock",
    "ingest_board_monthly_revenue",
]
