from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo
import math

from app.models.stock import StockRecord


def get_tw_stock_insights(stock: StockRecord) -> dict[str, object]:
    """Build Taiwan-only detail insights (revenue, peer snapshot, dividends)."""
    seed = sum(ord(char) for char in stock.symbol)
    rng = random.Random(seed)
    now = datetime.now(ZoneInfo("Asia/Taipei"))

    revenue_points = _build_revenue_points(now, rng)
    peer_metrics = _build_peer_metrics(rng)
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
            "sector": "半導體產業",
            "asOf": now.date().isoformat(),
            "companyCount": 89,
            "metrics": peer_metrics,
        },
        "dividends": {
            "avgCashYield2Y": round(
                (dividend_years[0]["cashYieldPercent"] + dividend_years[1]["cashYieldPercent"]) / 2,
                2,
            ),
            "years": dividend_years,
        },
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
