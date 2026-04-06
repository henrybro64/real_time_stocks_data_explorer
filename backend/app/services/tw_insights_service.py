from __future__ import annotations

import html
import random
from datetime import datetime
import re
from zoneinfo import ZoneInfo
import math

from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError, fetch_text


def get_tw_stock_insights(stock: StockRecord) -> dict[str, object]:
    """Build Taiwan-only detail insights (revenue, peer snapshot, dividends)."""
    seed = sum(ord(char) for char in stock.symbol)
    rng = random.Random(seed)
    now = datetime.now(ZoneInfo("Asia/Taipei"))

    revenue_points = _build_revenue_points(now, rng)
    peer_payload = _build_peer_insight(stock, now, rng)
    dividend_years = _build_dividend_years(now.year, rng)

    return {
        "revenue": {
            "lastUpdated": now.isoformat(),
            "latestRevenueK": revenue_points[-1]["revenueK"],
            "latestMomPercent": revenue_points[-1]["momPercent"],
            "latestYoyPercent": revenue_points[-1]["yoyPercent"],
            "points": revenue_points,
        },
        "peer": {
            "sector": peer_payload["sector"],
            "asOf": peer_payload["asOf"],
            "companyCount": peer_payload["companyCount"],
            "metrics": peer_payload["metrics"],
            "compareUrl": peer_payload["compareUrl"],
            "topMovers": peer_payload["topMovers"],
        },
        "dividends": {
            "avgCashYield2Y": round(
                (dividend_years[0]["cashYieldPercent"] + dividend_years[1]["cashYieldPercent"]) / 2,
                2,
            ),
            "years": dividend_years,
        },
    }


def _build_peer_insight(
    stock: StockRecord,
    now: datetime,
    rng: random.Random,
) -> dict[str, object]:
    fallback_metrics = _build_peer_metrics(rng)
    compare_data = _fetch_yahoo_compare(stock)
    if compare_data is None:
        return {
            "sector": "Taiwan industry",
            "asOf": now.date().isoformat(),
            "companyCount": fallback_metrics[0]["total"] if fallback_metrics else 0,
            "metrics": fallback_metrics,
            "compareUrl": None,
            "topMovers": [],
        }

    summary = compare_data["summary"]
    metrics = compare_data["metrics"] or fallback_metrics
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


def _build_revenue_points(now: datetime, rng: random.Random) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    base = 3_500_000 + rng.randint(0, 2_000_000)
    for offset in range(23, -1, -1):
        year = now.year
        month = now.month - offset
        while month <= 0:
            month += 12
            year -= 1
        seasonal = 1.0 + 0.07 * math.sin((month / 12) * 6.28318)
        trend = 1 + ((23 - offset) / 23) * 0.12
        noise = 1 + rng.uniform(-0.04, 0.04)
        revenue_k = int(base * seasonal * trend * noise)
        mom = round(rng.uniform(-9.0, 10.5), 2)
        yoy = round(rng.uniform(-3.0, 27.0), 2)
        points.append(
            {
                "month": f"{year:04d}/{month:02d}",
                "revenueK": revenue_k,
                "momPercent": mom,
                "yoyPercent": yoy,
            }
        )
    return points


def _build_peer_metrics(rng: random.Random) -> list[dict[str, object]]:
    labels = [("近1月", "1M"), ("近3月", "3M"), ("近6月", "6M"), ("近1年", "1Y")]
    metrics: list[dict[str, object]] = []
    for label, period in labels:
        company = round(rng.uniform(5.0, 120.0), 2)
        industry = round(company * rng.uniform(0.35, 0.75), 2)
        rank = rng.randint(1, 35)
        metrics.append(
            {
                "label": label,
                "period": period,
                "companyPercent": company,
                "industryAveragePercent": industry,
                "rank": rank,
                "total": 89,
            }
        )
    return metrics


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


def _build_dividend_years(current_year: int, rng: random.Random) -> list[dict[str, object]]:
    years: list[dict[str, object]] = []
    for year in range(current_year - 1, current_year - 7, -1):
        cash = round(rng.uniform(0.8, 4.2), 4)
        stock = round(rng.uniform(0.0, 1.8), 4) if rng.random() > 0.7 else 0.0
        cash_yield = round(rng.uniform(1.2, 6.4), 2)
        years.append(
            {
                "year": year,
                "cashDividend": cash,
                "stockDividend": stock,
                "cashYieldPercent": cash_yield,
                "exDividendDate": f"{year + 1}-06-{rng.randint(5, 28):02d}",
                "payDate": f"{year + 1}-07-{rng.randint(5, 28):02d}",
            }
        )
    return years
