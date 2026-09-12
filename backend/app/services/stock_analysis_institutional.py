"""Compact Taiwan institutional-investor facts for AI stock analysis."""

from __future__ import annotations

from typing import Protocol

from app.models.stock import StockRecord
from app.services.taiwan_market_data import net_shares_to_lots
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorsResult,
    tw_institutional_service,
)

_DEFAULT_PERIOD_DAYS = 90
_NET_WINDOW_SHORT = 5
_NET_WINDOW_LONG = 20


class InstitutionalInvestorsLookup(Protocol):
    def get_institutional_investors(
        self,
        stock_id: str,
        *,
        days: int = _DEFAULT_PERIOD_DAYS,
    ) -> TaiwanInstitutionalInvestorsResult: ...


def build_institutional_analysis(
    stock_id: str,
    stock: StockRecord,
    *,
    institutional_service: InstitutionalInvestorsLookup | None = None,
    days: int = _DEFAULT_PERIOD_DAYS,
) -> dict[str, object] | None:
    """Build compact institutional analysis for Taiwan stocks.

    Returns ``None`` for non-TW markets. Soft-fails to an unavailable payload
    when the institutional service raises or returns no usable history.
    Net figures are in 張 (lots); do not divide again after conversion.
    """
    if stock.market != "TW":
        return None

    # Reuse the shared instance so its result cache actually applies.
    service = institutional_service or tw_institutional_service
    try:
        result = service.get_institutional_investors(stock_id, days=days)
    except Exception as exc:  # noqa: BLE001 — soft degradation for AI payload
        return {
            "available": False,
            "reason": "provider_failed",
            "reasonDetail": type(exc).__name__,
            "market": "TW",
        }

    history = tuple(result.history)
    if not history:
        return {
            "available": False,
            "reason": "no_history",
            "market": "TW",
            "source": result.source,
        }

    symbol = result.symbol or stock.symbol
    foreign = [net_shares_to_lots(record.foreign_net_shares, symbol=symbol) for record in history]
    trusts = [
        net_shares_to_lots(record.investment_trust_net_shares, symbol=symbol)
        for record in history
    ]
    dealers = [net_shares_to_lots(record.dealer_net_shares, symbol=symbol) for record in history]
    totals = [net_shares_to_lots(record.total_net_shares, symbol=symbol) for record in history]

    foreign_5d = _sum_named_window(foreign, _NET_WINDOW_SHORT)
    foreign_20d = _sum_named_window(foreign, _NET_WINDOW_LONG)
    trust_5d = _sum_named_window(trusts, _NET_WINDOW_SHORT)
    trust_20d = _sum_named_window(trusts, _NET_WINDOW_LONG)
    dealer_5d = _sum_named_window(dealers, _NET_WINDOW_SHORT)
    dealer_20d = _sum_named_window(dealers, _NET_WINDOW_LONG)
    total_5d = _sum_named_window(totals, _NET_WINDOW_SHORT)
    total_20d = _sum_named_window(totals, _NET_WINDOW_LONG)

    buy_days, sell_days = _consecutive_net_streak(totals)
    missing_days = sum(1 for lots in totals if lots is None)

    return {
        "available": True,
        "market": "TW",
        "unit": "lots_張",
        "periodDays": days,
        "sessionCount": len(history),
        "coveredSessions5d": _covered_sessions(totals, _NET_WINDOW_SHORT),
        "coveredSessions20d": _covered_sessions(totals, _NET_WINDOW_LONG),
        "missingSessionCount": missing_days,
        "dataAsOf": result.latest_date or history[-1].date,
        "foreignNetLots5d": foreign_5d,
        "foreignNetLots20d": foreign_20d,
        "investmentTrustNetLots5d": trust_5d,
        "investmentTrustNetLots20d": trust_20d,
        "dealerNetLots5d": dealer_5d,
        "dealerNetLots20d": dealer_20d,
        "totalNetLots5d": total_5d,
        "totalNetLots20d": total_20d,
        "consecutiveNetBuyDays": buy_days,
        "consecutiveNetSellDays": sell_days,
        "agreement": _agreement(foreign_5d, trust_5d, dealer_5d),
        "source": result.source,
    }


def _covered_sessions(values: list[float | None], window: int) -> int:
    return sum(1 for value in values[-window:] if value is not None)


def _sum_named_window(values: list[float | None], window: int) -> float | None:
    """Sum the newest ``window`` sessions only when that many values exist.

    A 1-session fallback must not be labeled as a 5-day or 20-day total.
    """
    tail = values[-window:]
    usable = [value for value in tail if value is not None]
    if len(usable) < window:
        return None
    return round(sum(usable), 3)


def _consecutive_net_streak(total_lots: list[float | None]) -> tuple[int, int]:
    """Count same-sign total-net streak from the newest day backward."""
    buy_days = 0
    sell_days = 0
    for lots in reversed(total_lots):
        if lots is None or lots == 0:
            break
        if lots > 0:
            if sell_days:
                break
            buy_days += 1
        else:
            if buy_days:
                break
            sell_days += 1
    return buy_days, sell_days


def _agreement(
    foreign_net: float | None,
    trust_net: float | None,
    dealer_net: float | None,
) -> str:
    values = (foreign_net, trust_net, dealer_net)
    if any(value is None for value in values):
        return "unknown"
    if all(value > 0 for value in values):
        return "broad_buy"
    if all(value < 0 for value in values):
        return "broad_sell"
    return "mixed"
