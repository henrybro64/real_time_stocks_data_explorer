"""Audited research field/source map for #310 (versioned contract)."""

from __future__ import annotations

from app.schemas.tw_research import FieldProvenance, ResearchContractResponse
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    CONTRACT_VERSION,
    PRICE_BASIS,
    RESEARCH_SCHEMA_VERSION,
    UNIVERSE_PRESET_VERSION,
)

# Compact rows keep the audited map readable without repeating FieldProvenance boilerplate.
# Columns: field, source, freshness_class, validation, missing.
_FIELD_ROWS: tuple[tuple[str, str, str, str, bool], ...] = (
    (
        "stock_id",
        "catalog",
        "config",
        "canonical TW:NNNN via quote_service.get_catalog_stock",
        False,
    ),
    ("exchange", "catalog", "config", "TWSE|TPEx", False),
    (
        "sector_theme_tags",
        "stock_theme_service",
        "config",
        "optional metadata; not an eligibility whitelist",
        False,
    ),
    (
        "ohlcv_daily",
        "daily_price_history_service / TWSE|TPEx month APIs",
        "quote_or_eod_day",
        "completed trading day only; source_as_of is session date",
        False,
    ),
    (
        "macd_kd_daily",
        "tw_research.indicators",
        "computed",
        "MACD(12,26,9) KD(9,3,3); pass=DIF>0 & OSC>0 & K>50 & D>50",
        False,
    ),
    (
        "macd_kd_monthly",
        "tw_research.indicators",
        "monthly_closed_month",
        "fully closed calendar months only; never current month",
        False,
    ),
    (
        "sma_20_60_120_240",
        "tw_research.indicators",
        "computed",
        "completed daily closes; unadjusted",
        False,
    ),
    (
        "week52_position",
        "tw_research.indicators",
        "computed",
        "trailing up to 252 completed sessions",
        False,
    ),
    (
        "institutional_net_buys_5d",
        "tw_institutional_service (prefer persisted daily store)",
        "quote_or_eod_day",
        "five completed sessions; lots via net_shares_to_lots; #313",
        False,
    ),
    (
        "tdcc_holders_ge_400_lots",
        "tw_ownership_concentration_service",
        "tdcc_weekly",
        "weekly publication date + prior-week change; never same-day stamp; #313",
        False,
    ),
    (
        "margin_balance_and_delta",
        "tw_margin_short_service",
        "quote_or_eod_day",
        "balance + approx 5-session delta; utilization only when both sides verified",
        False,
    ),
    (
        "margin_utilization",
        "tw_margin_short_service",
        "quote_or_eod_day",
        "unavailable unless numerator+denominator both verified",
        True,
    ),
    (
        "official_pe",
        "key_stats_service official TWSE/TPEx daily P/E",
        "quote_or_eod_day",
        "official_daily type; do not reverse-engineer EPS from PE",
        False,
    ),
    (
        "ttm_eps_pe_peg",
        "key_stats_service / none",
        "earnings_quarterly",
        "TTM EPS often unavailable on TW official PE path; PEG null without growth denom",
        True,
    ),
    (
        "reference_levels",
        "stock_analysis_key_price_levels",
        "computed",
        "only when computable; final trade judgments belong outside Stock Ticker",
        False,
    ),
)


def build_research_contract() -> ResearchContractResponse:
    fields = [
        FieldProvenance(
            field=field,
            source=source,
            freshness_class=freshness,
            validation=validation,
            missing=missing,
        )
        for field, source, freshness, validation, missing in _FIELD_ROWS
    ]
    return ResearchContractResponse(
        contractVersion=CONTRACT_VERSION,
        schemaVersion=RESEARCH_SCHEMA_VERSION,
        calculationVersion=CALCULATION_VERSION,
        universePresetVersion=UNIVERSE_PRESET_VERSION,
        priceBasis=PRICE_BASIS,
        fieldMap=fields,
        notes=[
            "Source timestamps must not be overwritten with collection time.",
            "Distinguish quote/eod day, monthly closed month, TDCC weekly, earnings quarterly.",
            "Starter 14-sector ~80-stock list is an optional preset, not a hard universe cap.",
            "Closed issues #269/#271/#272/#274 already provide persisted candles, MIS batching, "
            "route auth, and related optimizations — reuse them; do not reimplement.",
            "Enrichment (#313) runs only for day+month technical passers; missing chip data is "
            "per-field unavailable and never flips a technical pass to fail.",
            "Export/UI (#314) reads precomputed state only. External Notion publish is #315 Agent-owned.",
            "Home-node CPU/RSS/disk measurements belong to the manual pilot (#315); code ships "
            "provisional unit benchmarks and a measurement worksheet only.",
        ],
    )
