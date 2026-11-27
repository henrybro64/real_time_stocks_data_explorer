from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError, fetch_text

logger = logging.getLogger(__name__)

# Official monthly revenue is served from stored OpenAPI snapshots when present.
# Synthetic revenue/dividend series are no longer emitted (#362 M1).
TW_INSIGHTS_REVENUE_IS_SYNTHETIC = False
TW_INSIGHTS_DIVIDENDS_ARE_SYNTHETIC = False

_REVENUE_UNAVAILABLE_REASON = "official_monthly_revenue_not_collected"
_DIVIDEND_UNAVAILABLE_REASON = "official_dividend_history_not_collected"


def get_tw_stock_insights(stock: StockRecord) -> dict[str, object]:
    """Build Taiwan-only detail insights (official revenue when stored, peer snapshot).

    Revenue comes from persisted official monthly-revenue observations only.
    Missing collection stays explicitly unavailable. Dividend history is not
    collected in Milestone 1 and is never fabricated.
    """
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    revenue = _load_official_revenue(stock)
    peer_payload = _build_peer_insight(stock, now)
    dividends = {
        "status": "unavailable",
        "reason": _DIVIDEND_UNAVAILABLE_REASON,
        "source": None,
        "avgCashYield2Y": None,
        "years": [],
    }
    return {
        "revenue": revenue,
        "peer": {
            "sector": peer_payload["sector"],
            "asOf": peer_payload["asOf"],
            "companyCount": peer_payload["companyCount"],
            "metrics": peer_payload["metrics"],
            "compareUrl": peer_payload["compareUrl"],
            "topMovers": peer_payload["topMovers"],
        },
        "dividends": dividends,
    }


def _load_official_revenue(stock: StockRecord) -> dict[str, object]:
    unavailable = {
        "status": "unavailable",
        "reason": _REVENUE_UNAVAILABLE_REASON,
        "source": None,
        "reportingPeriod": None,
        "reportGeneratedDate": None,
        "retrievedAt": None,
        "lastUpdated": None,
        "latestRevenueK": None,
        "latestMomPercent": None,
        "latestYoyPercent": None,
        "stale": False,
        "points": [],
    }
    try:
        from app.db.session import SessionLocal
        from app.services.tw_monthly_revenue.service import get_monthly_revenue_for_stock
    except Exception:  # noqa: BLE001 — soft degrade when DB wiring unavailable
        return unavailable

    if SessionLocal is None:
        return unavailable

    try:
        with SessionLocal() as session:
            observation = get_monthly_revenue_for_stock(session, stock.id)
    except Exception as exc:  # noqa: BLE001 — keep detail page honest on DB errors
        message = str(exc).lower()
        if "no such table" in message or "does not exist" in message:
            return unavailable
        logger.warning(
            "Failed reading official monthly revenue for insights",
            extra={
                "event": "tw_insights.monthly_revenue_read_failed",
                "stock_id": stock.id,
                "error_type": type(exc).__name__,
            },
        )
        return {
            **unavailable,
            "reason": f"official_monthly_revenue_read_failed:{type(exc).__name__}",
        }

    if observation is None:
        return unavailable

    revenue_k = observation.get("revenueNtThousand")
    latest_revenue_k = int(revenue_k) if isinstance(revenue_k, (int, float)) else None
    reporting_period = observation.get("reportingPeriod")
    points: list[dict[str, object]] = []
    if isinstance(reporting_period, str) and latest_revenue_k is not None:
        # One official month only — never invent a multi-month trend from a single snapshot.
        points.append(
            {
                "month": reporting_period.replace("-", "/"),
                "revenueK": latest_revenue_k,
                "momPercent": observation.get("momPercent"),
                "yoyPercent": observation.get("yoyPercent"),
            }
        )
    return {
        "status": "ok",
        "reason": None,
        "source": observation.get("source"),
        "reportingPeriod": reporting_period,
        "reportGeneratedDate": observation.get("reportGeneratedDate"),
        "retrievedAt": observation.get("lastSeenAt"),
        "lastUpdated": observation.get("lastSeenAt"),
        "latestRevenueK": latest_revenue_k,
        "latestMomPercent": observation.get("momPercent"),
        "latestYoyPercent": observation.get("yoyPercent"),
        "stale": bool(observation.get("stale")),
        "points": points,
    }


def _build_peer_insight(
    stock: StockRecord,
    now: datetime,
) -> dict[str, object]:
    compare_data = _fetch_yahoo_compare(stock)
    if compare_data is None:
        return {
            "sector": "Taiwan industry",
            "asOf": now.date().isoformat(),
            "companyCount": 0,
            "metrics": [],
            "compareUrl": None,
            "topMovers": [],
        }

    summary = compare_data["summary"]
    metrics = compare_data["metrics"] or []
    total = max(
        [metric.get("total", 0) for metric in metrics if isinstance(metric.get("total"), int)]
        or [summary.get("companyCount", 0)],
    )
    company_count = summary.get("companyCount", 0) or total
    return {
        "sector": summary.get("sector") or "Taiwan industry",
        "asOf": summary.get("asOf") or now.date().isoformat(),
        "companyCount": company_count,
        "metrics": metrics,
        "compareUrl": compare_data["url"],
        "topMovers": compare_data["topMovers"],
    }


def _fetch_yahoo_compare(stock: StockRecord) -> dict[str, object] | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    for yahoo_symbol in _yahoo_symbol_candidates(stock):
        url = f"https://tw.stock.yahoo.com/quote/{yahoo_symbol}/compare"
        try:
            html_text = fetch_text(
                url,
                headers=headers,
                timeout_seconds=6,
                retries=1,
            )
        except (HttpFetchError, TimeoutError):
            continue
        parsed = _parse_yahoo_compare(html_text)
        if parsed is None:
            continue
        parsed["url"] = url
        return parsed
    return None


def _yahoo_symbol_candidates(stock: StockRecord) -> tuple[str, ...]:
    if stock.exchange == "TPEx":
        return (f"{stock.symbol}.TWO", f"{stock.symbol}.TW")
    return (f"{stock.symbol}.TW", f"{stock.symbol}.TWO")


def _parse_yahoo_compare(raw_html: str) -> dict[str, object] | None:
    without_scripts = re.sub(
        r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>",
        " ",
        raw_html,
        flags=re.IGNORECASE,
    )
    without_styles = re.sub(
        r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>",
        " ",
        without_scripts,
        flags=re.IGNORECASE,
    )
    text = html.unescape(re.sub(r"<[^>]+>", "\n", without_styles))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    normalized = " ".join(lines)

    summary = _parse_compare_summary(normalized)
    metrics = _parse_compare_metrics(normalized)
    top_movers = _parse_top_movers(lines)

    if not metrics and not top_movers:
        return None
    return {
        "summary": summary,
        "metrics": metrics,
        "topMovers": top_movers,
    }


def _parse_compare_summary(normalized: str) -> dict[str, object]:
    sector = "Taiwan industry"
    company_count = 0

    sector_match = re.search(r"([^ ]+)產業[・.]公司數量(\d+)家", normalized)
    if sector_match:
        sector = sector_match.group(1)
        company_count = int(sector_match.group(2))
    else:
        company_count_match = re.search(r"公司數量(\d+)家", normalized)
        if company_count_match:
            company_count = int(company_count_match.group(1))

    as_of_match = re.search(r"資料時間[:：]\s*(\d{4}/\d{2}/\d{2})", normalized)
    as_of = (
        as_of_match.group(1).replace("/", "-")
        if as_of_match
        else datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    )

    return {
        "sector": sector,
        "companyCount": company_count,
        "asOf": as_of,
    }


def _parse_compare_metrics(normalized: str) -> list[dict[str, object]]:
    company = _extract_percent_series(normalized, "漲跌幅")
    industry = _extract_percent_series(normalized, "同業平均漲跌幅")
    rank_tokens = _extract_rank_series(normalized, "同業漲跌幅排名")
    labels = [("近1月", "1M"), ("近3月", "3M"), ("近6月", "6M"), ("近1年", "1Y")]

    metrics: list[dict[str, object]] = []
    for index, (label, period) in enumerate(labels):
        if (
            index >= len(company)
            or index >= len(industry)
            or index >= len(rank_tokens)
        ):
            break
        rank, total = rank_tokens[index]
        metrics.append(
            {
                "label": label,
                "period": period,
                "companyPercent": company[index],
                "industryAveragePercent": industry[index],
                "rank": rank,
                "total": total,
            }
        )
    return metrics


def _extract_percent_series(text: str, key: str) -> list[float]:
    pattern = re.compile(
        rf"{re.escape(key)}\s+([+\-]?\d+(?:\.\d+)?)%\s+([+\-]?\d+(?:\.\d+)?)%\s+([+\-]?\d+(?:\.\d+)?)%\s+([+\-]?\d+(?:\.\d+)?)%"
    )
    match = pattern.search(text)
    if not match:
        return []
    return [float(group) for group in match.groups()]


def _extract_rank_series(text: str, key: str) -> list[tuple[int, int]]:
    pattern = re.compile(
        rf"{re.escape(key)}\s+(\d+)\s*/\s*(\d+)\s+(\d+)\s*/\s*(\d+)\s+(\d+)\s*/\s*(\d+)\s+(\d+)\s*/\s*(\d+)"
    )
    match = pattern.search(text)
    if not match:
        return []
    values = [int(group) for group in match.groups()]
    return [
        (values[0], values[1]),
        (values[2], values[3]),
        (values[4], values[5]),
        (values[6], values[7]),
    ]


def _parse_top_movers(lines: list[str]) -> list[dict[str, object]]:
    symbol_pattern = re.compile(r"^\d{4}\.(?:TW|TWO)$")
    percent_pattern = re.compile(r"^[+\-]?\d+(?:\.\d+)?%$")
    movers: list[dict[str, object]] = []

    for idx, line in enumerate(lines):
        if not symbol_pattern.match(line):
            continue
        if idx < 2:
            continue
        rank_raw = lines[idx - 2]
        name = lines[idx - 1]
        if not rank_raw.isdigit():
            continue
        values: list[float] = []
        for next_line in lines[idx + 1 : idx + 6]:
            if percent_pattern.match(next_line):
                values.append(float(next_line.rstrip("%")))
            if len(values) == 4:
                break
        if len(values) != 4:
            continue
        movers.append(
            {
                "rank": int(rank_raw),
                "name": name,
                "symbol": line,
                "change1mPercent": values[0],
                "change3mPercent": values[1],
                "change6mPercent": values[2],
                "change1yPercent": values[3],
            }
        )
        if len(movers) >= 10:
            break
    return movers
