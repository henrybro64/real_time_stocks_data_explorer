"""Shared chip-evidence read model for research UI (#346) and analyst handoff (#347).

Formats frozen first-pass provenance fields:
- ``institutional_5d``
- ``margin`` / ``margin_utilization``
- ``tdcc_holders_ge_400_lots``

Does **not** fetch upstream providers. Never treats missing as zero. Never implies
a buy signal.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.market_clock import is_trading_day

_TAIPEI = ZoneInfo("Asia/Taipei")

CHIP_FIELD_KEYS = (
    "institutional_5d",
    "margin",
    "margin_utilization",
    "tdcc_holders_ge_400_lots",
)

# Keys that gate analyst readiness (#347). margin_utilization is unavailable by design.
GATING_CHIP_FIELD_KEYS = (
    "institutional_5d",
    "margin",
    "tdcc_holders_ge_400_lots",
)

# Raw provenance key → format_chip_evidence section key.
_GATING_FORMATTED_SECTION = {
    "institutional_5d": "institutional",
    "margin": "margin",
    "tdcc_holders_ge_400_lots": "tdcc",
}

# Approximate Asia/Taipei publication-ready cutoffs (institutional T86 ≈ 15:00–16:00,
# margin ≈ 21:00–21:30). Override via TW_CHIP_INSTITUTIONAL_READY / TW_CHIP_MARGIN_READY
# (HH:MM) or settings.tw_chip_*_ready.
CHIP_PUBLICATION_READY_TIME: dict[str, dt_time] = {
    "institutional": dt_time(16, 30),
    "margin": dt_time(21, 30),
}

_READY_ENV_KEYS = {
    "institutional": "TW_CHIP_INSTITUTIONAL_READY",
    "margin": "TW_CHIP_MARGIN_READY",
}


def format_chip_evidence(
    record: dict[str, Any] | None,
    *,
    reference_day: str | date | None = None,
    calendar_known: bool | None = None,
    capture_at: datetime | None = None,
    date_only: bool | None = None,
) -> dict[str, Any]:
    """Build a readable chip summary from one first-pass record.

    Prefer frozen first-pass ``fields`` only. Enrichment is not merged here —
    callers that need Phase-2 enrichment must pass it separately.

    Freshness for institutional/margin is relative to ``capture_at`` (snapshot
    created_at) and each field's publication-ready time — not the capture date
    alone. When ``capture_at`` is omitted, ``reference_day`` @ 23:59 Taipei is
    used (``staleBasis=date_only``). When neither is available, institutional/
    margin get ``staleCheck=capture_time_unavailable`` (never treated as fresh).

    ``calendar_known``:
    - ``None`` / ``True`` — compute expected session from the calendar helpers.
    - ``False`` — do not claim institutional/margin stale; set
      ``staleCheck=calendar_unavailable`` / ``staleBasis=unverified``.
    """
    record = record or {}
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}

    ref = _as_date(reference_day)
    if capture_at is not None:
        if capture_at.tzinfo is None:
            capture_at = capture_at.replace(tzinfo=_TAIPEI)
        resolved_capture = capture_at.astimezone(_TAIPEI)
        resolved_date_only = bool(date_only) if date_only is not None else False
    else:
        resolved_capture, resolved_date_only = _resolve_capture_at(None, ref)
        if date_only is True:
            resolved_date_only = True
    institutional = _format_institutional(
        fields.get("institutional_5d"),
        present="institutional_5d" in fields,
        reference_day=ref,
        calendar_known=calendar_known,
        capture_at=resolved_capture,
        date_only=resolved_date_only,
    )
    margin = _format_margin(
        fields.get("margin") if "margin" in fields else None,
        fields.get("margin_utilization") if "margin_utilization" in fields else None,
        present="margin" in fields,
        reference_day=ref,
        calendar_known=calendar_known,
        capture_at=resolved_capture,
        date_only=resolved_date_only,
    )
    tdcc = _format_tdcc(
        fields.get("tdcc_holders_ge_400_lots"),
        present="tdcc_holders_ge_400_lots" in fields,
        reference_day=ref,
        calendar_known=calendar_known,
        capture_at=resolved_capture,
    )

    return {
        "stockId": record.get("stockId") or record.get("stock_id"),
        "stockName": record.get("stockName") or record.get("stock_name"),
        "nameStatus": record.get("nameStatus") or record.get("name_status"),
        "institutional": institutional,
        "margin": margin,
        "tdcc": tdcc,
        "requiredFields": list(CHIP_FIELD_KEYS),
        "chipFieldsPresent": [
            key for key in CHIP_FIELD_KEYS if isinstance(fields.get(key), dict)
        ],
        "notBuySignal": True,
        "note": (
            "Chip trends are factual evidence for analyst review; "
            "they are not an automatic buy/sell or Notion inclusion rule."
        ),
    }


def chip_fields_from_record(record: dict[str, Any] | None) -> dict[str, Any]:
    """Return raw provenance for the three chip families (plus utilization)."""
    record = record or {}
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    return {key: fields.get(key) for key in CHIP_FIELD_KEYS}


def summarize_chip_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate coverage counts for handoff verification (#347)."""
    total = len(records)
    counts = {
        key: {
            "ok": 0,
            "partial": 0,
            "unavailable": 0,
            "stale": 0,
            "not_fetched": 0,
            "missing": 0,  # alias of not_fetched for compatibility
        }
        for key in CHIP_FIELD_KEYS
    }
    for record in records:
        fields = chip_fields_from_record(record)
        for key in CHIP_FIELD_KEYS:
            payload = fields.get(key)
            if not isinstance(payload, dict):
                counts[key]["not_fetched"] += 1
                counts[key]["missing"] += 1
                continue
            status = str(payload.get("status") or "unavailable")
            if status == "stale" or bool(payload.get("stale")):
                counts[key]["stale"] += 1
                continue
            if status == "not_fetched":
                counts[key]["not_fetched"] += 1
                counts[key]["missing"] += 1
                continue
            if status not in counts[key]:
                status = "unavailable"
            counts[key][status] += 1
    return {
        "recordCount": total,
        "fieldCoverage": counts,
        "requiredFields": list(CHIP_FIELD_KEYS),
        "gatingFields": list(GATING_CHIP_FIELD_KEYS),
        "note": (
            "missing/not_fetched means the provenance key was absent from the "
            "frozen record. margin_utilization unavailable-by-design does not gate."
        ),
    }


def reference_day_from_snapshot(
    *,
    trading_day: str | date | None = None,
    payload: dict[str, Any] | None = None,
    created_at: Any = None,
) -> str | None:
    """Normalize a chip reference day to Asia/Taipei ``YYYY-MM-DD``.

    Preference: explicit ``trading_day``, then payload tradingDay/trading_day,
    then ``created_at`` converted to Asia/Taipei.
    """
    if isinstance(trading_day, date) and not isinstance(trading_day, datetime):
        return trading_day.isoformat()
    if trading_day is not None and str(trading_day).strip():
        return str(trading_day).strip()[:10]

    payload = payload or {}
    for key in ("tradingDay", "trading_day"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()[:10]

    return _created_at_to_taipei_date(created_at)


def capture_at_from_snapshot(*, created_at: Any = None) -> datetime | None:
    """Parse snapshot ``created_at`` as timezone-aware Asia/Taipei datetime."""
    return _parse_created_at(created_at)


def resolve_chip_capture_at(
    *,
    created_at: Any = None,
    request_trading_day: str | date | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[datetime | None, bool]:
    """Resolve capture timestamp for chip freshness.

    Preference: ``created_at`` → request tradingDay @ 23:59 Taipei → payload
    tradingDay @ 23:59 Taipei. Trading-day fallbacks reuse ``_resolve_capture_at``
    and return ``date_only=True``. When nothing is available, returns
    ``(None, False)`` so callers can fail closed with
    ``staleCheck=capture_time_unavailable``.
    """
    parsed = _parse_created_at(created_at)
    if parsed is not None:
        return _resolve_capture_at(parsed, None)

    payload = payload or {}
    payload_day = payload.get("tradingDay")
    if payload_day is None or str(payload_day).strip() == "":
        payload_day = payload.get("trading_day")

    for day_src in (request_trading_day, payload_day):
        day = _as_date(day_src)
        if day is not None:
            return _resolve_capture_at(None, day)
    return None, False


def publication_ready_time(kind: str) -> dt_time:
    """Return Asia/Taipei publication-ready clock for institutional/margin."""
    env_key = _READY_ENV_KEYS.get(kind)
    raw = ""
    if env_key:
        raw = str(os.environ.get(env_key, "") or "").strip()
    if not raw:
        try:
            from app.core.config import settings

            attr = f"tw_chip_{kind}_ready"
            raw = str(getattr(settings, attr, "") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    parsed = _parse_hhmm(raw) if raw else None
    if parsed is not None:
        return parsed
    return CHIP_PUBLICATION_READY_TIME[kind]


def expected_session(kind: str, capture_at: datetime) -> date | None:
    """Latest TW trading session S with combine(S, ready_time[kind], Taipei) <= capture_at.

    No network: uses ``is_trading_day(..., allow_holiday_fetch=False)``. A capture
    before a session's ready time therefore expects the previous session.
    """
    if kind not in CHIP_PUBLICATION_READY_TIME:
        return None
    local = capture_at.astimezone(_TAIPEI) if capture_at.tzinfo else capture_at.replace(
        tzinfo=_TAIPEI
    )
    ready = publication_ready_time(kind)
    day = local.date()
    for _ in range(21):
        probe = datetime.combine(day, dt_time.min, tzinfo=_TAIPEI)
        if is_trading_day("TW", probe, allow_holiday_fetch=False):
            ready_at = datetime.combine(day, ready, tzinfo=_TAIPEI)
            if ready_at <= local:
                return day
        day -= timedelta(days=1)
    return None


def _created_at_to_taipei_date(created_at: Any) -> str | None:
    parsed = _parse_created_at(created_at)
    if parsed is None:
        if created_at is None or created_at == "":
            return None
        text = str(created_at).strip()
        return text[:10] if text else None
    return parsed.date().isoformat()


def _parse_created_at(created_at: Any) -> datetime | None:
    if created_at is None or created_at == "":
        return None
    if isinstance(created_at, datetime):
        parsed = created_at
    else:
        try:
            parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_TAIPEI)
    return parsed.astimezone(_TAIPEI)


def _parse_hhmm(raw: str) -> dt_time | None:
    text = str(raw).strip()
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return dt_time(hour, minute)


def _resolve_capture_at(
    capture_at: datetime | None,
    reference_day: date | None,
) -> tuple[datetime | None, bool]:
    """Return (capture_at Taipei, date_only). date_only when falling back to 23:59."""
    if capture_at is not None:
        if capture_at.tzinfo is None:
            capture_at = capture_at.replace(tzinfo=_TAIPEI)
        return capture_at.astimezone(_TAIPEI), False
    if reference_day is not None:
        return datetime.combine(reference_day, dt_time(23, 59), tzinfo=_TAIPEI), True
    return None, False


def apply_chip_coverage_warnings(
    records: list[dict[str, Any]],
    quality_warnings: list[str],
    *,
    candidate_stock_ids: list[str] | None = None,
    full_two_pass: bool = False,
    reference_day: str | date | None = None,
    calendar_known: bool | None = None,
    capture_at: datetime | None = None,
    date_only: bool | None = None,
) -> dict[str, Any]:
    """Append chip-content readiness warnings (#347). Returns coverage + chipGaps.

    Classification uses the same ``format_chip_evidence(..., reference_day=)``
    staleness path as ``symbols[].chipEvidence`` (not raw provenance alone).
    Explicit raw ``stale: True`` remains honored via that formatter.

    Blocking:
    - ``first_pass_chip_fields_missing`` when any record lacks a gating key
      (not_fetched) or a gating field is unavailable/partial with empty/None reason.
    - ``candidate_chip_fields_stale_or_missing`` (full_two_pass only) when any
      candidate-set stock has a gating field that is stale or not_fetched.
    - ``chip_staleness_unverifiable_calendar_unavailable`` (full_two_pass only)
      when a candidate gating field has ``staleCheck=calendar_unavailable``.
    - ``chip_staleness_unverifiable_capture_time_unavailable`` (full_two_pass only)
      when a candidate gating field has ``staleCheck=capture_time_unavailable``.

    Reasoned unavailable/partial and stale outside the candidate set are
    non-blocking and reported in ``chipGaps`` (capped items, sorted by stockId).
    Non-candidate ``calendar_unavailable`` / ``capture_time_unavailable`` →
    chipGaps status ``stale_check_unavailable``. ``margin_utilization`` remains
    non-gating.
    """
    coverage = summarize_chip_coverage(records)
    candidate_ids = {
        str(stock_id).upper()
        for stock_id in (candidate_stock_ids or [])
        if stock_id
    }
    gap_counts: dict[str, dict[str, int]] = {
        key: {} for key in GATING_CHIP_FIELD_KEYS
    }
    gap_items: list[dict[str, Any]] = []
    missing_block = False
    candidate_block = False
    calendar_block = False
    capture_time_block = False
    unverifiable_checks = {
        "calendar_unavailable": "calendar",
        "capture_time_unavailable": "capture_time",
    }

    for record in records:
        stock_id = str(record.get("stockId") or record.get("stock_id") or "")
        stock_key = stock_id.upper()
        summary = format_chip_evidence(
            record,
            reference_day=reference_day,
            calendar_known=calendar_known,
            capture_at=capture_at,
            date_only=date_only,
        )
        raw_fields = chip_fields_from_record(record)
        for key in GATING_CHIP_FIELD_KEYS:
            section = summary.get(_GATING_FORMATTED_SECTION[key])
            if not isinstance(section, dict):
                status = "not_fetched"
                reason = None
                stale_check = None
            else:
                status = str(section.get("status") or "unavailable")
                reason = section.get("reason")
                stale_check = section.get("staleCheck")
                # Backward compatible: honor explicit raw stale flag.
                raw = raw_fields.get(key)
                if (
                    isinstance(raw, dict)
                    and bool(raw.get("stale"))
                    and status not in {"not_fetched", "stale"}
                ):
                    status = "stale"
                if reason is not None and str(reason).strip() == "":
                    reason = None

            if stale_check in unverifiable_checks:
                if full_two_pass and stock_key in candidate_ids:
                    if unverifiable_checks[stale_check] == "calendar":
                        calendar_block = True
                    else:
                        capture_time_block = True
                else:
                    gap_status = "stale_check_unavailable"
                    bucket = gap_counts.setdefault(key, {})
                    bucket[gap_status] = int(bucket.get(gap_status) or 0) + 1
                    gap_items.append(
                        {
                            "stockId": stock_id,
                            "field": key,
                            "status": gap_status,
                            "reason": reason,
                        }
                    )
                continue

            if status == "ok":
                continue

            if status == "not_fetched":
                missing_block = True
                if full_two_pass and stock_key in candidate_ids:
                    candidate_block = True
            elif status in {"unavailable", "partial"} and reason is None:
                missing_block = True
            elif full_two_pass and stock_key in candidate_ids and status == "stale":
                candidate_block = True

            # Non-blocking reasoned gaps + non-candidate stale → chipGaps.
            blocking_here = (
                status == "not_fetched"
                or (status in {"unavailable", "partial"} and reason is None)
                or (
                    full_two_pass
                    and stock_key in candidate_ids
                    and status in {"stale", "not_fetched"}
                )
            )
            if not blocking_here:
                bucket = gap_counts.setdefault(key, {})
                bucket[status] = int(bucket.get(status) or 0) + 1
                gap_items.append(
                    {
                        "stockId": stock_id,
                        "field": key,
                        "status": status,
                        "reason": reason,
                    }
                )

    gap_items.sort(key=lambda item: (str(item.get("stockId") or ""), str(item.get("field"))))
    truncated = len(gap_items) > 200
    coverage["chipGaps"] = {
        "counts": gap_counts,
        "items": gap_items[:200],
        "truncated": truncated,
    }

    if missing_block and "first_pass_chip_fields_missing" not in quality_warnings:
        quality_warnings.append("first_pass_chip_fields_missing")
    if (
        candidate_block
        and "candidate_chip_fields_stale_or_missing" not in quality_warnings
    ):
        quality_warnings.append("candidate_chip_fields_stale_or_missing")
    if (
        calendar_block
        and "chip_staleness_unverifiable_calendar_unavailable" not in quality_warnings
    ):
        quality_warnings.append("chip_staleness_unverifiable_calendar_unavailable")
    if (
        capture_time_block
        and "chip_staleness_unverifiable_capture_time_unavailable"
        not in quality_warnings
    ):
        quality_warnings.append("chip_staleness_unverifiable_capture_time_unavailable")
    return coverage


def _prov(field: Any, *, present: bool) -> dict[str, Any]:
    if not present or not isinstance(field, dict):
        return {
            "status": "not_fetched",
            "reason": "field_absent",
            "source": None,
            "sourceAsOf": None,
            "capturedAt": None,
            "freshnessClass": None,
            "value": None,
            "stale": False,
        }
    return {
        "status": field.get("status") or "unavailable",
        "reason": field.get("reason"),
        "source": field.get("source"),
        "sourceAsOf": field.get("source_as_of") or field.get("sourceAsOf"),
        "capturedAt": field.get("captured_at") or field.get("capturedAt"),
        "freshnessClass": field.get("freshness_class") or field.get("freshnessClass"),
        "value": field.get("value"),
        "stale": bool(field.get("stale")),
    }


def _apply_stale(
    base: dict[str, Any],
    *,
    reference_day: date | None,
    kind: str,
    calendar_known: bool | None = None,
    capture_at: datetime | None = None,
    date_only: bool = False,
) -> dict[str, Any]:
    """Mark institutional/margin/TDCC stale relative to capture time / reference day."""
    if base.get("status") == "not_fetched":
        return base
    source_as_of = _as_date(base.get("sourceAsOf"))
    tdcc_ref = reference_day or (capture_at.astimezone(_TAIPEI).date() if capture_at else None)
    if kind == "tdcc":
        if tdcc_ref is None or source_as_of is None:
            if base.get("stale"):
                base["status"] = "stale"
            return base
        if source_as_of < (tdcc_ref - timedelta(days=10)) or base.get("stale"):
            base["stale"] = True
            base["status"] = "stale"
            base["reason"] = base.get("reason") or "tdcc_source_as_of_older_than_10_calendar_days"
        base["staleCheck"] = "verified"
        return base

    if kind not in {"institutional", "margin"}:
        return base

    if calendar_known is False:
        # Fail closed honestly — do not invent weekday trading sessions.
        base["staleCheck"] = "calendar_unavailable"
        base["staleBasis"] = "unverified"
        return base

    if capture_at is None:
        # Fail closed: without a capture timestamp (or trading-day fallback)
        # do not treat institutional/margin as fresh.
        if base.get("stale"):
            base["status"] = "stale"
        base["staleCheck"] = "capture_time_unavailable"
        base["staleBasis"] = "unverified"
        return base

    if source_as_of is None:
        if base.get("stale"):
            base["status"] = "stale"
        return base

    expected_day = expected_session(kind, capture_at)
    if expected_day is None:
        if date_only:
            base["staleBasis"] = "date_only"
        return base
    if source_as_of < expected_day:
        base["stale"] = True
        base["status"] = "stale"
        base["reason"] = base.get("reason") or "source_as_of_before_expected_session"
    if calendar_known is True:
        base["staleCheck"] = "verified"
    if date_only:
        base["staleBasis"] = "date_only"
    return base


def _format_institutional(
    field: Any,
    *,
    present: bool,
    reference_day: date | None,
    calendar_known: bool | None = None,
    capture_at: datetime | None = None,
    date_only: bool = False,
) -> dict[str, Any]:
    base = _apply_stale(
        _prov(field, present=present),
        reference_day=reference_day,
        kind="institutional",
        calendar_known=calendar_known,
        capture_at=capture_at,
        date_only=date_only,
    )
    points = base.get("value")
    sessions: list[dict[str, Any]] = []
    if isinstance(points, list):
        for row in points:
            if not isinstance(row, dict):
                continue
            sessions.append(
                {
                    "date": row.get("date"),
                    "foreignNetLots": _num_or_none(row.get("foreignNetLots")),
                    "trustNetLots": _num_or_none(row.get("trustNetLots")),
                    "dealerNetLots": _num_or_none(row.get("dealerNetLots")),
                    "totalNetLots": _num_or_none(row.get("totalNetLots")),
                }
            )
    totals = None
    totals_status = "unavailable"
    totals_reason = "fewer_than_5_completed_sessions"
    if base.get("status") == "not_fetched":
        totals_status = "not_fetched"
        totals_reason = "field_absent"
    elif len(sessions) >= 5:
        # Use the last five available completed sessions only.
        window = sessions[-5:]
        if all(
            all(
                value is not None
                for value in (
                    row["foreignNetLots"],
                    row["trustNetLots"],
                    row["dealerNetLots"],
                    row["totalNetLots"],
                )
            )
            for row in window
        ):
            totals = {
                "foreignNetLots": sum(float(row["foreignNetLots"]) for row in window),
                "trustNetLots": sum(float(row["trustNetLots"]) for row in window),
                "dealerNetLots": sum(float(row["dealerNetLots"]) for row in window),
                "totalNetLots": sum(float(row["totalNetLots"]) for row in window),
                "sessionCount": 5,
                "dates": [row["date"] for row in window],
            }
            totals_status = "ok"
            totals_reason = None
        else:
            totals_status = "partial"
            totals_reason = "null_lots_in_session_window"
            window = sessions[-5:]
            totals = {
                "foreignNetLots": _sum_present(row["foreignNetLots"] for row in window),
                "trustNetLots": _sum_present(row["trustNetLots"] for row in window),
                "dealerNetLots": _sum_present(row["dealerNetLots"] for row in window),
                "totalNetLots": _sum_present(row["totalNetLots"] for row in window),
                "sessionCount": len(window),
                "dates": [row["date"] for row in window],
                "note": "Partial totals omit null sessions; not invented zeros.",
            }
    elif sessions:
        totals_status = "partial"
        totals_reason = f"only_{len(sessions)}_sessions_available"

    return {
        **base,
        "unit": "lots",
        "sessions": sessions,
        "fiveSessionTotals": totals,
        "fiveSessionTotalsStatus": totals_status,
        "fiveSessionTotalsReason": totals_reason,
        "labelZh": "三大法人",
    }


def _format_margin(
    margin_field: Any,
    utilization_field: Any,
    *,
    present: bool,
    reference_day: date | None,
    calendar_known: bool | None = None,
    capture_at: datetime | None = None,
    date_only: bool = False,
) -> dict[str, Any]:
    base = _apply_stale(
        _prov(margin_field, present=present),
        reference_day=reference_day,
        kind="margin",
        calendar_known=calendar_known,
        capture_at=capture_at,
        date_only=date_only,
    )
    value = base.get("value") if isinstance(base.get("value"), dict) else {}
    balance = _num_or_none(value.get("balanceLots"))
    delta = _num_or_none(value.get("balanceDeltaLotsApprox5Sessions"))
    if delta is None:
        direction = "unavailable"
    elif delta > 0:
        direction = "increase"
    elif delta < 0:
        direction = "decrease"
    else:
        direction = "flat"

    utilization = _prov(utilization_field, present=isinstance(utilization_field, dict))
    short_balance = _first_num(
        value.get("shortBalanceLots"),
        value.get("borrowedShortBalanceLots"),
        value.get("marginShortBalanceLots"),
    )

    return {
        **base,
        "unit": "lots",
        "balanceLots": balance,
        "balanceDeltaLotsApprox5Sessions": delta,
        "direction": direction,
        "historyDates": list(value.get("historyDates") or []),
        "shortBalanceLots": short_balance,
        "shortBalanceStatus": "ok" if short_balance is not None else "unavailable",
        "shortBalanceReason": None
        if short_balance is not None
        else "verified_short_balance_not_in_first_pass_payload",
        "utilization": {
            "status": utilization.get("status"),
            "reason": utilization.get("reason")
            or "numerator_and_denominator_not_both_verified",
            "value": None,
        },
        "labelZh": "融資",
        "note": "Balance change is not the same as institutional net buying.",
    }


def _format_tdcc(
    field: Any,
    *,
    present: bool,
    reference_day: date | None,
    calendar_known: bool | None = None,
    capture_at: datetime | None = None,
) -> dict[str, Any]:
    del calendar_known  # TDCC staleness is calendar-day based; arg kept for API symmetry.
    base = _apply_stale(
        _prov(field, present=present),
        reference_day=reference_day,
        kind="tdcc",
        capture_at=capture_at,
    )
    value = base.get("value") if isinstance(base.get("value"), dict) else {}
    percent = _num_or_none(value.get("percent"))
    change_pp = _num_or_none(value.get("weeklyChangePercentPoints"))
    prior_week = value.get("priorWeekDate")
    # Prior-week missing → partial with null pp change (#346).
    status = base.get("status")
    nested_stale = bool(value.get("stale"))
    # Ownership stale-cache fallbacks store the flag under value.stale (#346/#347).
    if nested_stale and status not in {"not_fetched", "stale"}:
        status = "stale"
        base["stale"] = True
        base["reason"] = base.get("reason") or "tdcc_value_marked_stale"
        base["staleCheck"] = base.get("staleCheck") or "verified"
    if present and isinstance(field, dict) and status not in {"not_fetched", "stale"}:
        if prior_week in {None, ""} and change_pp is None and percent is not None:
            status = "partial"
            base["reason"] = base.get("reason") or "prior_week_missing"
    return {
        **base,
        "status": status,
        "unit": "percent",
        "changeUnit": "percentage_points",
        "holdersGe400LotsPercent": percent,
        "weeklyChangePercentagePoints": change_pp,
        "priorWeekDate": prior_week,
        "stale": bool(nested_stale or base.get("stale")),
        "labelZh": "大戶動向",
        "limitation": (
            "TDCC ≥400-lot concentration is a weekly holding snapshot, "
            "not a real-time purchase/sale feed."
        ),
        "note": value.get("note")
        or "Weekly change is in percentage points, not percent growth.",
    }


def _num_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_num(*values: Any) -> float | None:
    for value in values:
        parsed = _num_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _sum_present(values) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums)


def _as_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
