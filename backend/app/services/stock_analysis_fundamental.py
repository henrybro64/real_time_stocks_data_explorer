"""Compact fundamental-outlook facts for AI stock analysis."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Callable, NamedTuple

from app.models.stock import StockRecord

TwInsightsLoader = Callable[[StockRecord], dict[str, object]]


class _TwContext(NamedTuple):
    revenue_trend: dict[str, object] | None
    dividend_summary: dict[str, object] | None
    peer_context: dict[str, object] | None
    positive: list[str]
    missing: list[str]
    negative: list[str]
    sources: list[str]
    failure_reason: str | None


def build_fundamental_outlook(
    stock: StockRecord,
    info: dict[str, object],
    *,
    tw_insights_loader: TwInsightsLoader | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Build a compact ``fundamentalOutlook`` payload from already-loaded info.

    Soft-fails TW insights loading. Never invents missing numbers. Distinguishes
    historical fields from consensus/estimate fields via explicit labels.
    """
    as_of = (now or datetime.now(timezone.utc)).date().isoformat()
    trailing_pe = _coerce_float(info.get("trailingPE"))
    forward_pe = _coerce_float(info.get("forwardPE"))
    trailing_eps = _coerce_float(info.get("trailingEps"))
    forward_eps = _coerce_float(info.get("forwardEps"))
    revenue_growth = _coerce_float(info.get("revenueGrowth"))
    earnings_growth = _coerce_float(info.get("earningsGrowth"))
    earnings_quarterly_growth = _coerce_float(info.get("earningsQuarterlyGrowth"))
    target_mean = _coerce_float(info.get("targetMeanPrice"))
    target_high = _coerce_float(info.get("targetHighPrice"))
    target_low = _coerce_float(info.get("targetLowPrice"))
    target_median = _coerce_float(info.get("targetMedianPrice"))
    recommendation_key = _coerce_str(info.get("recommendationKey"))
    next_earnings = _format_earnings_date(info)

    missing = _missing_core_inputs(
        {
            "trailingPe": trailing_pe,
            "forwardPe": forward_pe,
            "trailingEps": trailing_eps,
            "forwardEps": forward_eps,
            "revenueGrowthEstimate": revenue_growth,
            "earningsGrowthEstimate": earnings_growth,
            "nextEarningsDate": next_earnings,
        }
    )
    positive, negative = _classify_info_factors(
        trailing_pe=trailing_pe,
        forward_pe=forward_pe,
        trailing_eps=trailing_eps,
        revenue_growth=revenue_growth,
        earnings_growth=earnings_growth,
    )

    tw = _build_tw_context(stock, tw_insights_loader)
    positive.extend(tw.positive)
    negative.extend(tw.negative)
    missing.extend(tw.missing)
    revenue_trend = tw.revenue_trend
    sources = ["yfinance.info", *tw.sources]

    present_core = sum(
        1
        for value in (trailing_pe, forward_pe, trailing_eps, forward_eps)
        if value is not None
    )
    availability, confidence = _grade_availability(present_core, revenue_trend is not None)

    payload: dict[str, object] = {
        "available": availability != "insufficient",
        "availability": availability,
        "confidence": confidence,
        "dataAsOf": as_of,
        "forecastHorizon": "next_12_months",
        "sources": sources,
        "trailingPe": trailing_pe,
        "forwardPe": forward_pe,
        "trailingEps": trailing_eps,
        "forwardEps": forward_eps,
        "revenueGrowthEstimate": revenue_growth,
        "earningsGrowthEstimate": earnings_growth,
        "earningsQuarterlyGrowth": earnings_quarterly_growth,
        "nextEarningsDate": next_earnings,
        "analystConsensus": {
            "label": "market_consensus_context_not_intrinsic_value",
            "targetMeanPrice": target_mean,
            "targetHighPrice": target_high,
            "targetLowPrice": target_low,
            "targetMedianPrice": target_median,
            "recommendationKey": recommendation_key,
            "companyGuidance": None,
            "note": (
                "Analyst target prices are market consensus context only; "
                "they are not intrinsic value and not company guidance."
            ),
        },
        "revenueTrend": revenue_trend,
        "dividendSummary": tw.dividend_summary,
        "peerContext": tw.peer_context,
        "positiveFactors": positive,
        "negativeFactors": negative,
        "missingInputs": missing,
    }
    if tw.failure_reason is not None:
        payload["twInsightsUnavailableReason"] = tw.failure_reason
    return payload


def _missing_core_inputs(values: dict[str, object | None]) -> list[str]:
    return [name for name, value in values.items() if value is None]


def _classify_info_factors(
    *,
    trailing_pe: float | None,
    forward_pe: float | None,
    trailing_eps: float | None,
    revenue_growth: float | None,
    earnings_growth: float | None,
) -> tuple[list[str], list[str]]:
    """Split yfinance info fields into named positive and negative signals."""
    positive: list[str] = []
    negative: list[str] = []

    _append_sign_factor(trailing_eps, "trailingEpsPositive", "trailingEpsNegative", positive, negative)
    if _forward_pe_below_trailing(trailing_pe, forward_pe):
        positive.append("forwardPeBelowTrailingPe")
    _append_sign_factor(
        earnings_growth,
        "positiveEarningsGrowthEstimate",
        "negativeEarningsGrowthEstimate",
        positive,
        negative,
    )
    _append_sign_factor(
        revenue_growth,
        "positiveRevenueGrowthEstimate",
        "negativeRevenueGrowthEstimate",
        positive,
        negative,
    )
    return positive, negative


def _append_sign_factor(
    value: float | None,
    positive_name: str,
    negative_name: str,
    positive: list[str],
    negative: list[str],
) -> None:
    if value is None or value == 0:
        return
    if value > 0:
        positive.append(positive_name)
    else:
        negative.append(negative_name)


def _forward_pe_below_trailing(trailing_pe: float | None, forward_pe: float | None) -> bool:
    if trailing_pe is None or forward_pe is None:
        return False
    return 0 < forward_pe < trailing_pe


def _grade_availability(present_core: int, has_revenue_trend: bool) -> tuple[str, str]:
    """Map how many core inputs are present onto availability and confidence."""
    if present_core >= 3:
        return "ok", "moderate"
    if has_revenue_trend and present_core >= 1:
        return "ok", "low"
    if present_core >= 1 or has_revenue_trend:
        return "partial", "low"
    return "insufficient", "low"


def _build_tw_context(
    stock: StockRecord,
    tw_insights_loader: TwInsightsLoader | None,
) -> _TwContext:
    """Taiwan revenue/dividend/peer context, or a labeled reason it is absent."""
    empty = _TwContext(None, None, None, [], [], [], [], None)
    if stock.market != "TW":
        return empty

    if tw_insights_loader is None and not _tw_insights_are_real():
        # The TW insights service still returns seeded placeholder series.
        # Never let that reach the model as reported company results.
        return empty._replace(
            missing=["monthlyRevenueTrend", "dividendTrend", "peerContext"],
            failure_reason="tw_insights_source_is_placeholder_data",
        )

    try:
        loader = tw_insights_loader or _load_tw_insights
        insights = loader(stock)
    except Exception as exc:  # noqa: BLE001 — soft degradation
        return empty._replace(
            missing=["twInsights"],
            failure_reason=f"tw_insights_failed:{type(exc).__name__}",
        )

    revenue_trend = _summarize_tw_revenue(insights)
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []
    if revenue_trend is None:
        missing.append("monthlyRevenueTrend")
    elif revenue_trend.get("yoyDirection") == "improving":
        positive.append("monthlyRevenueYoyImproving")
    elif revenue_trend.get("yoyDirection") == "weakening":
        negative.append("monthlyRevenueYoyWeakening")

    return _TwContext(
        revenue_trend=revenue_trend,
        dividend_summary=_summarize_tw_dividends(insights),
        peer_context=_summarize_tw_peer(insights),
        positive=positive,
        missing=missing,
        negative=negative,
        sources=["tw_insights"],
        failure_reason=None,
    )


def _tw_insights_are_real() -> bool:
    from app.services.tw_insights_service import (  # pylint: disable=import-outside-toplevel
        TW_INSIGHTS_DIVIDENDS_ARE_SYNTHETIC,
        TW_INSIGHTS_REVENUE_IS_SYNTHETIC,
    )

    return not (TW_INSIGHTS_REVENUE_IS_SYNTHETIC or TW_INSIGHTS_DIVIDENDS_ARE_SYNTHETIC)


def _load_tw_insights(stock: StockRecord) -> dict[str, object]:
    from app.services.tw_insights_service import (  # pylint: disable=import-outside-toplevel
        get_tw_stock_insights,
    )

    return get_tw_stock_insights(stock)


def classify_monthly_revenue_direction(values: list[float]) -> str:
    """Classify recent MoM or YoY direction from newest-last percent changes."""
    usable = [value for value in values if value is not None]
    if len(usable) < 2:
        return "insufficient"
    recent = usable[-3:] if len(usable) >= 3 else usable
    ups = sum(1 for value in recent if value > 0)
    downs = sum(1 for value in recent if value < 0)
    if ups >= 2 and ups > downs:
        return "improving"
    if downs >= 2 and downs > ups:
        return "weakening"
    return "mixed"


def _summarize_tw_revenue(insights: dict[str, object]) -> dict[str, object] | None:
    revenue = insights.get("revenue")
    if not isinstance(revenue, dict):
        return None
    points = revenue.get("points")
    if not isinstance(points, list) or not points:
        return None
    mom_values: list[float] = []
    yoy_values: list[float] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        mom = _coerce_float(point.get("momPercent"))
        yoy = _coerce_float(point.get("yoyPercent"))
        if mom is not None:
            mom_values.append(mom)
        if yoy is not None:
            yoy_values.append(yoy)
    if not mom_values and not yoy_values:
        return None
    latest_month = None
    last = points[-1]
    if isinstance(last, dict):
        latest_month = _coerce_str(last.get("month"))
    return {
        "kind": "historical_monthly_revenue",
        "latestMonth": latest_month,
        "latestRevenueK": _coerce_int(revenue.get("latestRevenueK")),
        "latestMomPercent": _coerce_float(revenue.get("latestMomPercent")),
        "latestYoyPercent": _coerce_float(revenue.get("latestYoyPercent")),
        "momDirection": classify_monthly_revenue_direction(mom_values),
        "yoyDirection": classify_monthly_revenue_direction(yoy_values),
        "note": (
            "MoM and YoY describe recent reported monthly revenue changes; "
            "they are historical results, not a long-term forecast."
        ),
    }


def _summarize_tw_dividends(insights: dict[str, object]) -> dict[str, object] | None:
    dividends = insights.get("dividends")
    if not isinstance(dividends, dict):
        return None
    years = dividends.get("years")
    return {
        "kind": "historical_dividends",
        "avgCashYield2Y": _coerce_float(dividends.get("avgCashYield2Y")),
        "yearCount": len(years) if isinstance(years, list) else 0,
    }


def _summarize_tw_peer(insights: dict[str, object]) -> dict[str, object] | None:
    peer = insights.get("peer")
    if not isinstance(peer, dict):
        return None
    return {
        "kind": "peer_relative_context",
        "sector": _coerce_str(peer.get("sector")),
        "asOf": _coerce_str(peer.get("asOf")),
        "companyCount": _coerce_int(peer.get("companyCount")),
    }


def _format_earnings_date(info: dict[str, object]) -> str | None:
    for key in ("earningsTimestamp", "earningsTimestampStart", "earningsDate"):
        formatted = _earnings_date_from_value(info.get(key))
        if formatted is not None:
            return formatted
    return None


def _earnings_date_from_value(value: object) -> str | None:
    """Accept an epoch number, a list of them, or an already-formatted string."""
    if isinstance(value, list):
        return _earnings_date_from_value(value[0]) if value else None
    epoch_date = _epoch_to_iso_date(value)
    if epoch_date is not None:
        return epoch_date
    return _coerce_str(value)


def _epoch_to_iso_date(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 4)


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
