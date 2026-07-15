"""Aggregate key statistics for stock detail: quote + Yahoo chart (US) or candles (TW)."""

from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.quote_service import quote_service

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TIMEOUT = 15
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
_FINNHUB_API = "https://finnhub.io/api/v1"
_TWSE_DAILY_PE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
_TPEX_DAILY_PE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
_NULL_OFFICIAL_PE_MARKERS = frozenset({"", "-", "--", "N/A", "NA"})
_UNSUPPORTED_US_QUOTE_TYPES = frozenset({"ETF", "ETN", "FUND", "MUTUALFUND", "MONEYMARKETFUND", "INDEX"})
_UNSET = object()
_YFINANCE_INFO_TTL_SECONDS = 15 * 60
_YFINANCE_INFO_MAX_SIZE = 512
_YFINANCE_PRELOAD_WORKERS = 8
_yfinance_info_cache: dict[str, tuple[float, dict[str, object] | None]] = {}


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
    try:
        data = quote_service.get_history(stock.id, "1Y")
    except RuntimeError:
        # History outages map to 502 on /history; key-stats should still return quote-based fields.
        return out
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


def _quote_local_date(stock: StockRecord, quote_record: QuoteRecord) -> date:
    raw_timestamp = quote_record.timestamp.strip()
    if raw_timestamp:
        try:
            return datetime.fromisoformat(raw_timestamp).astimezone(ZoneInfo(stock.timezone)).date()
        except ValueError:
            pass
    return date.today()


def _recent_weekdays(anchor: date, limit: int = 5) -> list[date]:
    days: list[date] = []
    current = anchor
    while len(days) < limit:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return days


def _yyyymmdd_to_iso(raw_value: object) -> str | None:
    cleaned = str(raw_value or "").strip()
    if len(cleaned) != 8 or not cleaned.isdigit():
        return None
    return f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:8]}"


def _positive_float(value: object) -> float | None:
    parsed = _parse_number(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _finite_rounded(value: float) -> float | None:
    if not math.isfinite(value):
        return None
    return round(value, 2)


def _parse_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return _finite_rounded(float(value))
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned.upper() in _NULL_OFFICIAL_PE_MARKERS or cleaned in {"-", "--"}:
        return None
    try:
        return _finite_rounded(float(cleaned))
    except ValueError:
        return None


def _parse_official_pe_value(raw_value: object) -> tuple[float | None, str | None]:
    if raw_value is None:
        return None, "missing"
    if isinstance(raw_value, (int, float)):
        parsed = _finite_rounded(float(raw_value))
        if parsed is None:
            return None, "missing"
        return parsed, None

    cleaned = str(raw_value).strip().replace(",", "")
    if not cleaned or cleaned.upper() in _NULL_OFFICIAL_PE_MARKERS or cleaned in {"-", "--"}:
        return None, "missing"
    try:
        parsed = _finite_rounded(float(cleaned))
        if parsed is None:
            return None, "missing"
        return parsed, None
    except ValueError:
        return None, "malformed"


def _field_index(fields: list[object], *names: str) -> int | None:
    normalized = [str(field).strip() for field in fields]
    for name in names:
        try:
            return normalized.index(name)
        except ValueError:
            continue
    return None


def _row_value(row: list[object], index: int | None) -> object | None:
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


def _normalize_stock_id_to_yfinance(stock: StockRecord) -> str:
    symbol = stock.symbol.strip().upper()
    if stock.market == "US":
        return symbol
    if stock.market == "TW":
        suffix = ".TWO" if stock.exchange == "TPEx" else ".TW"
        return f"{symbol}{suffix}"
    return symbol


def _yfinance_cache_key(stock: StockRecord) -> str:
    return _normalize_stock_id_to_yfinance(stock)


def _read_yfinance_info_cache(key: str) -> dict[str, object] | None | object:
    cached = _yfinance_info_cache.get(key)
    if cached is None:
        return _UNSET
    fetched_at, payload = cached
    if time.time() - fetched_at >= _YFINANCE_INFO_TTL_SECONDS:
        _yfinance_info_cache.pop(key, None)
        return _UNSET
    return payload


def _purge_yfinance_info_cache(now_epoch: float | None = None) -> None:
    now = now_epoch if now_epoch is not None else time.time()
    expired_keys = [
        key
        for key, (fetched_at, _) in _yfinance_info_cache.items()
        if now - fetched_at >= _YFINANCE_INFO_TTL_SECONDS
    ]
    for key in expired_keys:
        _yfinance_info_cache.pop(key, None)
    overflow = len(_yfinance_info_cache) - _YFINANCE_INFO_MAX_SIZE
    if overflow <= 0:
        return
    oldest_keys = sorted(_yfinance_info_cache, key=lambda key: _yfinance_info_cache[key][0])
    for key in oldest_keys[:overflow]:
        _yfinance_info_cache.pop(key, None)


def _write_yfinance_info_cache(key: str, payload: dict[str, object] | None) -> None:
    _yfinance_info_cache[key] = (time.time(), payload)
    _purge_yfinance_info_cache()


def _merge_yfinance_fast_info(info: dict[str, object], ticker: object) -> dict[str, object]:
    merged = dict(info)
    fast_info = getattr(ticker, "fast_info", None)
    if fast_info is not None:
        for key, target_key in (
            ("currency", "currency"),
            ("market_cap", "marketCap"),
            ("lastPrice", "currentPrice"),
        ):
            try:
                value = fast_info.get(key)
            except Exception:  # noqa: BLE001 - yfinance fast_info can raise lazily
                value = None
            if value is not None and target_key not in merged:
                merged[target_key] = value
    return merged


def _fetch_yfinance_info_uncached(stock: StockRecord) -> dict[str, object] | None:
    try:
        import yfinance as yf  # pylint: disable=import-outside-toplevel
    except Exception:  # noqa: BLE001 - optional dependency or import failure
        return None

    ticker = yf.Ticker(_yfinance_cache_key(stock))
    try:
        info = dict(ticker.info or {})
    except Exception:  # noqa: BLE001 - yfinance can fail in many shapes
        return None
    return _merge_yfinance_fast_info(info, ticker)


def _load_yfinance_info(stock: StockRecord) -> dict[str, object] | None:
    key = _yfinance_cache_key(stock)
    cached = _read_yfinance_info_cache(key)
    if cached is not _UNSET:
        return cached if isinstance(cached, dict) else None

    info = _fetch_yfinance_info_uncached(stock)
    _write_yfinance_info_cache(key, info)
    return info


def preload_yfinance_info(stocks: list[StockRecord]) -> None:
    """Warm the TTL yfinance info cache for many U.S. stocks with parallel lookups."""
    pending = [
        stock
        for stock in stocks
        if stock.market == "US" and _read_yfinance_info_cache(_yfinance_cache_key(stock)) is _UNSET
    ]
    if not pending:
        return

    worker_count = min(_YFINANCE_PRELOAD_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(_load_yfinance_info, pending))


def _provider_quote_kind(info: dict[str, object] | None) -> str | None:
    if not isinstance(info, dict):
        return None
    for key in ("quoteType", "instrumentType"):
        raw_value = info.get(key)
        if isinstance(raw_value, str):
            kind = raw_value.strip().upper()
            if kind:
                return kind
    return None


def _looks_like_fund_or_etf(stock: StockRecord, info: dict[str, object] | None = None) -> bool:
    kind = _provider_quote_kind(info)
    if kind in _UNSUPPORTED_US_QUOTE_TYPES:
        return True

    symbol = stock.symbol.strip().upper()
    if stock.market == "TW" and symbol.startswith(("00", "02")):
        return True

    joined_names = " ".join(
        part
        for part in (stock.name, stock.name_en, stock.name_zh_tw)
        if isinstance(part, str) and part.strip()
    ).upper()
    return any(marker in joined_names for marker in ("ETF", "ETN", " FUND", "基金", "指數股票型"))


def _set_valuation_fields(
    out: dict[str, object],
    *,
    pe_ratio: float | None,
    pe_ratio_type: str | None = None,
    pe_ratio_source: str | None = None,
    price_used: float | None = None,
    price_as_of: str | None = None,
    eps_ttm: object = _UNSET,
    eps_as_of: str | None = None,
    valuation_as_of: str | None = None,
    valuation_warning: str | None = None,
) -> dict[str, object]:
    merged = dict(out)
    merged["peRatio"] = pe_ratio
    merged["peTtm"] = pe_ratio
    merged["peRatioType"] = pe_ratio_type
    merged["peRatioSource"] = pe_ratio_source
    merged["priceUsed"] = price_used
    merged["priceAsOf"] = price_as_of
    merged["epsAsOf"] = eps_as_of
    merged["valuationAsOf"] = valuation_as_of
    merged["valuationWarning"] = valuation_warning
    if eps_ttm is not _UNSET:
        merged["epsTtm"] = eps_ttm
    return merged


@lru_cache(maxsize=32)
def _fetch_twse_daily_pe_snapshot(target_day: date) -> tuple[dict[str, dict[str, object]], str | None] | None:
    payload = _http_get_any(
        f"{_TWSE_DAILY_PE_URL}?{urlencode({'date': target_day.strftime('%Y%m%d'), 'selectType': 'ALL', 'response': 'json'})}"
    )
    if not isinstance(payload, dict) or str(payload.get("stat", "")).strip().upper() != "OK":
        return None

    fields = payload.get("fields")
    rows = payload.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return None

    symbol_index = _field_index(fields, "證券代號")
    pe_index = _field_index(fields, "本益比")
    price_index = _field_index(fields, "收盤價")
    parsed_rows: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, list):
            continue
        symbol = str(_row_value(row, symbol_index) or "").strip()
        if not symbol:
            continue
        pe_ratio, pe_status = _parse_official_pe_value(_row_value(row, pe_index))
        parsed_rows[symbol] = {
            "peRatio": pe_ratio,
            "peStatus": pe_status,
            "priceUsed": _parse_number(_row_value(row, price_index)),
        }
    return parsed_rows, _yyyymmdd_to_iso(payload.get("date"))


@lru_cache(maxsize=32)
def _fetch_tpex_daily_pe_snapshot(target_day: date) -> tuple[dict[str, dict[str, object]], str | None] | None:
    payload = _http_get_any(
        f"{_TPEX_DAILY_PE_URL}?{urlencode({'date': target_day.strftime('%Y/%m/%d'), 'response': 'json'})}"
    )
    if not isinstance(payload, dict) or str(payload.get("stat", "")).strip().lower() != "ok":
        return None

    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict):
        return None

    table = tables[0]
    fields = table.get("fields")
    rows = table.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return None

    symbol_index = _field_index(fields, "股票代號")
    pe_index = _field_index(fields, "本益比")
    parsed_rows: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, list):
            continue
        symbol = str(_row_value(row, symbol_index) or "").strip()
        if not symbol:
            continue
        pe_ratio, pe_status = _parse_official_pe_value(_row_value(row, pe_index))
        parsed_rows[symbol] = {
            "peRatio": pe_ratio,
            "peStatus": pe_status,
            "priceUsed": None,
        }
    return parsed_rows, _yyyymmdd_to_iso(payload.get("date"))


def _apply_tw_official_valuation(
    out: dict[str, object],
    stock: StockRecord,
    quote_record: QuoteRecord,
) -> tuple[dict[str, object], bool]:
    fetch_snapshot = _fetch_tpex_daily_pe_snapshot if stock.exchange == "TPEx" else _fetch_twse_daily_pe_snapshot
    source_label = "TPEx official daily P/E" if stock.exchange == "TPEx" else "TWSE official daily P/E"
    saw_official_snapshot = False

    for session_day in _recent_weekdays(_quote_local_date(stock, quote_record)):
        snapshot = fetch_snapshot(session_day)
        if snapshot is None:
            continue
        saw_official_snapshot = True
        rows, valuation_as_of = snapshot
        row = rows.get(stock.symbol)
        if row is None:
            continue

        pe_ratio = _positive_float(row.get("peRatio"))
        price_used = _parse_number(row.get("priceUsed"))
        price_as_of = valuation_as_of if price_used is not None else None
        if pe_ratio is not None:
            return (
                _set_valuation_fields(
                    out,
                    pe_ratio=pe_ratio,
                    pe_ratio_type="official_daily",
                    pe_ratio_source=source_label,
                    price_used=price_used,
                    price_as_of=price_as_of,
                    valuation_as_of=valuation_as_of,
                ),
                True,
            )

        if row.get("peStatus") == "malformed":
            return (
                _set_valuation_fields(
                    out,
                    pe_ratio=None,
                    valuation_as_of=valuation_as_of,
                    valuation_warning=f"{source_label} returned a malformed P/E value.",
                ),
                True,
            )

        if _looks_like_fund_or_etf(stock):
            return (
                _set_valuation_fields(
                    out,
                    pe_ratio=None,
                    valuation_as_of=valuation_as_of,
                    valuation_warning="P/E is not reported for ETF or fund instruments.",
                ),
                True,
            )

        return (
            _set_valuation_fields(
                out,
                pe_ratio=None,
                valuation_as_of=valuation_as_of,
                valuation_warning=f"{source_label} did not report a usable P/E value.",
            ),
            True,
        )

    if _looks_like_fund_or_etf(stock):
        return (
            _set_valuation_fields(
                out,
                pe_ratio=None,
                valuation_warning="P/E is not reported for ETF or fund instruments.",
            ),
            True,
        )
    return out, saw_official_snapshot


def _apply_us_valuation(
    out: dict[str, object],
    stock: StockRecord,
    quote_record: QuoteRecord,
) -> dict[str, object]:
    legacy_eps = _parse_number(out.get("epsTtm"))
    legacy_pe = _positive_float(out.get("peTtm"))
    if legacy_pe is not None:
        return _set_valuation_fields(
            out,
            pe_ratio=legacy_pe,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Finnhub stock metric",
            eps_ttm=legacy_eps,
            valuation_as_of=_iso_date_only(quote_record.timestamp),
        )

    if _looks_like_fund_or_etf(stock):
        return _set_valuation_fields(
            out,
            pe_ratio=None,
            valuation_warning="P/E is not reported for ETF or fund instruments.",
        )

    yfinance_info = _load_yfinance_info(stock)
    if _looks_like_fund_or_etf(stock, yfinance_info):
        return _set_valuation_fields(
            out,
            pe_ratio=None,
            valuation_warning="P/E is not reported for ETF or fund instruments.",
        )

    yfinance_eps = _parse_number(yfinance_info.get("trailingEps")) if isinstance(yfinance_info, dict) else None
    eps_ttm = legacy_eps if legacy_eps is not None else yfinance_eps

    provider_pe = _positive_float(yfinance_info.get("trailingPE")) if isinstance(yfinance_info, dict) else None
    if provider_pe is not None:
        return _set_valuation_fields(
            out,
            pe_ratio=provider_pe,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Yahoo Finance trailing P/E",
            eps_ttm=eps_ttm,
            valuation_as_of=_iso_date_only(quote_record.timestamp),
        )

    price_used = _positive_float(quote_record.last_price)
    if price_used is not None and eps_ttm is not None and eps_ttm > 0:
        return _set_valuation_fields(
            out,
            pe_ratio=round(price_used / eps_ttm, 2),
            pe_ratio_type="calculated_ttm",
            pe_ratio_source="Calculated from latest price and EPS TTM",
            price_used=price_used,
            price_as_of=_iso_date_only(quote_record.timestamp),
            eps_ttm=eps_ttm,
            valuation_as_of=_iso_date_only(quote_record.timestamp),
        )

    return _set_valuation_fields(
        out,
        pe_ratio=None,
        eps_ttm=eps_ttm,
        valuation_warning="P/E could not be calculated because EPS TTM was missing or non-positive.",
    )


def _apply_valuation_enrichment(
    out: dict[str, object],
    stock: StockRecord,
    quote_record: QuoteRecord,
) -> dict[str, object]:
    if stock.market == "TW":
        enriched, handled = _apply_tw_official_valuation(out, stock, quote_record)
        if handled:
            return enriched

    if stock.market == "US":
        return _apply_us_valuation(out, stock, quote_record)

    legacy_pe = _positive_float(out.get("peTtm"))
    if legacy_pe is not None:
        return _set_valuation_fields(
            out,
            pe_ratio=legacy_pe,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Finnhub stock metric",
            eps_ttm=_parse_number(out.get("epsTtm")),
        )
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


def _null_valuation() -> dict[str, None]:
    return {
        "peRatio": None,
        "peRatioType": None,
        "peRatioSource": None,
        "priceUsed": None,
        "priceAsOf": None,
        "epsAsOf": None,
        "valuationAsOf": None,
        "valuationWarning": None,
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


def get_valuation_snapshot(stock: StockRecord, quote_record: QuoteRecord) -> dict[str, object]:
    """Shared valuation-only helper for callers that already have stock and quote objects."""
    return _apply_valuation_enrichment(_null_valuation(), stock, quote_record)


def get_valuation_snapshots(
    envelopes: list[tuple[StockRecord, QuoteRecord]],
) -> dict[str, dict[str, object]]:
    """Batch valuation lookup for ranking flows that already have quote envelopes."""
    us_stocks = [stock for stock, _ in envelopes if stock.market == "US"]
    if us_stocks:
        preload_yfinance_info(us_stocks)
    return {
        stock.id: get_valuation_snapshot(stock, quote)
        for stock, quote in envelopes
    }


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
        **_null_valuation(),
    }

    merged: dict[str, object]
    if stock.market == "US":
        merged = _us_from_yahoo(stock, base)
    else:
        merged = _tw_from_candles(stock, base)

    enriched = _finnhub_enrich(merged, stock)
    return _merge_range_markers(_apply_valuation_enrichment(enriched, stock, quote))
