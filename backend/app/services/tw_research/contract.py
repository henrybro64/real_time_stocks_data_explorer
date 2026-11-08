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
            source="existing tw institutional routes/services",
            freshness_class="quote_or_eod_day",
            missing=True,
            validation="wired in later enrichment issue; contract reserved",
        ),
        FieldProvenance(
            field="tdcc_holders_ge_400_lots",
            source="existing ownership concentration services",
            freshness_class="tdcc_weekly",
            missing=True,
            validation="wired in later enrichment issue; contract reserved",
        ),
        FieldProvenance(
            field="margin_utilization",
            source="existing tw margin-short services",
            freshness_class="quote_or_eod_day",
            missing=True,
            validation="wired in later enrichment issue; contract reserved",
        ),
        FieldProvenance(
            field="ttm_eps_pe_peg",
            source="existing profitability / fundamental helpers",
            freshness_class="earnings_quarterly",
            missing=True,
            validation="PEG only when growth input defensible; later enrichment",
        ),
        FieldProvenance(
            field="reference_levels",
            source="stock_analysis_key_price_levels",
            freshness_class="computed",
            missing=True,
            validation="only when computable inputs exist; later enrichment",
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
            "Home-node CPU/RSS/disk measurements belong to the manual pilot (#315); this "
            "contract establishes the instrumentation plan only.",
        ],
    )
