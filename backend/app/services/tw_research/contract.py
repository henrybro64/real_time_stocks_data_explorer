"""Audited research field/source map for #310 / #335 (versioned contract)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas.tw_research import FieldProvenance, ResearchContractResponse
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    CONTRACT_VERSION,
    FIRST_PASS_VERSION,
    PRICE_BASIS,
    RELATIVE_PRICE_BASIS_VERSION,
    RESEARCH_SCHEMA_VERSION,
    UNIVERSE_PRESET_VERSION,
)

_FIELD_MAP_PATH = Path(__file__).with_name("contract_fields.json")
_FEASIBILITY_PATH = Path(__file__).with_name("feasibility_matrix.json")


@lru_cache(maxsize=1)
def _field_rows() -> tuple[FieldProvenance, ...]:
    raw = json.loads(_FIELD_MAP_PATH.read_text(encoding="utf-8"))
    return tuple(FieldProvenance.model_validate(row) for row in raw)


@lru_cache(maxsize=1)
def load_feasibility_matrix() -> list[dict[str, Any]]:
    return list(json.loads(_FEASIBILITY_PATH.read_text(encoding="utf-8")))


def build_research_contract() -> ResearchContractResponse:
    return ResearchContractResponse(
        contractVersion=CONTRACT_VERSION,
        schemaVersion=RESEARCH_SCHEMA_VERSION,
        calculationVersion=CALCULATION_VERSION,
        universePresetVersion=UNIVERSE_PRESET_VERSION,
        priceBasis=PRICE_BASIS,
        firstPassVersion=FIRST_PASS_VERSION,
        relativePriceBasisVersion=RELATIVE_PRICE_BASIS_VERSION,
        fieldMap=list(_field_rows()),
        feasibilityMatrix=load_feasibility_matrix(),
        notes=[
            "Source timestamps must not be overwritten with collection time.",
            "Distinguish quote/eod day, monthly closed month, TDCC weekly, earnings quarterly.",
            "Starter 14-sector ~80-stock list is an optional preset, not a hard universe cap.",
            "Closed issues #269/#271/#272/#274 already provide persisted candles, MIS batching, "
            "route auth, and related optimizations — reuse them; do not reimplement.",
            "Enrichment (#313) runs only for day+month technical passers; missing chip data is "
            "per-field unavailable and never flips a technical pass to fail.",
            "First-pass (#335/#336) collects evidence for every configured universe symbol "
            "without second-pass MACD/KD and without fabricating missing fundamentals.",
            "Relative price basis (#337) is explanatory evidence, not a recommendation or "
            "Notion inclusion rule. Low relative price ≠ cheap valuation.",
            "Export/UI (#314) reads precomputed state only and never initiates market-data fetches.",
            "Stock Ticker publishes independently; it does not track consumer acknowledgment or write Notion.",
            "Home-node CPU/RSS/disk measurements belong to the manual pilot (#315); code ships "
            "provisional unit benchmarks and a measurement worksheet only.",
            "tw_insights monthly revenue is synthetic and must never be treated as reported fundamentals.",
        ],
    )
