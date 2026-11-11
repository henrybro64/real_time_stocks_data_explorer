"""Bounded first-pass research evidence for the full configured universe (#335/#336).

Collects identity + cheap market/chip/valuation evidence for **every** selected
symbol without requiring technical day+month qualification and without invoking
second-pass MACD/KD. Missing fields stay explicitly unavailable — never
fabricated. Yahoo profitability is opt-in only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.services.daily_price_history_service import daily_price_history_service
from app.services.moving_average import completed_daily_candles
from app.services.quote_service import quote_service
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
    FIRST_PASS_VERSION,
    PREFERRED_DAILY_HISTORY_LIMIT,
    PRICE_BASIS,
)
from app.services.tw_research.enrichment import (
    ProvenanceField,
    _enrich_institutional,
    _enrich_margin,
    _enrich_ownership,
    _enrich_valuation,
    EnrichmentResult,
)
from app.services.tw_research.price_basis import (
    RelativePriceBasisEvidence,
    evaluate_relative_price_basis,
)
from app.services.tw_research.universe import resolve_universe, UniverseSelector

StockLoader = Callable[[str], StockRecord | None]
HistoryCandlesLoader = Callable[[StockRecord], list[CandleRecord]]
InstitutionalLoader = Callable[[str, int], TaiwanInstitutionalInvestorsResult]
MarginLoader = Callable[[str, int], TaiwanMarginShortResult]
OwnershipLoader = Callable[[str], TaiwanOwnershipConcentrationResult]
KeyStatsLoader = Callable[[str], dict[str, object] | None]
ProfitabilityLoader = Callable[[str], dict[str, object] | None]
ThemeKeysLoader = Callable[[str], list[str]]


@dataclass
class FirstPassSymbolRecord:
    stock_id: str
    stock_name: str | None
    name_status: Literal["ok", "unavailable"]
    name_source: str
    exchange: str | None
    market: str | None
    sector_theme_keys: list[str]
    first_pass_version: str = FIRST_PASS_VERSION
    fields: dict[str, ProvenanceField] = field(default_factory=dict)
    relative_price_basis: dict[str, Any] | None = None
    completeness: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stockId": self.stock_id,
            "stockName": self.stock_name,
            "nameStatus": self.name_status,
            "nameSource": self.name_source,
            "exchange": self.exchange,
            "market": self.market,
            "sectorThemeKeys": list(self.sector_theme_keys),
            "firstPassVersion": self.first_pass_version,
            "error": self.error,
            "fields": {key: asdict(value) for key, value in self.fields.items()},
            "relativePriceBasis": self.relative_price_basis,
            "completeness": self.completeness,
        }


@dataclass
class FirstPassBatchResult:
    first_pass_version: str
    universe_version: str
    requested_count: int
    collected_count: int
    deduped_count: int
    records: list[FirstPassSymbolRecord]
    coverage_summary: dict[str, Any]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "firstPassVersion": self.first_pass_version,
            "universeVersion": self.universe_version,
            "requestedCount": self.requested_count,
            "collectedCount": self.collected_count,
            "dedupedCount": self.deduped_count,
            "records": [record.as_dict() for record in self.records],
            "coverageSummary": self.coverage_summary,
            "notes": list(self.notes),
        }


def collect_first_pass_batch(
    stock_ids: Sequence[str],
    *,
    universe_version: str | None = None,
    stock_loader: StockLoader | None = None,
    candles_loader: HistoryCandlesLoader | None = None,
    institutional_loader: InstitutionalLoader | None = None,
    margin_loader: MarginLoader | None = None,
    ownership_loader: OwnershipLoader | None = None,
    key_stats_loader: KeyStatsLoader | None = None,
    profitability_loader: ProfitabilityLoader | None = None,
    theme_keys_loader: ThemeKeysLoader | None = None,
    include_nonofficial_margins: bool = False,
    now: datetime | None = None,
) -> FirstPassBatchResult:
    """Collect first-pass evidence for a **deduplicated** ID list.

    Dedup happens before any loader call so multi-sector membership cannot
    trigger duplicate upstream work for the same symbol in one batch.
    """
    deduped = dedupe_stock_ids(stock_ids)
    loader = stock_loader or quote_service.get_catalog_stock
    themes = theme_keys_loader or (lambda sid: list(_theme_keys_for(sid)))
    version = universe_version or f"first-pass:{len(deduped)}"
    captured_at = datetime.now(UTC).isoformat()
    calculation_now = now
    records: list[FirstPassSymbolRecord] = []

    for stock_id in deduped:
        records.append(
            _collect_one(
                stock_id,
                stock_loader=loader,
                candles_loader=candles_loader or _default_stored_candles_loader,
                institutional_loader=institutional_loader or _default_institutional,
                margin_loader=margin_loader or _default_margin,
                ownership_loader=ownership_loader or _default_ownership,
                key_stats_loader=key_stats_loader or _default_key_stats,
                profitability_loader=profitability_loader,
                theme_keys_loader=themes,
                include_nonofficial_margins=include_nonofficial_margins,
                captured_at=captured_at,
                now=calculation_now,
            )
        )

    coverage = _coverage_summary(records)
    return FirstPassBatchResult(
        first_pass_version=FIRST_PASS_VERSION,
        universe_version=version,
        requested_count=len(stock_ids),
        collected_count=len(records),
        deduped_count=len(deduped),
        records=records,
        coverage_summary=coverage,
        notes=[
            "First-pass is candidate-independent evidence for the external analyst.",
            "Technical MACD/KD second-pass is intentionally not invoked here.",
            "Missing PEG/revenue/OCF/debt never become fabricated values.",
            "include_nonofficial_margins defaults false to avoid unbounded Yahoo fan-out.",
            "Stock Ticker does not decide Notion report inclusion from this pack.",
        ],
    )


def collect_first_pass_for_universe(
    selector: UniverseSelector,
    *,
    stock_ids_override: Sequence[str] | None = None,
    **kwargs: Any,
) -> FirstPassBatchResult:
    """Resolve universe (dedupe) then collect first-pass evidence."""
    if stock_ids_override is not None:
        resolved_ids = _dedupe_stock_ids(stock_ids_override)
        version = f"explicit:{len(resolved_ids)}"
    else:
        resolved = resolve_universe(selector)
        resolved_ids = list(resolved.stock_ids)
        version = resolved.universe_version
    return collect_first_pass_batch(
        resolved_ids,
        universe_version=version,
        **kwargs,
    )


def _collect_one(
    stock_id: str,
    *,
    stock_loader: StockLoader,
    candles_loader: HistoryCandlesLoader,
    institutional_loader: InstitutionalLoader,
    margin_loader: MarginLoader,
    ownership_loader: OwnershipLoader,
    key_stats_loader: KeyStatsLoader,
    profitability_loader: ProfitabilityLoader | None,
    theme_keys_loader: ThemeKeysLoader,
    include_nonofficial_margins: bool,
    captured_at: str,
    now: datetime | None,
) -> FirstPassSymbolRecord:
    stock = stock_loader(stock_id)
    if stock is None:
        record = FirstPassSymbolRecord(
            stock_id=stock_id,
            stock_name=None,
            name_status="unavailable",
            name_source="catalog",
            exchange=None,
            market=None,
            sector_theme_keys=[],
            error="not_in_catalog",
        )
        record.completeness = _symbol_completeness(record)
        return record

    name, name_status = _display_name(stock)
    record = FirstPassSymbolRecord(
        stock_id=stock.id,
        stock_name=name,
        name_status=name_status,
        name_source="catalog",
        exchange=stock.exchange,
        market=stock.market,
        sector_theme_keys=list(theme_keys_loader(stock.id)),
    )

    # Reuse enrichment step helpers via a temporary EnrichmentResult.
    scratch = EnrichmentResult(stock_id=stock.id, qualified=True)
    steps: list[tuple[str, str, str, Callable[[], None]]] = [
        (
            "institutional_5d",
            "tw_institutional_service",
            "quote_or_eod_day",
            lambda: _enrich_institutional(
                scratch,
                stock.id,
                loader=institutional_loader,
                captured_at=captured_at,
                symbol=stock.symbol,
            ),
        ),
        (
            "margin",
            "tw_margin_short_service",
            "quote_or_eod_day",
            lambda: _enrich_margin(
                scratch,
                stock.id,
                loader=margin_loader,
                captured_at=captured_at,
            ),
        ),
        (
            "tdcc_holders_ge_400_lots",
            "tw_ownership_concentration_service",
            "tdcc_weekly",
            lambda: _enrich_ownership(
                scratch,
                stock.id,
                loader=ownership_loader,
                captured_at=captured_at,
            ),
        ),
        (
            "official_pe",
            "key_stats_service",
            "quote_or_eod_day",
            lambda: _enrich_valuation(
                scratch,
                stock.id,
                loader=key_stats_loader,
                captured_at=captured_at,
            ),
        ),
    ]
    for field_name, source, freshness_class, action in steps:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 — isolate per source
            scratch.fields[field_name] = ProvenanceField(
                field=field_name,
                status="unavailable",
                source=source,
                captured_at=captured_at,
                freshness_class=freshness_class,
                reason=f"symbol_error:{type(exc).__name__}",
            )

    # Explicit not-implemented / unavailable fundamentals (no fabrication).
    # official_pb / official_dividend_yield may already be set by _enrich_valuation
    # from TWSE BWIBBU / TPEx official daily valuation fields.
    for field_name, source, freshness_class, reason in (
        (
            "monthly_revenue_yoy_mom",
            "none",
            "monthly_closed_month",
            "real_monthly_revenue_adapter_missing;tw_insights_synthetic_forbidden",
        ),
        (
            "operating_cash_flow",
            "none",
            "earnings_quarterly",
            "cash_flow_adapter_not_implemented",
        ),
        (
            "debt_metrics",
            "none",
            "earnings_quarterly",
            "balance_sheet_adapter_not_implemented",
        ),
        (
            "historical_pe_pb_range",
            "none",
            "computed",
            "same_definition_historical_valuation_source_not_verified",
        ),
    ):
        if field_name not in scratch.fields:
            scratch.fields[field_name] = ProvenanceField(
                field=field_name,
                status="unavailable",
                source=source,
                captured_at=captured_at,
                freshness_class=freshness_class,
                reason=reason,
            )
    if "official_pb" not in scratch.fields:
        scratch.fields["official_pb"] = ProvenanceField(
            field="official_pb",
            status="unavailable",
            source="key_stats_service",
            captured_at=captured_at,
            freshness_class="quote_or_eod_day",
            reason="official_pb_not_present_in_valuation_snapshot",
        )
    if "official_dividend_yield" not in scratch.fields:
        scratch.fields["official_dividend_yield"] = ProvenanceField(
            field="official_dividend_yield",
            status="unavailable",
            source="key_stats_service",
            captured_at=captured_at,
            freshness_class="quote_or_eod_day",
            reason="official_yield_not_present_in_valuation_snapshot",
        )

    if "peg" not in scratch.fields:
        scratch.fields["peg"] = ProvenanceField(
            field="peg",
            status="unavailable",
            source="none",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="verified_eps_growth_denominator_unavailable",
        )

    _apply_margins(
        scratch,
        stock_id=stock.id,
        include_nonofficial_margins=include_nonofficial_margins,
        profitability_loader=profitability_loader,
        captured_at=captured_at,
    )

    candles = list(candles_loader(stock) or [])
    completed = completed_daily_candles(candles, "TW", now=now)
    if completed:
        as_of = datetime.fromisoformat(completed[-1].time).date().isoformat()
        scratch.fields["latest_completed_close"] = ProvenanceField(
            field="latest_completed_close",
            status="ok",
            source="daily_price_history_service",
            source_as_of=as_of,
            captured_at=captured_at,
            freshness_class="quote_or_eod_day",
            value={
                "close": float(completed[-1].close),
                "priceBasis": PRICE_BASIS,
                "sessionCount": len(completed),
            },
        )
    else:
        scratch.fields["latest_completed_close"] = ProvenanceField(
            field="latest_completed_close",
            status="unavailable",
            source="daily_price_history_service",
            captured_at=captured_at,
            freshness_class="quote_or_eod_day",
            reason="stored_history_empty_or_incomplete",
        )

    pe_value = None
    pe_field = scratch.fields.get("official_pe")
    if pe_field is not None and isinstance(pe_field.value, dict):
        raw_pe = pe_field.value.get("peRatio")
        if isinstance(raw_pe, (int, float)):
            pe_value = float(raw_pe)

    basis: RelativePriceBasisEvidence = evaluate_relative_price_basis(
        candles,
        market="TW",
        now=now,
        official_pe=pe_value,
    )
    record.relative_price_basis = basis.as_dict()
    scratch.fields["relative_price_basis"] = ProvenanceField(
        field="relative_price_basis",
        status="ok" if basis.category != "insufficient_data" else "unavailable",
        source="tw_research.price_basis",
        source_as_of=basis.as_of,
        captured_at=captured_at,
        freshness_class="computed",
        reason=None if basis.category != "insufficient_data" else basis.rationale,
        value={
            "category": basis.category,
            "week52Position": basis.week52_position,
            "drawdownFromWeek52High": basis.drawdown_from_week52_high,
            "potentialValueTrapFlag": basis.potential_value_trap_flag,
            "rationale": basis.rationale,
        },
    )

    # Cheap trend summary only — not second-pass MACD/KD.
    scratch.fields["second_pass_macd_kd"] = ProvenanceField(
        field="second_pass_macd_kd",
        status="unavailable",
        source="deferred",
        captured_at=captured_at,
        freshness_class="computed",
        reason="deferred_to_separate_technical_pass;not_invoked_in_first_pass",
    )

    record.fields = dict(scratch.fields)
    record.completeness = _symbol_completeness(record)
    return record


def _apply_margins(
    scratch: EnrichmentResult,
    *,
    stock_id: str,
    include_nonofficial_margins: bool,
    profitability_loader: ProfitabilityLoader | None,
    captured_at: str,
) -> None:
    if not include_nonofficial_margins:
        scratch.fields["gross_operating_net_margins"] = ProvenanceField(
            field="gross_operating_net_margins",
            status="unavailable",
            source="tw_profitability_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="not_fetched;set_include_nonofficial_margins_true_to_opt_in_yahoo",
        )
        return

    loader = profitability_loader or _default_profitability
    try:
        payload = loader(stock_id)
    except Exception as exc:  # noqa: BLE001
        scratch.fields["gross_operating_net_margins"] = ProvenanceField(
            field="gross_operating_net_margins",
            status="unavailable",
            source="tw_profitability_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason=f"symbol_error:{type(exc).__name__}",
        )
        return

    if not payload:
        scratch.fields["gross_operating_net_margins"] = ProvenanceField(
            field="gross_operating_net_margins",
            status="unavailable",
            source="tw_profitability_service",
            captured_at=captured_at,
            freshness_class="earnings_quarterly",
            reason="profitability_payload_empty",
        )
        return

    scratch.fields["gross_operating_net_margins"] = ProvenanceField(
        field="gross_operating_net_margins",
        status="partial",
        source=str(payload.get("source") or "yahoo_finance_quarterly_income"),
        source_as_of=str(payload.get("asOf")) if payload.get("asOf") else None,
        captured_at=captured_at,
        freshness_class="earnings_quarterly",
        reason="nonofficial_yahoo_quarterly;mops_xbrl_not_wired",
        value=payload,
    )


def _theme_keys_for(stock_id: str) -> tuple[str, ...]:
    try:
        from app.services.stock_theme_service import DEFAULT_THEME_ASSIGNMENTS

        for assignment in DEFAULT_THEME_ASSIGNMENTS:
            if assignment.stock_id == stock_id:
                return tuple(assignment.theme_keys)
    except Exception:  # noqa: BLE001 — themes are optional metadata
        return ()
    return ()


def _display_name(stock: StockRecord) -> tuple[str | None, Literal["ok", "unavailable"]]:
    name = (stock.name_zh_tw or stock.name or "").strip()
    if name:
        return name, "ok"
    return None, "unavailable"


def _default_stored_candles_loader(stock: StockRecord) -> list[CandleRecord]:
    candles, _missing = daily_price_history_service.peek_daily_history(
        stock,
        "MAX",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
    )
    return list(candles)


def _default_institutional(stock_id: str, days: int) -> TaiwanInstitutionalInvestorsResult:
    return tw_institutional_service.get_institutional_investors(stock_id, days=days)


def _default_margin(stock_id: str, days: int) -> TaiwanMarginShortResult:
    return tw_margin_short_service.get_margin_short(stock_id, days=days)


def _default_ownership(stock_id: str) -> TaiwanOwnershipConcentrationResult:
    return tw_ownership_concentration_service.get_ownership_concentration(stock_id, range="3M")


def _default_key_stats(stock_id: str) -> dict[str, object] | None:
    from app.services.key_stats_service import get_key_statistics

    return get_key_statistics(stock_id)


def _default_profitability(stock_id: str) -> dict[str, object] | None:
    from app.services.tw_profitability_service import tw_profitability_service

    result = tw_profitability_service.get_profitability(stock_id)
    if result is None:
        return None
    latest = result.points[-1] if result.points else None
    return {
        "source": result.source,
        "asOf": result.as_of,
        "stale": result.stale,
        "latest": None
        if latest is None
        else {
            "fiscalQuarter": latest.fiscal_quarter,
            "periodEnd": latest.period_end,
            "grossMarginPercent": latest.gross_margin_percent,
            "operatingMarginPercent": latest.operating_margin_percent,
            "netMarginPercent": latest.net_margin_percent,
        },
    }


def dedupe_stock_ids(stock_ids: Sequence[str]) -> list[str]:
    """Canonical TW stock-id dedupe (order-preserving). Used before batchLimit."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in stock_ids:
        key = str(raw).strip().upper()
        if not key:
            continue
        if not key.startswith("TW:") and key.isdigit():
            key = f"TW:{key}"
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# Back-compat alias for internal callers / older tests.
_dedupe_stock_ids = dedupe_stock_ids


def _symbol_completeness(record: FirstPassSymbolRecord) -> dict[str, Any]:
    attempted = len(record.fields)
    ok = sum(1 for f in record.fields.values() if f.status == "ok")
    partial = sum(1 for f in record.fields.values() if f.status == "partial")
    unavailable = sum(1 for f in record.fields.values() if f.status == "unavailable")
    fundamental_keys = {
        "official_pe",
        "ttm_eps",
        "peg",
        "official_pb",
        "official_dividend_yield",
        "monthly_revenue_yoy_mom",
        "gross_operating_net_margins",
        "operating_cash_flow",
        "debt_metrics",
        "historical_pe_pb_range",
    }
    fund_fields = [record.fields[k] for k in fundamental_keys if k in record.fields]
    fund_ok = sum(1 for f in fund_fields if f.status == "ok")
    return {
        "identityComplete": record.name_status == "ok" and record.error is None,
        "fieldsAttempted": attempted,
        "fieldsOk": ok,
        "fieldsPartial": partial,
        "fieldsUnavailable": unavailable,
        "fundamentalFieldsAttempted": len(fund_fields),
        "fundamentalFieldsOk": fund_ok,
        "fundamentalComplete": False,  # never claim complete while core adapters missing
        "technicalSecondPassInvoked": False,
        "note": (
            "Attempted counts are denominators; ok counts are numerators. "
            "A finished first-pass run does not imply fundamental completeness."
        ),
    }


def _coverage_summary(records: list[FirstPassSymbolRecord]) -> dict[str, Any]:
    total = len(records)
    with_name = sum(1 for r in records if r.name_status == "ok")
    catalog_ok = sum(1 for r in records if r.error is None)
    fund_ok_any = 0
    for record in records:
        pe = record.fields.get("official_pe")
        if pe is not None and pe.status == "ok":
            fund_ok_any += 1
    return {
        "symbolsRequestedAfterDedupe": total,
        "symbolsWithCatalogIdentity": catalog_ok,
        "symbolsWithVerifiedName": with_name,
        "symbolsWithOfficialPe": fund_ok_any,
        "validFundamentalRecords": fund_ok_any,
        "denominators": {
            "universeSymbols": total,
            "fundamentalFieldsCore": "see per-record completeness.fundamentalFieldsAttempted",
        },
        "note": (
            f"{fund_ok_any}/{total} symbols have verified official PE; "
            "this is not full fundamental completeness."
        ),
    }
