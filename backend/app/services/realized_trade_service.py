from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from app.db.realized_trade_model import RealizedTrade
from app.repositories.realized_trade_repository import realized_trade_repository
from app.schemas.realized_trade import (
    RealizedTradeCreate,
    RealizedTradeCurrency,
    RealizedTradeUpdate,
)

SUPPORTED_CURRENCIES = frozenset({"TWD", "USD"})
SYMBOL_MAX_LENGTH = 32
DECIMAL_SCALE = Decimal("0.000001")
MAX_TRADE_DECIMAL = Decimal("99999999999999.999999")


class RealizedTradeValidationError(ValueError):
    pass


class RealizedTradeNotFoundError(ValueError):
    pass


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise RealizedTradeValidationError("Symbol is required")
    if len(normalized) > SYMBOL_MAX_LENGTH:
        raise RealizedTradeValidationError(
            f"Symbol must be at most {SYMBOL_MAX_LENGTH} characters",
        )
    return normalized


def normalize_currency(currency: str) -> RealizedTradeCurrency:
    normalized = currency.strip().upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise RealizedTradeValidationError(f"Unsupported currency: {currency}")
    return normalized  # type: ignore[return-value]


def _to_decimal(value: float, *, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RealizedTradeValidationError(f"Invalid {field_name}") from exc
    if not decimal_value.is_finite():
        raise RealizedTradeValidationError(f"Invalid {field_name}")
    return decimal_value


def _to_trade_decimal(value: float, *, field_name: str) -> Decimal:
    decimal_value = _to_decimal(value, field_name=field_name)
    quantized = decimal_value.quantize(DECIMAL_SCALE, rounding=ROUND_HALF_UP)
    if quantized <= 0:
        raise RealizedTradeValidationError(f"{field_name} must be greater than 0")
    if quantized.copy_abs() > MAX_TRADE_DECIMAL:
        raise RealizedTradeValidationError(f"{field_name} exceeds allowed precision")
    return quantized


def calculate_realized_metrics(
    buy_price: Decimal,
    sell_price: Decimal,
    shares: Decimal,
) -> tuple[float, float]:
    cost = buy_price * shares
    proceeds = sell_price * shares
    realized_gain = proceeds - cost
    realized_return_percent = ((sell_price - buy_price) / buy_price) * Decimal(100)
    return (
        float(realized_gain.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        float(
            realized_return_percent.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        ),
    )


def _validate_dates(buy_date: date | None, sell_date: date) -> None:
    if buy_date is not None and buy_date > sell_date:
        raise RealizedTradeValidationError("buyDate must be on or before sellDate")


def _validate_positive_trade_values(
    buy_price: Decimal,
    sell_price: Decimal,
    shares: Decimal,
    buy_date: date | None,
    sell_date: date,
) -> None:
    if buy_price <= 0 or sell_price <= 0 or shares <= 0:
        raise RealizedTradeValidationError("Prices and shares must be greater than 0")
    _validate_dates(buy_date, sell_date)


def _build_trade_updates(payload: RealizedTradeUpdate) -> dict[str, object]:
    updates: dict[str, object] = {}
    if payload.symbol is not None:
        updates["symbol"] = normalize_symbol(payload.symbol)
    if payload.currency is not None:
        updates["currency"] = normalize_currency(payload.currency)
    if payload.buyPrice is not None:
        updates["buy_price"] = _to_trade_decimal(payload.buyPrice, field_name="buyPrice")
    if payload.sellPrice is not None:
        updates["sell_price"] = _to_trade_decimal(payload.sellPrice, field_name="sellPrice")
    if payload.shares is not None:
        updates["shares"] = _to_trade_decimal(payload.shares, field_name="shares")
    if "buyDate" in payload.model_fields_set:
        updates["buy_date"] = payload.buyDate
    if payload.sellDate is not None:
        updates["sell_date"] = payload.sellDate
    return updates


class RealizedTradeService:
    def list_trades(
        self,
        user_id: str,
        *,
        currency: RealizedTradeCurrency | None = None,
    ) -> list[RealizedTrade]:
        if currency is not None:
            normalize_currency(currency)
        return realized_trade_repository.list_trades(user_id, currency=currency)

    def create_trade(self, user_id: str, payload: RealizedTradeCreate) -> RealizedTrade:
        symbol = normalize_symbol(payload.symbol)
        currency = normalize_currency(payload.currency)
        buy_price = _to_trade_decimal(payload.buyPrice, field_name="buyPrice")
        sell_price = _to_trade_decimal(payload.sellPrice, field_name="sellPrice")
        shares = _to_trade_decimal(payload.shares, field_name="shares")
        _validate_positive_trade_values(
            buy_price,
            sell_price,
            shares,
            payload.buyDate,
            payload.sellDate,
        )
        return realized_trade_repository.create_trade(
            user_id=user_id,
            symbol=symbol,
            currency=currency,
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
            buy_date=payload.buyDate,
            sell_date=payload.sellDate,
        )

    def update_trade(
        self,
        user_id: str,
        trade_id: int,
        payload: RealizedTradeUpdate,
    ) -> RealizedTrade:
        updates = _build_trade_updates(payload)
        if not updates:
            existing = realized_trade_repository.get_trade(user_id, trade_id)
            if existing is None:
                raise RealizedTradeNotFoundError(f"Realized trade not found: {trade_id}")
            return existing

        updated = realized_trade_repository.update_trade(user_id, trade_id, updates)
        if updated is None:
            raise RealizedTradeNotFoundError(f"Realized trade not found: {trade_id}")
        _validate_positive_trade_values(
            updated.buy_price,
            updated.sell_price,
            updated.shares,
            updated.buy_date,
            updated.sell_date,
        )
        return updated

    def delete_trade(self, user_id: str, trade_id: int) -> None:
        deleted = realized_trade_repository.delete_trade(user_id, trade_id)
        if not deleted:
            raise RealizedTradeNotFoundError(f"Realized trade not found: {trade_id}")


realized_trade_service = RealizedTradeService()
