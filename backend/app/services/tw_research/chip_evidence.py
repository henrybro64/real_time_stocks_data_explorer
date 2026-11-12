"""Shared chip-evidence read model for research UI (#346) and analyst handoff (#347).

Formats frozen first-pass provenance fields:
- ``institutional_5d``
- ``margin`` / ``margin_utilization``
- ``tdcc_holders_ge_400_lots``

Does **not** fetch upstream providers. Never treats missing as zero. Never implies
a buy signal.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.services.market_clock import last_completed_session_date

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


def format_chip_evidence(
    record: dict[str, Any] | None,
    *,
    reference_day: str | date | None = None,
) -> dict[str, Any]:
    """Build a readable chip summary from one first-pass record.

    Prefer frozen first-pass ``fields`` only. Enrichment is not merged here —
    callers that need Phase-2 enrichment must pass it separately.
    """
    record = record or {}
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}

    ref = _as_date(reference_day)
    institutional = _format_institutional(
        fields.get("institutional_5d"),
        present="institutional_5d" in fields,
        reference_day=ref,
    )
    margin = _format_margin(
        fields.get("margin") if "margin" in fields else None,
        fields.get("margin_utilization") if "margin_utilization" in fields else None,
        present="margin" in fields,
        reference_day=ref,
    )
    tdcc = _format_tdcc(
        fields.get("tdcc_holders_ge_400_lots"),
        present="tdcc_holders_ge_400_lots" in fields,
        reference_day=ref,
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


def apply_chip_coverage_warnings(
    records: list[dict[str, Any]],
    quality_warnings: list[str],
    *,
    candidate_stock_ids: list[str] | None = None,
    full_two_pass: bool = False,
) -> dict[str, Any]:
    """Append chip-content readiness warnings (#347). Returns coverage + chipGaps.

    Blocking:
    - ``first_pass_chip_fields_missing`` when any record lacks a gating key
      (not_fetched) or a gating field is unavailable/partial with empty/None reason.
    - ``candidate_chip_fields_stale_or_missing`` (full_two_pass only) when any
      candidate-set stock has a gating field that is stale or not_fetched.

    Reasoned unavailable/partial and stale outside the candidate set are
    non-blocking and reported in ``chipGaps`` (capped items, sorted by stockId).
    ``margin_utilization`` remains non-gating.
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

    for record in records:
        stock_id = str(record.get("stockId") or record.get("stock_id") or "")
        stock_key = stock_id.upper()
        fields = chip_fields_from_record(record)
        for key in GATING_CHIP_FIELD_KEYS:
            payload = fields.get(key)
            if not isinstance(payload, dict):
                status = "not_fetched"
                reason = None
            else:
                status = str(payload.get("status") or "unavailable")
                if bool(payload.get("stale")) and status not in {"not_fetched"}:
                    status = "stale"
                reason = payload.get("reason")
                if reason is not None and str(reason).strip() == "":
                    reason = None

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
) -> dict[str, Any]:
    """Mark institutional/margin/TDCC stale relative to the snapshot reference day."""
    if base.get("status") == "not_fetched":
        return base
    source_as_of = _as_date(base.get("sourceAsOf"))
    if reference_day is None or source_as_of is None:
        if base.get("stale"):
            base["status"] = "stale"
        return base

    if kind in {"institutional", "margin"}:
        expected = last_completed_session_date(
            "TW",
            datetime.combine(reference_day, datetime.min.time()),
            allow_holiday_fetch=False,
        )
        # Fall back to reference_day itself when calendar lookup is empty.
        expected_day = expected or reference_day
        if source_as_of < expected_day:
            base["stale"] = True
            base["status"] = "stale"
            base["reason"] = base.get("reason") or "source_as_of_before_last_completed_session"
    elif kind == "tdcc":
        if source_as_of < (reference_day - timedelta(days=10)) or base.get("stale"):
            base["stale"] = True
            base["status"] = "stale"
            base["reason"] = base.get("reason") or "tdcc_source_as_of_older_than_10_calendar_days"
    return base


def _format_institutional(
    field: Any,
    *,
    present: bool,
    reference_day: date | None,
) -> dict[str, Any]:
    base = _apply_stale(_prov(field, present=present), reference_day=reference_day, kind="institutional")
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
) -> dict[str, Any]:
    base = _apply_stale(_prov(margin_field, present=present), reference_day=reference_day, kind="margin")
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
) -> dict[str, Any]:
    base = _apply_stale(_prov(field, present=present), reference_day=reference_day, kind="tdcc")
    value = base.get("value") if isinstance(base.get("value"), dict) else {}
    percent = _num_or_none(value.get("percent"))
    change_pp = _num_or_none(value.get("weeklyChangePercentPoints"))
    prior_week = value.get("priorWeekDate")
    # Prior-week missing → partial with null pp change (#346).
    status = base.get("status")
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
        "stale": bool(value.get("stale") or base.get("stale")),
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
