"""Latest real trade in a TWSE MIS ``getStockInfo`` row (#258).

Live MIS rows are 5-second snapshots. ``z`` is filled only when a trade
matched inside that snapshot, so during the session it is usually ``-``. The
``trade`` object carries the most recent matched trade (``z`` price, ``t``
time) and is the source of the current price.

``y`` is the reference/previous close and ``pz`` is undocumented (observed
``-`` throughout live sessions). Neither is ever a trade.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from app.services.tw_price_limits import parse_price


@dataclass(frozen=True)
class MisLastTrade:
    price: Decimal
    # ``HH:MM:SS`` of the trade. ``None`` means the trade is in this snapshot,
    # so the snapshot time applies.
    trade_time: str | None


def mis_last_trade(row: Mapping[str, object]) -> MisLastTrade | None:
    """The latest matched trade in ``row``, or ``None`` when there is none.

    A trade outside the snapshot's own high/low, or stamped after the
    snapshot, is incoherent and rejected. The ``trade`` object also needs
    session volume, so it cannot be a trade from an earlier session.
    """
    candidate = _trade_object(row)
    if candidate is None:
        price = _traded_price(row.get("z"))
        candidate = MisLastTrade(price=price, trade_time=None) if price is not None else None
    if candidate is None:
        return None

    high = parse_price(row.get("h"))
    low = parse_price(row.get("l"))
    if high is not None and low is not None and not low <= candidate.price <= high:
        return None
    return candidate


def _trade_object(row: Mapping[str, object]) -> MisLastTrade | None:
    trade = row.get("trade")
    if not isinstance(trade, Mapping):
        return None
    price = _traded_price(trade.get("z"))
    raw_time = trade.get("t")
    trade_clock = _clock(raw_time)
    if price is None or trade_clock is None:
        return None
    snapshot_clock = _clock(row.get("t"))
    if snapshot_clock is not None and trade_clock > snapshot_clock:
        return None
    volume = parse_price(row.get("v"))
    if volume is None or volume <= 0:
        return None
    return MisLastTrade(price=price, trade_time=str(raw_time).strip())


def _traded_price(value: object) -> Decimal | None:
    price = parse_price(value)
    if price is None or price <= 0:
        return None
    return price


def _clock(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None
