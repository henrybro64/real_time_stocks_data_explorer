"""Shared chip-evidence read model for research UI (#346) and analyst handoff (#347).

Formats frozen first-pass provenance fields:
- ``institutional_5d``
- ``margin`` / ``margin_utilization``
- ``tdcc_holders_ge_400_lots``

Does **not** fetch upstream providers. Never treats missing as zero. Never implies
a buy signal.
"""

from __future__ import annotations

from typing import Any

CHIP_FIELD_KEYS = (
    "institutional_5d",
    "margin",
    "margin_utilization",
    "tdcc_holders_ge_400_lots",
)


def format_chip_evidence(record: dict[str, Any] | None) -> dict[str, Any]:
    """Build a readable chip summary from one first-pass (or enrichment) record."""
    record = record or {}
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    # Enrichment payloads nest under enrichment.fields in technical ledger rows.
    enrichment = record.get("enrichment")
    if isinstance(enrichment, dict) and isinstance(enrichment.get("fields"), dict):
        # Prefer first-pass fields when both exist; fall back to enrichment.
        merged = dict(enrichment["fields"])
        merged.update(fields)
        fields = merged

    institutional = _format_institutional(fields.get("institutional_5d"))
    margin = _format_margin(fields.get("margin"), fields.get("margin_utilization"))
    tdcc = _format_tdcc(fields.get("tdcc_holders_ge_400_lots"))

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
    enrichment = record.get("enrichment")
    if isinstance(enrichment, dict) and isinstance(enrichment.get("fields"), dict):
        merged = dict(enrichment["fields"])
        merged.update(fields)
        fields = merged
    return {key: fields.get(key) for key in CHIP_FIELD_KEYS}


def summarize_chip_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate coverage counts for handoff verification (#347)."""
    total = len(records)
    counts = {key: {"ok": 0, "partial": 0, "unavailable": 0, "missing": 0} for key in CHIP_FIELD_KEYS}
    for record in records:
        fields = chip_fields_from_record(record)
        for key in CHIP_FIELD_KEYS:
            payload = fields.get(key)
            if not isinstance(payload, dict):
                counts[key]["missing"] += 1
                continue
            status = str(payload.get("status") or "unavailable")
            if status not in counts[key]:
                status = "unavailable"
            counts[key][status] += 1
    return {
        "recordCount": total,
        "fieldCoverage": counts,
        "requiredFields": list(CHIP_FIELD_KEYS),
        "note": "missing means the provenance key was absent from the frozen record.",
    }


def _prov(field: Any) -> dict[str, Any]:
    if not isinstance(field, dict):
        return {
            "status": "unavailable",
            "reason": "field_absent",
            "source": None,
            "sourceAsOf": None,
            "capturedAt": None,
            "freshnessClass": None,
            "value": None,
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


def _format_institutional(field: Any) -> dict[str, Any]:
    base = _prov(field)
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
    if len(sessions) >= 5:
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


def _format_margin(margin_field: Any, utilization_field: Any) -> dict[str, Any]:
    base = _prov(margin_field)
    value = base.get("value") if isinstance(base.get("value"), dict) else {}
    balance = _num_or_none(value.get("balanceLots"))
    delta = _num_or_none(value.get("balanceDeltaLotsApprox5Sessions"))
    direction = "unavailable"
    if delta is None:
        direction = "unavailable"
    elif delta > 0:
        direction = "increase"
    elif delta < 0:
        direction = "decrease"
    else:
        direction = "flat"

    utilization = _prov(utilization_field)
    short_balance = _num_or_none(
        value.get("shortBalanceLots")
        or value.get("borrowedShortBalanceLots")
        or value.get("marginShortBalanceLots")
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


def _format_tdcc(field: Any) -> dict[str, Any]:
    base = _prov(field)
    value = base.get("value") if isinstance(base.get("value"), dict) else {}
    percent = _num_or_none(value.get("percent"))
    change_pp = _num_or_none(value.get("weeklyChangePercentPoints"))
    return {
        **base,
        "unit": "percent",
        "changeUnit": "percentage_points",
        "holdersGe400LotsPercent": percent,
        "weeklyChangePercentagePoints": change_pp,
        "priorWeekDate": value.get("priorWeekDate"),
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


def _sum_present(values) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums)
