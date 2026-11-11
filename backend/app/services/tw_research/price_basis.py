"""Relative price-basis and valuation-position evidence (#337).

These labels are explanatory evidence for an external analyst. They are **not**
buy/sell instructions, Notion inclusion rules, or automatic rankings.

``PRICE_BASIS`` (unadjusted_close) remains the candle convention. Relative
categories here answer: where does the latest completed-session close sit versus
recent history and moving averages?
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.services.moving_average import completed_daily_candles
from app.services.tw_research.constants import (
    PRICE_BASIS,
    RELATIVE_PRICE_BASIS_HIGH_THRESHOLD,
    RELATIVE_PRICE_BASIS_LOW_THRESHOLD,
    RELATIVE_PRICE_BASIS_VERSION,
)
from app.services.tw_research.indicators import fifty_two_week_position, latest_sma

RelativePriceBasisCategory = Literal[
    "low_relative_price_basis",
    "mid_relative_price_basis",
    "high_relative_price_basis",
    "insufficient_data",
]

HistoryCoverageStatus = Literal[
    "complete_52w",
    "partial",
    "sparse",
    "insufficient",
]

# Separate missing PE / incomplete context from evidence-backed trap risk (#337).
ValueTrapStatus = Literal[
    "not_applicable",
    "not_indicated",
    "potential_trap",
    "not_evaluated_missing_pe",
]

TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class RelativePriceBasisEvidence:
    """Deterministic price-location evidence for one symbol."""

    category: RelativePriceBasisCategory
    version: str
    price_basis: str
    as_of: str | None
    latest_close: float | None
    week52_high: float | None
    week52_low: float | None
    week52_position: float | None
    drawdown_from_week52_high: float | None
    sma20: float | None
    sma60: float | None
    sma120: float | None
    sma240: float | None
    sma20_distance: float | None
    sma60_distance: float | None
    sma120_distance: float | None
    sma240_distance: float | None
    session_count: int
    coverage_sessions: int
    history_coverage_status: HistoryCoverageStatus
    stale_history_flag: bool
    corporate_action_status: Literal["unknown_unadjusted", "unavailable"]
    corporate_action_reason: str
    low_threshold: float
    high_threshold: float
    rationale: str
    potential_value_trap_flag: bool
    value_trap_status: ValueTrapStatus
    official_pe: float | None
    official_pe_missing: bool
    valuation_context_status: Literal["ok", "unavailable", "not_same_definition"]
    valuation_context_reason: str | None
    multi_year_price_location_status: Literal["not_calculated", "unavailable"]
    multi_year_price_location_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_relative_price_basis(
    candles: list[CandleRecord],
    *,
    market: str = "TW",
    now: datetime | None = None,
    official_pe: float | None = None,
    low_threshold: float = RELATIVE_PRICE_BASIS_LOW_THRESHOLD,
    high_threshold: float = RELATIVE_PRICE_BASIS_HIGH_THRESHOLD,
    value_trap_pe_threshold: float = 40.0,
) -> RelativePriceBasisEvidence:
    """Classify relative price basis from completed Taiwan sessions.

    Uses the shared ``fifty_two_week_position`` / ``latest_sma`` helpers — no
    duplicate calculation engine. Multi-year (3–5y) location stays
    ``not_calculated`` unless a future budgeted path requests it.
    """
    clock = now or datetime.now(TAIPEI)
    completed = completed_daily_candles(candles, market, now=clock)
    session_count = len(completed)
    if session_count < 2:
        return _insufficient(
            session_count=session_count,
            coverage_sessions=0,
            history_coverage_status="insufficient",
            stale_history_flag=False,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
            official_pe=official_pe,
            rationale="fewer_than_2_completed_sessions",
        )

    window = completed[-252:] if session_count >= 252 else completed
    coverage = len(window)
    high = max(float(c.high) for c in window)
    low = min(float(c.low) for c in window)
    close = float(window[-1].close)
    as_of = datetime.fromisoformat(window[-1].time).date().isoformat()
    position = fifty_two_week_position(completed)
    drawdown = None if high <= 0 else (high - close) / high

    coverage_status: HistoryCoverageStatus
    if coverage >= 240:
        coverage_status = "complete_52w"
    elif coverage >= 60:
        coverage_status = "partial"
    else:
        coverage_status = "sparse"

    stale = False
    try:
        last_day = datetime.fromisoformat(window[-1].time).astimezone(TAIPEI).date()
        today = clock.astimezone(TAIPEI).date() if clock.tzinfo else clock.replace(tzinfo=TAIPEI).date()
        # More than ~7 calendar days behind latest completed session ⇒ stale flag.
        stale = (today - last_day) > timedelta(days=7)
    except Exception:  # noqa: BLE001
        stale = False

    sma20 = latest_sma(completed, 20)
    sma60 = latest_sma(completed, 60)
    sma120 = latest_sma(completed, 120)
    sma240 = latest_sma(completed, 240)

    if position is None:
        return _insufficient(
            session_count=session_count,
            coverage_sessions=coverage,
            history_coverage_status=coverage_status,
            stale_history_flag=stale,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
            official_pe=official_pe,
            rationale="week52_position_unavailable",
            as_of=as_of,
            latest_close=close,
        )

    if position <= low_threshold:
        category: RelativePriceBasisCategory = "low_relative_price_basis"
        rationale = (
            f"week52_position={position:.4f} <= low_threshold={low_threshold}; "
            "low relative price basis is not cheap valuation"
        )
    elif position >= high_threshold:
        category = "high_relative_price_basis"
        rationale = (
            f"week52_position={position:.4f} >= high_threshold={high_threshold}"
        )
    else:
        category = "mid_relative_price_basis"
        rationale = (
            f"week52_position={position:.4f} between "
            f"{low_threshold} and {high_threshold}"
        )

    if coverage_status != "complete_52w":
        rationale += f"; history_coverage={coverage_status}"
    if stale:
        rationale += "; stale_history_flag"

    trap = False
    pe_missing = official_pe is None
    value_trap_status: ValueTrapStatus = "not_applicable"
    if category == "low_relative_price_basis":
        if pe_missing:
            # Missing PE is incomplete valuation evidence — not a trap signal.
            value_trap_status = "not_evaluated_missing_pe"
            rationale += (
                "; official_pe_missing; value_trap_not_evaluated; "
                "incomplete_valuation_context"
            )
        elif official_pe >= value_trap_pe_threshold:
            trap = True
            value_trap_status = "potential_trap"
            rationale += (
                f"; potential_value_trap_flag because official_pe={official_pe} "
                f">= {value_trap_pe_threshold}"
            )
        else:
            value_trap_status = "not_indicated"
            rationale += (
                f"; official_pe={official_pe} below trap threshold "
                f"{value_trap_pe_threshold}"
            )
    elif pe_missing:
        rationale += "; official_pe_missing"

    return RelativePriceBasisEvidence(
        category=category,
        version=RELATIVE_PRICE_BASIS_VERSION,
        price_basis=PRICE_BASIS,
        as_of=as_of,
        latest_close=close,
        week52_high=high,
        week52_low=low,
        week52_position=position,
        drawdown_from_week52_high=drawdown,
        sma20=sma20,
        sma60=sma60,
        sma120=sma120,
        sma240=sma240,
        sma20_distance=_distance(close, sma20),
        sma60_distance=_distance(close, sma60),
        sma120_distance=_distance(close, sma120),
        sma240_distance=_distance(close, sma240),
        session_count=session_count,
        coverage_sessions=coverage,
        history_coverage_status=coverage_status,
        stale_history_flag=stale,
        corporate_action_status="unknown_unadjusted",
        corporate_action_reason=(
            "PRICE_BASIS=unadjusted_close; split/adjustment ambiguity not resolved"
        ),
        low_threshold=low_threshold,
        high_threshold=high_threshold,
        rationale=rationale,
        potential_value_trap_flag=trap,
        value_trap_status=value_trap_status,
        official_pe=official_pe,
        official_pe_missing=pe_missing,
        valuation_context_status="unavailable",
        valuation_context_reason=(
            "historical_pe_pb_range_same_definition_source_not_verified"
        ),
        multi_year_price_location_status="not_calculated",
        multi_year_price_location_reason=(
            "deferred_until_explicit_budgeted_request; first_pass_avoids_3_to_5y_backfill"
        ),
    )


def _distance(close: float, sma: float | None) -> float | None:
    if sma is None or sma == 0:
        return None
    return (close - sma) / sma


def _insufficient(
    *,
    session_count: int,
    coverage_sessions: int,
    history_coverage_status: HistoryCoverageStatus,
    stale_history_flag: bool,
    low_threshold: float,
    high_threshold: float,
    official_pe: float | None,
    rationale: str,
    as_of: str | None = None,
    latest_close: float | None = None,
) -> RelativePriceBasisEvidence:
    return RelativePriceBasisEvidence(
        category="insufficient_data",
        version=RELATIVE_PRICE_BASIS_VERSION,
        price_basis=PRICE_BASIS,
        as_of=as_of,
        latest_close=latest_close,
        week52_high=None,
        week52_low=None,
        week52_position=None,
        drawdown_from_week52_high=None,
        sma20=None,
        sma60=None,
        sma120=None,
        sma240=None,
        sma20_distance=None,
        sma60_distance=None,
        sma120_distance=None,
        sma240_distance=None,
        session_count=session_count,
        coverage_sessions=coverage_sessions,
        history_coverage_status=history_coverage_status,
        stale_history_flag=stale_history_flag,
        corporate_action_status="unknown_unadjusted",
        corporate_action_reason=(
            "PRICE_BASIS=unadjusted_close; split/adjustment ambiguity not resolved"
        ),
        low_threshold=low_threshold,
        high_threshold=high_threshold,
        rationale=rationale,
        potential_value_trap_flag=False,
        value_trap_status="not_applicable",
        official_pe=official_pe,
        official_pe_missing=official_pe is None,
        valuation_context_status="unavailable",
        valuation_context_reason=(
            "historical_pe_pb_range_same_definition_source_not_verified"
        ),
        multi_year_price_location_status="not_calculated",
        multi_year_price_location_reason=(
            "deferred_until_explicit_budgeted_request; first_pass_avoids_3_to_5y_backfill"
        ),
    )
