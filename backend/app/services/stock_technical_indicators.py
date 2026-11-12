"""Bounded single-symbol daily/monthly MACD/KD for stock Profile (#331).

Reuses ``tw_research.indicators`` exactly. Reads **stored** daily history only
(``peek_daily_history``) — never opens a research scan or upstream month backfill.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.models.stock import StockRecord
from app.services.daily_price_history_service import daily_price_history_service
from app.services.quote_service import quote_service
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    PREFERRED_DAILY_HISTORY_LIMIT,
    PRICE_BASIS,
)
from app.services.tw_research.indicators import (
    daily_macd_kd_snapshot,
    monthly_macd_kd_snapshot,
)


class StockTechnicalIndicatorsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_stock_technical_indicators(
    stock_id: str,
    *,
    now: datetime | None = None,
    stock_loader=None,
    candles_loader=None,
) -> dict[str, Any]:
    """Return daily + completed-monthly MACD/KD for one catalog stock.

    Non-TW stocks raise ``unsupported_market``. Missing catalog raises
    ``not_found``. Insufficient history returns quality flags — never fake zeros.
    """
    loader = stock_loader or quote_service.get_catalog_stock
    stock = loader(stock_id)
    if stock is None:
        raise StockTechnicalIndicatorsError("not_found", "stock_not_in_catalog")
    if stock.market != "TW" and not str(stock.id).startswith("TW:"):
        raise StockTechnicalIndicatorsError(
            "unsupported_market",
            "macd_kd_profile_values_are_taiwan_only",
        )

    candles = list(
        (candles_loader or _default_stored_candles)(stock) or []
    )
    daily = daily_macd_kd_snapshot(candles, market="TW", now=now)
    monthly = monthly_macd_kd_snapshot(candles, market="TW", now=now)

    return {
        "stockId": stock.id,
        "exchange": stock.exchange,
        "market": stock.market,
        "priceBasis": PRICE_BASIS,
        "calculationVersion": CALCULATION_VERSION,
        "historySource": "daily_price_history_service.peek_daily_history",
        "upstreamFetch": False,
        "storedBarCount": len(candles),
        "daily": _snapshot_payload(daily),
        "monthly": _snapshot_payload(monthly),
        "notes": [
            "MACD: DIF=EMA12−EMA26; Signal=EMA9(DIF); OSC=DIF−Signal (not ×2).",
            "KD(9,3,3) with RSV seed rules matching tw_research.indicators.",
            "Monthly uses last fully completed calendar months only.",
            "quality=insufficient_history|unavailable means values are not validated.",
            "Does not start a TW research scan or unbounded upstream backfill.",
        ],
    }


def _default_stored_candles(stock: StockRecord):
    candles, _missing = daily_price_history_service.peek_daily_history(
        stock,
        "MAX",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
    )
    return list(candles)


_NUMERIC_KEYS = (
    "dif",
    "signal",
    "osc",
    "k",
    "d",
    "macdDif",
    "macdSignal",
    "macdHistogram",
    "kdK",
    "kdD",
)


def _snapshot_payload(snapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    # Public API aliases for chart/analyst clarity (#331).
    payload["macdDif"] = payload.get("dif")
    payload["macdSignal"] = payload.get("signal")
    payload["macdHistogram"] = payload.get("osc")
    payload["kdK"] = payload.get("k")
    payload["kdD"] = payload.get("d")
    payload["passStatus"] = payload.get("pass_status")
    payload["asOf"] = payload.get("as_of")
    payload["priceBasis"] = payload.get("price_basis")
    payload["calculationVersion"] = payload.get("calculation_version")
    payload["barCount"] = payload.get("bar_count")
    # Keep snake_case + aliases for research parity. When quality is not ok,
    # force numeric fields to null so clients never treat missing as zero.
    if payload.get("quality") != "ok":
        payload["unavailableReason"] = payload.get("quality")
        payload["passStatus"] = None
        for key in _NUMERIC_KEYS:
            payload[key] = None
    else:
        for key in _NUMERIC_KEYS:
            value = payload.get(key)
            if value is None:
                continue
            # Reject accidental NaN / non-finite values rather than coercing to 0.
            try:
                if not isinstance(value, (int, float)) or value != value:  # noqa: PLR0124
                    payload[key] = None
                elif isinstance(value, float) and (
                    value == float("inf") or value == float("-inf")
                ):
                    payload[key] = None
            except (TypeError, ValueError):
                payload[key] = None
    return payload
