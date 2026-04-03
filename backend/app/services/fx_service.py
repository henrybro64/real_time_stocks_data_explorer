from datetime import datetime

from zoneinfo import ZoneInfo
from app.services.http_client import HttpFetchError, fetch_json

_YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    "TWD%3DX?interval=1d&range=5d"
)
_REQUEST_TIMEOUT_SECONDS = 8
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_FALLBACK_USD_TWD_RATE = 32.0


def get_usd_twd_fx_snapshot() -> dict[str, str | float]:
    rate = _fetch_usd_twd_rate()
    if rate is None:
        rate = _FALLBACK_USD_TWD_RATE
    return {
        "pair": "USD/TWD",
        "rate": rate,
        "timestamp": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
    }


def _fetch_usd_twd_rate() -> float | None:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    try:
        payload = fetch_json(
            _YAHOO_CHART_URL,
            headers=headers,
            timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
            retries=2,
        )
    except HttpFetchError:
        return None

    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error"):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return None
    block = results[0]
    if not isinstance(block, dict):
        return None
    meta = block.get("meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get("regularMarketPrice")
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return round(float(value), 4)
