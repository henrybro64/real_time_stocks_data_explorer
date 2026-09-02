from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from app.db.models import RealizedTrade
from app.repositories.realized_trade_repository import realized_trade_repository
from app.schemas.realized_trade import (
    RealizedTradeCreate,
    RealizedTradeCurrency,
    RealizedTradeUpdate,
)

SUPPORTED_CURRENCIES = frozenset({"TWD", "USD"})


class RealizedTradeValidationError(ValueError):
    pass


class RealizedTradeNotFoundError(ValueError):
    pass


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise RealizedTradeValidationError("Symbol is required")
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
        buy_price = _to_decimal(payload.buyPrice, field_name="buyPrice")
        sell_price = _to_decimal(payload.sellPrice, field_name="sellPrice")
        shares = _to_decimal(payload.shares, field_name="shares")
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
        existing = realized_trade_repository.get_trade(user_id, trade_id)
        if existing is None:
            raise RealizedTradeNotFoundError(f"Realized trade not found: {trade_id}")

        symbol = normalize_symbol(payload.symbol) if payload.symbol is not None else existing.symbol
        currency = (
            normalize_currency(payload.currency)
            if payload.currency is not None
            else existing.currency
        )
        buy_price = (
            _to_decimal(payload.buyPrice, field_name="buyPrice")
            if payload.buyPrice is not None
            else existing.buy_price
        )
        sell_price = (
            _to_decimal(payload.sellPrice, field_name="sellPrice")
            if payload.sellPrice is not None
            else existing.sell_price
        )
        shares = (
            _to_decimal(payload.shares, field_name="shares")
            if payload.shares is not None
            else existing.shares
        )
        buy_date = (
            payload.buyDate
            if "buyDate" in payload.model_fields_set
            else existing.buy_date
        )
        sell_date = existing.sell_date if payload.sellDate is None else payload.sellDate

        _validate_positive_trade_values(buy_price, sell_price, shares, buy_date, sell_date)

        updated = realized_trade_repository.update_trade(
            user_id,
            trade_id,
            symbol=symbol,
            currency=currency,
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
            buy_date=buy_date,
            sell_date=sell_date,
        )
        if updated is None:
            raise RealizedTradeNotFoundError(f"Realized trade not found: {trade_id}")
        return updated

    def delete_trade(self, user_id: str, trade_id: int) -> None:
        deleted = realized_trade_repository.delete_trade(user_id, trade_id)
        if not deleted:
            raise RealizedTradeNotFoundError(f"Realized trade not found: {trade_id}")


realized_trade_service = RealizedTradeService()
