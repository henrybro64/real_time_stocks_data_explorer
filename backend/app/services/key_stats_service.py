"""Aggregate key statistics for stock detail: quote + Yahoo chart (US) or candles (TW)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.models.stock import StockRecord
from app.services.quote_service import quote_service

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TIMEOUT = 15
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
_FINNHUB_API = "https://finnhub.io/api/v1"


def _yahoo_chart(symbol: str, interval: str, range_name: str) -> dict | None:
    enc = quote(symbol, safe="-.")
    url = f"{_YAHOO_CHART}/{enc}?interval={interval}&range={range_name}"
    request = Request(url)
    request.add_header("User-Agent", _BROWSER_USER_AGENT)
    request.add_header("Accept", "application/json,text/plain,*/*")
    try:
        with urlopen(request, timeout=_TIMEOUT) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def _avg_last_n_int(values: list[object | None], n: int) -> int | None:
    nums = [int(v) for v in values if isinstance(v, (int, float)) and v is not None]
    if not nums:
        return None
    tail = nums[-n:] if len(nums) >= n else nums
    return int(sum(tail) / len(tail))


def _date_from_ts(ts: int, tz_name: str) -> str:
    dt = datetime.fromtimestamp(ts, tz=ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d")


def _extreme_low_high_dates(
    timestamps: list[object],
    lows: list[float | None],
    highs: list[float | None],
    tz_name: str,
) -> tuple[str | None, str | None]:
    low_date: str | None = None
    high_date: str | None = None
    n = min(len(timestamps), len(lows), len(highs))
    if n == 0:
        return low_date, high_date
    best_i_low: int | None = None
    best_lv = None
    for i in range(n):
        t_raw, l = timestamps[i], lows[i]
        if l is None or not isinstance(t_raw, (int, float)):
            continue
        if best_lv is None or l < best_lv:
            best_lv = l
            best_i_low = i
    if best_i_low is not None:
        ts = timestamps[best_i_low]
        if isinstance(ts, (int, float)):
            low_date = _date_from_ts(int(ts), tz_name)
    best_i_high: int | None = None
    best_hv = None
    for i in range(n):
        t_raw, h = timestamps[i], highs[i]
        if h is None or not isinstance(t_raw, (int, float)):
            continue
        if best_hv is None or h > best_hv:
            best_hv = h
            best_i_high = i
    if best_i_high is not None:
        ts = timestamps[best_i_high]
        if isinstance(ts, (int, float)):
            high_date = _date_from_ts(int(ts), tz_name)
    return low_date, high_date


def _us_from_yahoo(stock: StockRecord, base: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = dict(base)
    sym = stock.symbol
    tz = stock.timezone

    day = _yahoo_chart(sym, "5m", "1d")
    y1 = _yahoo_chart(sym, "1d", "1y")

    if day and day.get("chart", {}).get("result"):
        meta = day["chart"]["result"][0].get("meta") or {}
        dh = meta.get("regularMarketDayHigh")
        dl = meta.get("regularMarketDayLow")
        if isinstance(dh, (int, float)):
            out["dayHigh"] = float(dh)
        if isinstance(dl, (int, float)):
            out["dayLow"] = float(dl)

    if y1 and y1.get("chart", {}).get("result"):
        block = y1["chart"]["result"][0]
        meta = block.get("meta") or {}
        fhi = meta.get("fiftyTwoWeekHigh")
        flo = meta.get("fiftyTwoWeekLow")
        if isinstance(fhi, (int, float)):
            out["fiftyTwoWeekHigh"] = float(fhi)
        if isinstance(flo, (int, float)):
            out["fiftyTwoWeekLow"] = float(flo)

        ts_list = block.get("timestamp") or []
        q = (block.get("indicators") or {}).get("quote", [{}])[0]
        vols = q.get("volume") or []
        lows = q.get("low") or []
        highs = q.get("high") or []
        if isinstance(ts_list, list) and vols:
            out["avgVolume10d"] = _avg_last_n_int(vols, 10)

        if isinstance(ts_list, list) and lows and highs:
            low_date, high_date = _extreme_low_high_dates(list(ts_list), list(lows), list(highs), tz)
            out["fiftyTwoWeekLowDate"] = low_date
            out["fiftyTwoWeekHighDate"] = high_date

    return out


def _tw_from_candles(stock: StockRecord, base: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = dict(base)
    data = quote_service.get_history(stock.id, "1Y")
    if data is None:
        return out
    _, candles = data
    if not candles:
        return out

    lows = [c.low for c in candles]
    highs = [c.high for c in candles]
    vols = [c.volume for c in candles]
    i_low = min(range(len(lows)), key=lambda k: lows[k])
    i_high = max(range(len(highs)), key=lambda k: highs[k])
    out["fiftyTwoWeekLow"] = lows[i_low]
    out["fiftyTwoWeekHigh"] = highs[i_high]
    out["fiftyTwoWeekLowDate"] = _iso_date_only(candles[i_low].time)
    out["fiftyTwoWeekHighDate"] = _iso_date_only(candles[i_high].time)
    tail = vols[-10:] if len(vols) >= 10 else vols
    if tail:
        out["avgVolume10d"] = int(sum(tail) / len(tail))
    return out


def _iso_date_only(iso: str) -> str | None:
    if not iso:
        return None
    return iso[:10] if len(iso) >= 10 else None


def _http_get_json_url(url: str) -> dict | None:
    request = Request(url)
    request.add_header("User-Agent", _BROWSER_USER_AGENT)
    request.add_header("Accept", "application/json,text/plain,*/*")
    try:
        with urlopen(request, timeout=_TIMEOUT) as response:
            data = json.load(response)
            return data if isinstance(data, dict) else None
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def _http_get_any(url: str) -> object | None:
    request = Request(url)
    request.add_header("User-Agent", _BROWSER_USER_AGENT)
    request.add_header("Accept", "application/json,text/plain,*/*")
    try:
        with urlopen(request, timeout=_TIMEOUT) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def _finnhub_symbol(stock: StockRecord) -> str | None:
    if stock.market == "US":
        return stock.symbol.upper()
    if stock.market == "TW":
        return f"{stock.symbol}.TW"
    return None


def _format_market_cap_usd(value: float) -> str:
    if value >= 1e12:
        return f"{value / 1e12:.1f}T"
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{value / 1e6:.1f}M"
    return f"{value:.0f}"


def _format_shares_outstanding(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{value / 1e6:.1f}M"
    return f"{value:,.0f}"


def _finnhub_enrich(out: dict[str, object], stock: StockRecord) -> dict[str, object]:
    """Optional enrichment via Finnhub (free tier API key). Set FINNHUB_API_KEY."""
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        return out
    sym = _finnhub_symbol(stock)
    if not sym:
        return out

    sym_q = quote(sym, safe="")
    tok_q = quote(token, safe="")

    profile = _http_get_json_url(f"{_FINNHUB_API}/stock/profile2?symbol={sym_q}&token={tok_q}")
    if isinstance(profile, dict) and not profile.get("error"):
        mc = profile.get("marketCapitalization")
        if isinstance(mc, (int, float)) and mc > 0:
            v = float(mc)
            # Finnhub reports market cap in millions USD for many tickers.
            if v < 1e11:
                v *= 1_000_000.0
            out["marketCap"] = _format_market_cap_usd(v)
        so = profile.get("shareOutstanding")
        if isinstance(so, (int, float)) and so > 0:
            out["sharesOutstanding"] = _format_shares_outstanding(float(so))

    metric_payload = _http_get_json_url(
        f"{_FINNHUB_API}/stock/metric?symbol={sym_q}&metric=all&token={tok_q}"
    )
    if isinstance(metric_payload, dict) and not metric_payload.get("error"):
        m = metric_payload.get("metric")
        if isinstance(m, dict):
            beta = m.get("beta") if isinstance(m.get("beta"), (int, float)) else m.get("betaAnnual")
            if isinstance(beta, (int, float)):
                out["beta"] = round(float(beta), 2)
            pe = m.get("peTTM") or m.get("peNormalizedAnnual") or m.get("peExclExtraTTM")
            if isinstance(pe, (int, float)) and pe > 0:
                out["peTtm"] = round(float(pe), 2)
            eps = m.get("epsTTM") or m.get("epsNormalizedAnnual") or m.get("epsBasicExclExtraTTM")
            if isinstance(eps, (int, float)):
                out["epsTtm"] = round(float(eps), 2)
            div_yield = m.get("dividendYieldIndicatedAnnual") or m.get("dividendYieldIndicated")
            if isinstance(div_yield, (int, float)) and div_yield >= 0:
                raw = float(div_yield)
                out["dividendYieldPercent"] = round(raw * 100, 2) if raw <= 1 else round(raw, 2)
            dps = m.get("dividendPerShareTTM") or m.get("dividendPerShareAnnual")
            if isinstance(dps, (int, float)) and dps >= 0:
                out["dividendQuarterly"] = round(float(dps) / 4.0, 4)

    today = date.today()
    earn_from = today.isoformat()
    earn_to = (today + timedelta(days=400)).isoformat()
    earn_url = f"{_FINNHUB_API}/calendar/earnings?{urlencode({'symbol': sym, 'from': earn_from, 'to': earn_to, 'token': token})}"
    earn = _http_get_json_url(earn_url)
    if isinstance(earn, dict):
        earnings = earn.get("earningsCalendar")
        if isinstance(earnings, list) and earnings:
            future: list[str] = []
            for row in earnings:
                if not isinstance(row, dict):
                    continue
                d = row.get("date")
                if not isinstance(d, str) or len(d) < 10:
                    continue
                try:
                    if date.fromisoformat(d[:10]) >= today:
                        future.append(d[:10])
                except ValueError:
                    continue
            if future:
                out["nextEarningsDate"] = min(future)

    div_url = f"{_FINNHUB_API}/stock/dividend?{urlencode({'symbol': sym, 'from': (today - timedelta(days=400)).isoformat(), 'to': (today + timedelta(days=400)).isoformat(), 'token': token})}"
    div_data = _http_get_any(div_url)
    if isinstance(div_data, list) and div_data:
        future_ex = None
        future_pay = None
        for row in div_data:
            if not isinstance(row, dict):
                continue
            exd = row.get("date")
            pay = row.get("paymentDate")
            if isinstance(exd, str) and len(exd) >= 10:
                try:
                    ex_date = date.fromisoformat(exd[:10])
                except ValueError:
                    continue
                if ex_date >= today and future_ex is None:
                    future_ex = exd[:10]
                if isinstance(pay, str) and len(pay) >= 10:
                    try:
                        pay_date = date.fromisoformat(pay[:10])
                    except ValueError:
                        continue
                    if pay_date >= today and future_pay is None:
                        future_pay = pay[:10]
        if future_ex:
            out["exDividendDate"] = future_ex
        if future_pay:
            out["dividendPayDate"] = future_pay

    return out


def _null_fundamentals() -> dict[str, None]:
    return {
        "marketCap": None,
        "sharesOutstanding": None,
        "beta": None,
        "peTtm": None,
        "epsTtm": None,
        "dividendQuarterly": None,
        "dividendYieldPercent": None,
        "exDividendDate": None,
        "dividendPayDate": None,
        "nextEarningsDate": None,
    }


def _range_marker_percent(current: object, low: object, high: object) -> float | None:
    """Where current sits between low and high, as 0–100 (server-side)."""
    if current is None or low is None or high is None:
        return None
    try:
        c = float(current)
        lo = float(low)
        hi = float(high)
    except (TypeError, ValueError):
        return None
    if hi <= lo:
        return None
    return min(100.0, max(0.0, (c - lo) / (hi - lo) * 100.0))


def _merge_range_markers(out: dict[str, object]) -> dict[str, object]:
    merged = dict(out)
    merged["dayRangeMarkerPercent"] = _range_marker_percent(
        merged.get("lastPrice"),
        merged.get("dayLow"),
        merged.get("dayHigh"),
    )
    merged["week52RangeMarkerPercent"] = _range_marker_percent(
        merged.get("lastPrice"),
        merged.get("fiftyTwoWeekLow"),
        merged.get("fiftyTwoWeekHigh"),
    )
    return merged


def get_key_statistics(stock_id: str) -> dict[str, object] | None:
    stock = quote_service.get_stock(stock_id)
    quote = quote_service.get_quote(stock_id)
    if stock is None or quote is None:
        return None

    base: dict[str, object] = {
        "open": quote.open,
        "previousClose": quote.previous_close,
        "volume": quote.volume,
        "lastPrice": quote.last_price,
        "dayLow": quote.low,
        "dayHigh": quote.high,
        "fiftyTwoWeekLow": None,
        "fiftyTwoWeekHigh": None,
        "fiftyTwoWeekLowDate": None,
        "fiftyTwoWeekHighDate": None,
        "avgVolume10d": None,
        **_null_fundamentals(),
    }

    merged: dict[str, object]
    if stock.market == "US":
        merged = _us_from_yahoo(stock, base)
    else:
        merged = _tw_from_candles(stock, base)

    return _merge_range_markers(_finnhub_enrich(merged, stock))
