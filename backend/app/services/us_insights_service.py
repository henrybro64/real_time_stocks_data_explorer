from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError, fetch_json

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_YAHOO_RECOMMENDATIONS_URL = (
    "https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/"
)
_YAHOO_QUOTE_SUMMARY_URL = (
    "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
)
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def get_us_stock_insights(stock: StockRecord) -> dict[str, object]:
    now = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    peer_symbols = _fetch_peer_symbols(stock.symbol)
    if stock.symbol not in peer_symbols:
        peer_symbols.insert(0, stock.symbol)
    peer_symbols = peer_symbols[:8]

    sector, industry = _fetch_sector_industry(stock.symbol)
    rows = _build_peer_rows(peer_symbols)
    company_row = next(
        (row for row in rows if row["symbol"] == stock.symbol),
        None,
    )
    company_total = len(rows)

    return {
        "peer": {
            "asOf": now,
            "sector": sector,
            "industry": industry,
            "compareUrl": f"https://finance.yahoo.com/quote/{stock.symbol}",
            "companySymbol": stock.symbol,
            "companyRankBy1m": _rank_of_symbol(rows, stock.symbol),
            "companyCount": company_total,
            "companyReturns": (
                {
                    "change1mPercent": company_row["change1mPercent"],
                    "change3mPercent": company_row["change3mPercent"],
                    "change6mPercent": company_row["change6mPercent"],
                    "change1yPercent": company_row["change1yPercent"],
                }
                if company_row is not None
                else None
            ),
            "topMovers": rows,
        }
    }


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }


def _fetch_peer_symbols(symbol: str) -> list[str]:
    url = f"{_YAHOO_RECOMMENDATIONS_URL}{symbol}"
    try:
        payload = fetch_json(
            url,
            headers=_headers(),
            timeout_seconds=6,
            retries=1,
        )
    except (HttpFetchError, TimeoutError, ValueError):
        return [symbol]
    finance = payload.get("finance") if isinstance(payload, dict) else None
    result = finance.get("result") if isinstance(finance, dict) else None
    if not isinstance(result, list) or not result:
        return [symbol]
    first = result[0]
    recs = first.get("recommendedSymbols") if isinstance(first, dict) else None
    if not isinstance(recs, list):
        return [symbol]
    out: list[str] = [symbol]
    for item in recs:
        if not isinstance(item, dict):
            continue
        s = item.get("symbol")
        if isinstance(s, str) and s and s not in out:
            out.append(s)
    return out


def _fetch_sector_industry(symbol: str) -> tuple[str, str]:
    url = f"{_YAHOO_QUOTE_SUMMARY_URL}{symbol}"
    try:
        payload = fetch_json(
            url,
            params={"modules": "assetProfile"},
            headers=_headers(),
            timeout_seconds=6,
            retries=1,
        )
    except (HttpFetchError, TimeoutError, ValueError):
        return ("U.S. equities", "N/A")
    quote_summary = (
        payload.get("quoteSummary")
        if isinstance(payload, dict)
        else None
    )
    result = (
        quote_summary.get("result")
        if isinstance(quote_summary, dict)
        else None
    )
    if not isinstance(result, list) or not result:
        return ("U.S. equities", "N/A")
    first = result[0]
    profile = first.get("assetProfile") if isinstance(first, dict) else None
    if not isinstance(profile, dict):
        return ("U.S. equities", "N/A")
    sector = profile.get("sector")
    industry = profile.get("industry")
    return (
        sector if isinstance(sector, str) and sector else "U.S. equities",
        industry if isinstance(industry, str) and industry else "N/A",
    )


def _build_peer_rows(symbols: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        returns = _fetch_returns(symbol)
        if returns is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "change1mPercent": returns[0],
                "change3mPercent": returns[1],
                "change6mPercent": returns[2],
                "change1yPercent": returns[3],
            }
        )
    rows.sort(key=lambda row: row["change1mPercent"], reverse=True)
    for index, row in enumerate(rows):
        row["rank"] = index + 1
    return rows


def _fetch_returns(symbol: str) -> tuple[float, float, float, float] | None:
    url = f"{_YAHOO_CHART_URL}{symbol}"
    try:
        payload = fetch_json(
            url,
            params={"interval": "1d", "range": "1y"},
            headers=_headers(),
            timeout_seconds=6,
            retries=1,
        )
    except (HttpFetchError, TimeoutError, ValueError):
        return None
    chart = payload.get("chart") if isinstance(payload, dict) else None
    result = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(result, list) or not result:
        return None
    first = result[0]
    indicators = first.get("indicators") if isinstance(first, dict) else None
    quote = indicators.get("quote") if isinstance(indicators, dict) else None
    if not isinstance(quote, list) or not quote:
        return None
    q0 = quote[0]
    closes = q0.get("close") if isinstance(q0, dict) else None
    if not isinstance(closes, list):
        return None
    numeric = [
        float(value)
        for value in closes
        if isinstance(value, (int, float))
    ]
    if len(numeric) < 30:
        return None
    last = numeric[-1]
    return (
        _pct(last, numeric[-22]),
        _pct(last, numeric[-66] if len(numeric) >= 66 else numeric[0]),
        _pct(last, numeric[-126] if len(numeric) >= 126 else numeric[0]),
        _pct(last, numeric[0]),
    )


def _pct(last: float, first: float) -> float:
    if first == 0:
        return 0.0
    return round(((last - first) / first) * 100, 2)


def _rank_of_symbol(rows: list[dict[str, object]], symbol: str) -> int | None:
    for row in rows:
        if row.get("symbol") == symbol:
            rank = row.get("rank")
            return int(rank) if isinstance(rank, int) else None
    return None
