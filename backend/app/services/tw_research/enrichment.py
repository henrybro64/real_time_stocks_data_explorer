"""Bounded enrichment for technically qualified Taiwan stocks (#313).

Only day+fully-closed-month technical passers are enriched. Missing chip or
valuation data becomes per-field ``unavailable`` with provenance — never a
technical fail, never invented zeros/PEG, and never sector-based exclusion.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.services.daily_price_history_service import daily_price_history_service
from app.services.moving_average import completed_daily_candles
from app.services.quote_service import quote_service
from app.services.stock_analysis_key_price_levels import build_key_price_levels
from app.services.taiwan_market_data import net_shares_to_lots
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorsResult,
    tw_institutional_service,
)
from app.services.tw_margin_short_service import (
    TaiwanMarginShortResult,
    tw_margin_short_service,
)
from app.services.tw_ownership_concentration_service import (
    TaiwanOwnershipConcentrationResult,
    tw_ownership_concentration_service,
)
from app.services.tw_research.constants import (
    ENRICHMENT_VERSION,
    PREFERRED_DAILY_HISTORY_LIMIT,
    PRICE_BASIS,
)
from app.services.tw_research.indicators import fifty_two_week_position

FieldStatus = Literal["ok", "unavailable", "partial"]


@dataclass(frozen=True)
class ProvenanceField:
    field: str
    status: FieldStatus
    source: str
    source_as_of: str | None = None
    captured_at: str | None = None
    freshness_class: str = "quote_or_eod_day"
    reason: str | None = None
    value: Any = None


@dataclass
class EnrichmentResult:
    stock_id: str
    qualified: bool
    enrichment_version: str = ENRICHMENT_VERSION
    fields: dict[str, ProvenanceField] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stockId": self.stock_id,
            "qualified": self.qualified,
            "enrichmentVersion": self.enrichment_version,
            "error": self.error,
            "fields": {key: asdict(value) for key, value in self.fields.items()},
        }


InstitutionalLoader = Callable[[str, int], TaiwanInstitutionalInvestorsResult]
MarginLoader = Callable[[str, int], TaiwanMarginShortResult]
OwnershipLoader = Callable[[str], TaiwanOwnershipConcentrationResult]
KeyStatsLoader = Callable[[str], dict[str, object] | None]
HistoryCandlesLoader = Callable[[StockRecord], list[CandleRecord]]
StockLoader = Callable[[str], StockRecord | None]


def is_technically_qualified(record: Any) -> bool:
    """Day pass and fully-closed-month pass; missing month is not enrichment-eligible."""
    daily = getattr(record, "daily", None) or {}
    monthly = getattr(record, "monthly", None) or {}
    if getattr(record, "stage", None) != "B":
        return False
    return daily.get("passStatus") == "pass" and monthly.get("passStatus") == "pass"


def enrich_qualified_symbol(
    stock_id: str,
    *,
    stock_loader: StockLoader | None = None,
    institutional_loader: InstitutionalLoader | None = None,
    margin_loader: MarginLoader | None = None,
    ownership_loader: OwnershipLoader | None = None,
    key_stats_loader: KeyStatsLoader | None = None,
    candles_loader: HistoryCandlesLoader | None = None,
    prior_week52_position: float | None = None,
    now: datetime | None = None,
) -> EnrichmentResult:
    """Enrich one dual-pass symbol. Isolates failures to this symbol only.

    When ``candles_loader`` is omitted, levels use **stored-only** daily history
    (``peek_daily_history`` / no upstream month backfill).

    ``now`` anchors completed-candle / indicator cutoffs (typically the scan
    trading day). ``captured_at`` is always the real wall-clock fetch time so
    enrichment provenance is not stamped with an artificial historical capture.
    """
    captured_at = datetime.now(UTC).isoformat()
    calculation_now = now
    result = EnrichmentResult(stock_id=stock_id, qualified=True)
    loader = stock_loader or quote_service.get_catalog_stock
    stock = loader(stock_id)
    if stock is None:
        result.qualified = False
        result.error = "not_in_catalog"
        return result

    try:
        _enrich_institutional(
            result,
            stock_id,
            loader=institutional_loader or _default_institutional,
            captured_at=captured_at,
            symbol=stock.symbol,
        )
    except Exception as exc:  # noqa: BLE001
        result.fields["institutional_5d"] = ProvenanceField(
            field="institutional_5d",
            status="unavailable",
            source="tw_institutional_service",
            captured_at=captured_at,
            reason=f"symbol_error:{type(exc).__name__}",
        )

    try:
        _enrich_margin(
            result,
            stock_id,
            loader=margin_loader or _default_margin,
            captured_at=captured_at,
        )
    except Exception as exc:  # noqa: BLE001
        result.fields["margin"] = ProvenanceField(
            field="margin",
            status="unavailable",
            source="tw_margin_short_service",
            captured_at=captured_at,
            reason=f"symbol_error:{type(exc).__name__}",
        )

    try:
        _enrich_ownership(
            result,
            stock_id,
            loader=ownership_loader or _default_ownership,
            captured_at=captured_at,
        )
    except Exception as exc:  # noqa: BLE001
        result.fields["tdcc_holders_ge_400_lots"] = ProvenanceField(
            field="tdcc_holders_ge_400_lots",
            status="unavailable",
            source="tw_ownership_concentration_service",
            captured_at=captured_at,
            freshness_class="tdcc_weekly",
            reason=f"symbol_error:{type(exc).__name__}",
        )

    try:
        _enrich_valuation(
            result,
            stock_id,
            loader=key_stats_loader or _default_key_stats,
            captured_at=captured_at,
        )
    except Exception as exc:  # noqa: BLE001
        result.fields["official_pe"] = ProvenanceField(
            field="official_pe",
            status="unavailable",
            source="key_stats_service",
            captured_at=captured_at,
            reason=f"symbol_error:{type(exc).__name__}",
        )

    try:
        _enrich_levels(
            result,
            stock,
            loader=candles_loader or _default_stored_candles_loader,
            prior_week52_position=prior_week52_position,
            captured_at=captured_at,
            now=calculation_now,
        )
    except Exception as exc:  # noqa: BLE001
        result.fields["reference_levels"] = ProvenanceField(
            field="reference_levels",
            status="unavailable",
            source="stock_analysis_key_price_levels",
            captured_at=captured_at,
            freshness_class="computed",
            reason=f"symbol_error:{type(exc).__name__}",
        )

    # PEG is never reverse-engineered from a mismatched PE period.
    if "peg" not in result.fields:
        result.fields["peg"] = ProvenanceField(
            field="peg",
            status="unavailable",
            source="none",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="verified_eps_growth_denominator_unavailable",
        )
    if "ttm_eps" not in result.fields:
        result.fields["ttm_eps"] = ProvenanceField(
            field="ttm_eps",
            status="unavailable",
            source="key_stats_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="ttm_eps_not_verified_for_taiwan_official_pe_path",
        )

    return result


def _default_institutional(stock_id: str, days: int) -> TaiwanInstitutionalInvestorsResult:
    return tw_institutional_service.get_institutional_investors(stock_id, days=days)


def _default_margin(stock_id: str, days: int) -> TaiwanMarginShortResult:
    return tw_margin_short_service.get_margin_short(stock_id, days=days)


def _default_ownership(stock_id: str) -> TaiwanOwnershipConcentrationResult:
    return tw_ownership_concentration_service.get_ownership_concentration(stock_id, range="3M")


def _default_key_stats(stock_id: str) -> dict[str, object] | None:
    from app.services.key_stats_service import get_key_statistics

    return get_key_statistics(stock_id)


def _default_stored_candles_loader(stock: StockRecord) -> list[CandleRecord]:
    """Read persisted bars only — never open a cold upstream month backfill (#313)."""
    candles, _missing = daily_price_history_service.peek_daily_history(
        stock,
        "MAX",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
    )
    return list(candles)


def _enrich_institutional(
    result: EnrichmentResult,
    stock_id: str,
    *,
    loader: InstitutionalLoader,
    captured_at: str,
    symbol: str,
) -> None:
    payload = loader(stock_id, 12)
    history = list(payload.history[-5:]) if payload.history else []
    if len(history) < 5:
        result.fields["institutional_5d"] = ProvenanceField(
            field="institutional_5d",
            status="unavailable" if not history else "partial",
            source=payload.source,
            source_as_of=payload.latest_date,
            captured_at=captured_at,
            reason="fewer_than_5_completed_sessions_in_store",
            value=[_institutional_point(row, symbol) for row in history] or None,
        )
        return
    result.fields["institutional_5d"] = ProvenanceField(
        field="institutional_5d",
        status="ok",
        source=payload.source,
        source_as_of=payload.latest_date,
        captured_at=captured_at,
        value=[_institutional_point(row, symbol) for row in history],
    )


def _institutional_point(row: Any, symbol: str) -> dict[str, Any]:
    return {
        "date": row.date,
        "foreignNetLots": net_shares_to_lots(row.foreign_net_shares, symbol=symbol),
        "trustNetLots": net_shares_to_lots(row.investment_trust_net_shares, symbol=symbol),
        "dealerNetLots": net_shares_to_lots(row.dealer_net_shares, symbol=symbol),
        "totalNetLots": net_shares_to_lots(row.total_net_shares, symbol=symbol),
    }


def _enrich_margin(
    result: EnrichmentResult,
    stock_id: str,
    *,
    loader: MarginLoader,
    captured_at: str,
) -> None:
    payload = loader(stock_id, 12)
    history = list(payload.history[-5:]) if payload.history else []
    latest = payload.summary
    delta = None
    if len(history) >= 2 and history[-1].margin_balance_lots is not None:
        prior = history[0].margin_balance_lots
        current = history[-1].margin_balance_lots
        if prior is not None and current is not None:
            delta = current - prior

    utilization = ProvenanceField(
        field="margin_utilization",
        status="unavailable",
        source=payload.source,
        source_as_of=payload.latest_date,
        captured_at=captured_at,
        reason="numerator_and_denominator_not_both_verified_in_margin_short_payload",
    )
    result.fields["margin_utilization"] = utilization

    if latest is None or latest.margin_balance_lots is None:
        result.fields["margin"] = ProvenanceField(
            field="margin",
            status="unavailable",
            source=payload.source,
            source_as_of=payload.latest_date,
            captured_at=captured_at,
            reason=payload.empty_reason or "margin_balance_missing",
        )
        return

    result.fields["margin"] = ProvenanceField(
        field="margin",
        status="ok" if delta is not None else "partial",
        source=payload.source,
        source_as_of=payload.latest_date,
        captured_at=captured_at,
        reason=None if delta is not None else "fewer_than_2_sessions_for_5d_delta",
        value={
            "balanceLots": latest.margin_balance_lots,
            "balanceDeltaLotsApprox5Sessions": delta,
            "historyDates": [row.date for row in history],
        },
    )


def _enrich_ownership(
    result: EnrichmentResult,
    stock_id: str,
    *,
    loader: OwnershipLoader,
    captured_at: str,
) -> None:
    payload = loader(stock_id)
    points = list(payload.points)
    if not points:
        result.fields["tdcc_holders_ge_400_lots"] = ProvenanceField(
            field="tdcc_holders_ge_400_lots",
            status="unavailable",
            source=payload.source,
            source_as_of=payload.as_of,
            captured_at=captured_at,
            freshness_class="tdcc_weekly",
            reason="tdcc_weekly_points_missing",
            value=None,
        )
        return

    latest = points[-1]
    previous = points[-2] if len(points) >= 2 else None
    change = None
    if (
        previous is not None
        and latest.holders_400_lots_or_more_percent is not None
        and previous.holders_400_lots_or_more_percent is not None
    ):
        change = latest.holders_400_lots_or_more_percent - previous.holders_400_lots_or_more_percent

    status: FieldStatus = "ok"
    reason = None
    if latest.holders_400_lots_or_more_percent is None:
        status = "unavailable"
        reason = "holders_400_lots_percent_missing"
    elif previous is None:
        status = "partial"
        reason = "prior_weekly_point_missing_for_change"

    result.fields["tdcc_holders_ge_400_lots"] = ProvenanceField(
        field="tdcc_holders_ge_400_lots",
        status=status,
        source=payload.source,
        source_as_of=latest.date,
        captured_at=captured_at,
        freshness_class="tdcc_weekly",
        reason=reason,
        value={
            "percent": latest.holders_400_lots_or_more_percent,
            "weeklyChangePercentPoints": change,
            "priorWeekDate": previous.date if previous is not None else None,
            "stale": payload.stale,
            "note": "Weekly TDCC publication date; not a same-day daily change.",
        },
    )


def _enrich_valuation(
    result: EnrichmentResult,
    stock_id: str,
    *,
    loader: KeyStatsLoader,
    captured_at: str,
) -> None:
    stats = loader(stock_id) or {}
    pe = stats.get("peRatio")
    pe_type = stats.get("peRatioType")
    pe_source = stats.get("peRatioSource")
    valuation_as_of = stats.get("valuationAsOf")
    warning = stats.get("valuationWarning")

    if pe is None:
        result.fields["official_pe"] = ProvenanceField(
            field="official_pe",
            status="unavailable",
            source=str(pe_source or "key_stats_service"),
            source_as_of=str(valuation_as_of) if valuation_as_of else None,
            captured_at=captured_at,
            reason=str(warning or "official_pe_unavailable"),
        )
    else:
        result.fields["official_pe"] = ProvenanceField(
            field="official_pe",
            status="ok",
            source=str(pe_source or "key_stats_service"),
            source_as_of=str(valuation_as_of) if valuation_as_of else None,
            captured_at=captured_at,
            value={"peRatio": pe, "peRatioType": pe_type},
        )

    eps = stats.get("epsTtm")
    if eps is None:
        result.fields["ttm_eps"] = ProvenanceField(
            field="ttm_eps",
            status="unavailable",
            source="key_stats_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="ttm_eps_not_verified_for_taiwan_official_pe_path",
        )
    elif isinstance(eps, (int, float)) and float(eps) < 0:
        result.fields["ttm_eps"] = ProvenanceField(
            field="ttm_eps",
            status="partial",
            source="key_stats_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="negative_eps",
            value=float(eps),
        )
        result.fields["peg"] = ProvenanceField(
            field="peg",
            status="unavailable",
            source="none",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="peg_invalid_with_negative_eps",
        )
    else:
        result.fields["ttm_eps"] = ProvenanceField(
            field="ttm_eps",
            status="ok",
            source="key_stats_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            value=float(eps),
        )
        result.fields["peg"] = ProvenanceField(
            field="peg",
            status="unavailable",
            source="none",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="verified_eps_growth_denominator_unavailable",
        )


def _enrich_levels(
    result: EnrichmentResult,
    stock: StockRecord,
    *,
    loader: HistoryCandlesLoader,
    prior_week52_position: float | None,
    captured_at: str,
    now: datetime | None,
) -> None:
    candles = list(loader(stock) or [])
    completed = completed_daily_candles(candles, "TW", now=now)
    week52 = fifty_two_week_position(completed)
    week52_source = "tw_research.indicators"
    week52_reason: str | None = None
    if week52 is None and prior_week52_position is not None:
        # Prefer Stage B value already computed on the scan ledger when the
        # stored-only peek is empty (common right after a cold scan budget).
        week52 = prior_week52_position
        week52_source = "scan_stage_b"
    elif week52 is None:
        week52_reason = (
            "stored_history_empty_or_incomplete"
            if not completed
            else "insufficient_completed_sessions"
        )
    result.fields["week52_position"] = ProvenanceField(
        field="week52_position",
        status="ok" if week52 is not None else "unavailable",
        source=week52_source,
        captured_at=captured_at,
        freshness_class="computed",
        reason=week52_reason,
        value=week52,
    )
    if not completed:
        result.fields["reference_levels"] = ProvenanceField(
            field="reference_levels",
            status="unavailable",
            source="stock_analysis_key_price_levels",
            captured_at=captured_at,
            freshness_class="computed",
            reason="stored_history_empty_or_incomplete",
        )
        result.fields["price_basis"] = ProvenanceField(
            field="price_basis",
            status="ok",
            source="config",
            captured_at=captured_at,
            freshness_class="config",
            value=PRICE_BASIS,
        )
        return
    as_of = datetime.fromisoformat(completed[-1].time).date().isoformat()
    levels = build_key_price_levels(
        completed,
        currency="TWD",
        data_as_of=as_of,
        reference_price=float(completed[-1].close),
    )
    availability = levels.get("availability")
    result.fields["reference_levels"] = ProvenanceField(
        field="reference_levels",
        status="ok" if availability == "ok" else "unavailable",
        source="stock_analysis_key_price_levels",
        source_as_of=as_of,
        captured_at=captured_at,
        freshness_class="computed",
        reason=None if availability == "ok" else f"levels_{availability}",
        value=levels if availability == "ok" else None,
    )
    result.fields["price_basis"] = ProvenanceField(
        field="price_basis",
        status="ok",
        source="config",
        captured_at=captured_at,
        freshness_class="config",
        value=PRICE_BASIS,
    )
