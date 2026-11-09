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


def build_research_contract() -> ResearchContractResponse:
    fields = [
        FieldProvenance(
            field="stock_id",
            source="catalog",
            freshness_class="config",
            validation="canonical TW:NNNN via quote_service.get_catalog_stock",
        ),
        FieldProvenance(
            field="exchange",
            source="catalog",
            freshness_class="config",
            validation="TWSE|TPEx",
        ),
        FieldProvenance(
            field="sector_theme_tags",
            source="stock_theme_service",
            freshness_class="config",
            validation="optional metadata; not an eligibility whitelist",
        ),
        FieldProvenance(
            field="ohlcv_daily",
            source="daily_price_history_service / TWSE|TPEx month APIs",
            freshness_class="quote_or_eod_day",
            validation="completed trading day only; source_as_of is session date",
        ),
        FieldProvenance(
            field="macd_kd_daily",
            source="tw_research.indicators",
            freshness_class="computed",
            validation="MACD(12,26,9) KD(9,3,3); pass=DIF>0 & OSC>0 & K>50 & D>50",
        ),
        FieldProvenance(
            field="macd_kd_monthly",
            source="tw_research.indicators",
            freshness_class="monthly_closed_month",
            validation="fully closed calendar months only; never current month",
        ),
        FieldProvenance(
            field="sma_20_60_120_240",
            source="tw_research.indicators",
            freshness_class="computed",
            validation="completed daily closes; unadjusted",
        ),
        FieldProvenance(
            field="week52_position",
            source="tw_research.indicators",
            freshness_class="computed",
            validation="trailing up to 252 completed sessions",
        ),
        FieldProvenance(
            field="institutional_net_buys_5d",
            source="tw_institutional_service (prefer persisted daily store)",
            freshness_class="quote_or_eod_day",
            missing=False,
            validation="five completed sessions; lots via net_shares_to_lots; #313",
        ),
        FieldProvenance(
            field="tdcc_holders_ge_400_lots",
            source="tw_ownership_concentration_service",
            freshness_class="tdcc_weekly",
            missing=False,
            validation="weekly publication date + prior-week change; never same-day stamp; #313",
        ),
        FieldProvenance(
            field="margin_balance_and_delta",
            source="tw_margin_short_service",
            freshness_class="quote_or_eod_day",
            missing=False,
            validation="balance + approx 5-session delta; utilization only when both sides verified",
        ),
        FieldProvenance(
            field="margin_utilization",
            source="tw_margin_short_service",
            freshness_class="quote_or_eod_day",
            missing=True,
            validation="unavailable unless numerator+denominator both verified",
        ),
        FieldProvenance(
            field="official_pe",
            source="key_stats_service official TWSE/TPEx daily P/E",
            freshness_class="quote_or_eod_day",
            missing=False,
            validation="official_daily type; do not reverse-engineer EPS from PE",
        ),
        FieldProvenance(
            field="ttm_eps_pe_peg",
            source="key_stats_service / none",
            freshness_class="earnings_quarterly",
            missing=True,
            validation="TTM EPS often unavailable on TW official PE path; PEG null without growth denom",
        ),
        FieldProvenance(
            field="reference_levels",
            source="stock_analysis_key_price_levels",
            freshness_class="computed",
            missing=False,
            validation="only when computable; final trade judgments belong outside Stock Ticker",
        ),
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
