"""Taiwan daily price-limit rules and classification.

Limit-up / limit-down is decided by comparing the traded price to the session's
upper and lower limit prices. A rounded change percentage near +10% or -10% is
never treated as proof of a limit.

Authoritative limit prices (TWSE MIS ``u`` / ``w``, or the published daily band)
win when both are present. Calculation is only a fallback for a standard equity
or ETF whose reference price is known and whose limit prices are missing. The
fallback follows the current TWSE/TPEx ±10% rule and tick size, including the
tick of the unrounded limit price.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Literal

PriceLimitStatus = Literal["limit_up", "limit_down", "none", "unknown"]
TickKind = Literal["equity", "etf"]

# Upper bound is exclusive. ``None`` is the open-ended top band.
_EQUITY_TICKS: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.1")),
    (Decimal("500"), Decimal("0.5")),
    (Decimal("1000"), Decimal("1")),
    (None, Decimal("5")),
)
# 4-digit ETFs such as 0050 use a finer schedule than common stock.
_ETF_TICKS: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("50"), Decimal("0.01")),
    (None, Decimal("0.05")),
)
_NO_LIMIT_SENTINEL = Decimal("9999")
_MISSING_PRICE_TOKENS = {"", "-", "--", "---", "----", "null", "none", "n/a", "na"}


def tick_kind_for_symbol(symbol: str) -> TickKind | None:
    """Return the tick schedule for symbols we know how to calculate.

    Anything else (warrants, leveraged ETFs, foreign-currency codes) is left
    unclassified unless the provider sent authoritative limit prices.
    """
    code = symbol.strip()
    if not code.isdigit() or len(code) != 4:
        return None
    if code.startswith("00"):
        return "etf"
    return "equity"


def tick_size(price: Decimal, kind: TickKind) -> Decimal:
    table = _ETF_TICKS if kind == "etf" else _EQUITY_TICKS
    for upper, size in table:
        if upper is None or price < upper:
            return size
    return table[-1][1]


def limit_up_price(reference: Decimal, *, kind: TickKind) -> Decimal:
    """漲停價: reference × 1.1, rounded down to the limit price's tick."""
    return _round_to_tick(reference * Decimal("1.1"), rounding=ROUND_DOWN, kind=kind)


def limit_down_price(reference: Decimal, *, kind: TickKind) -> Decimal:
    """跌停價: reference × 0.9, rounded up to the limit price's tick."""
    return _round_to_tick(reference * Decimal("0.9"), rounding=ROUND_UP, kind=kind)


def parse_price(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value).replace(",", "").strip()
        if text.casefold() in _MISSING_PRICE_TOKENS:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    if not number.is_finite() or number < 0:
        return None
    return number


def parse_signed_decimal(value: object) -> Decimal | None:
    """Parse a signed decimal such as a change percentage. Prices stay non-negative."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value).replace(",", "").strip()
        if text.casefold() in _MISSING_PRICE_TOKENS:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    if not number.is_finite():
        return None
    return number


def decimal_to_api_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(quantized)


def restore_cached_price_limit(
    *,
    last_price: float,
    status: object,
    reference_price: object,
    limit_up_price: object,
    limit_down_price: object,
) -> tuple[PriceLimitStatus | None, float | None, float | None, float | None]:
    """Keep cached limit fields only when they still describe this quote.

    Older payloads omit the fields. Those stay unset so callers can treat them
    as unknown without inventing a limit. A stored 漲停／跌停／none label is kept
    only when both official limit prices are present and the traded price still
    matches that label.
    """
    if status is None:
        return (None, None, None, None)
    if status not in {"limit_up", "limit_down", "none", "unknown"}:
        return ("unknown", None, None, None)

    reference = _cached_price(reference_price)
    limit_up = _cached_price(limit_up_price)
    limit_down = _cached_price(limit_down_price)
    if status == "unknown":
        return ("unknown", reference, limit_up, limit_down)
    if limit_up is None or limit_down is None:
        return ("unknown", None, None, None)
    at_up = _prices_match(last_price, limit_up)
    at_down = _prices_match(last_price, limit_down)
    if status == "limit_up" and at_up and not at_down:
        return ("limit_up", reference, limit_up, limit_down)
    if status == "limit_down" and at_down and not at_up:
        return ("limit_down", reference, limit_up, limit_down)
    if status == "none" and not at_up and not at_down:
        return ("none", reference, limit_up, limit_down)
    return ("unknown", None, None, None)


def _cached_price(value: object) -> float | None:
    return decimal_to_api_float(parse_price(value))


def _prices_match(left: float, right: float) -> bool:
    return abs(left - right) <= 0.005


def is_no_limit_band(limit_up: Decimal, limit_down: Decimal) -> bool:
    """TWSE/TPEx publish 9999.95 / 0.01 when a security has no daily band."""
    return limit_up >= _NO_LIMIT_SENTINEL or (
        limit_down <= Decimal("0.01") and limit_up >= _NO_LIMIT_SENTINEL
    )


def classify_taiwan_price_limit(
    *,
    last_price: Decimal | None,
    reference_price: Decimal | None,
    limit_up_price_value: Decimal | None,
    limit_down_price_value: Decimal | None,
    traded: bool,
    symbol: str | None = None,
    allow_calculated: bool = True,
) -> tuple[PriceLimitStatus, Decimal | None, Decimal | None, Decimal | None]:
    """Classify one quote.

    Returns ``(status, reference, limit_up, limit_down)``. Missing trades,
    suspended rows, and incomplete bands are ``unknown`` so callers can drop
    them from a definitive 漲停／跌停 list.

    ``allow_calculated`` keeps the ±10% tick fallback used by market-wide
    rankings when both official limits are absent. Regular quotes pass
    ``False``: a missing MIS ``u``/``w`` pair is incomplete and must stay
    ``unknown`` rather than become a badge.
    """
    if not traded or last_price is None:
        return ("unknown", reference_price, limit_up_price_value, limit_down_price_value)

    authoritative = limit_up_price_value is not None and limit_down_price_value is not None
    if authoritative:
        assert limit_up_price_value is not None and limit_down_price_value is not None
        if is_no_limit_band(limit_up_price_value, limit_down_price_value):
            return ("none", reference_price, limit_up_price_value, limit_down_price_value)
        return (
            _status_for_prices(last_price, limit_up_price_value, limit_down_price_value),
            reference_price,
            limit_up_price_value,
            limit_down_price_value,
        )

    kind = tick_kind_for_symbol(symbol or "")
    if (
        not allow_calculated
        or reference_price is None
        or reference_price <= 0
        or kind is None
        or limit_up_price_value is not None
        or limit_down_price_value is not None
    ):
        # A single missing side, a security we cannot tick-round, or a caller
        # that refuses the calculated band stays unknown.
        return ("unknown", reference_price, limit_up_price_value, limit_down_price_value)

    calculated_up = limit_up_price(reference_price, kind=kind)
    calculated_down = limit_down_price(reference_price, kind=kind)
    return (
        _status_for_prices(last_price, calculated_up, calculated_down),
        reference_price,
        calculated_up,
        calculated_down,
    )


def _status_for_prices(
    last_price: Decimal,
    limit_up: Decimal,
    limit_down: Decimal,
) -> PriceLimitStatus:
    at_up = last_price == limit_up
    at_down = last_price == limit_down
    if at_up and at_down:
        return "unknown"
    if at_up:
        return "limit_up"
    if at_down:
        return "limit_down"
    return "none"


def _round_to_tick(raw: Decimal, *, rounding: str, kind: TickKind) -> Decimal:
    size = tick_size(raw, kind)
    rounded = (raw / size).to_integral_value(rounding=rounding) * size
    crossed = tick_size(rounded, kind)
    if crossed != size:
        rounded = (raw / crossed).to_integral_value(rounding=rounding) * crossed
    return rounded
