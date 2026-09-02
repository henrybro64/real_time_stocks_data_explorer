from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.realized_trade_service import (
    RealizedTradeValidationError,
    calculate_realized_metrics,
    normalize_currency,
    normalize_symbol,
)


@pytest.mark.parametrize(
    ("buy_price", "sell_price", "shares", "expected_gain", "expected_return"),
    [
        (Decimal("920"), Decimal("1020"), Decimal("1000"), 100_000.0, 10.8696),
        (Decimal("200"), Decimal("150"), Decimal("10"), -500.0, -25.0),
        (Decimal("180.25"), Decimal("195.50"), Decimal("10.5"), 160.13, 8.4605),
        (Decimal("100"), Decimal("100"), Decimal("3"), 0.0, 0.0),
    ],
)
def test_calculate_realized_metrics(
    buy_price: Decimal,
    sell_price: Decimal,
    shares: Decimal,
    expected_gain: float,
    expected_return: float,
) -> None:
    gain, return_percent = calculate_realized_metrics(buy_price, sell_price, shares)
    assert gain == expected_gain
    assert return_percent == expected_return


def test_normalize_symbol_trims_and_uppercases() -> None:
    assert normalize_symbol("  aapl  ") == "AAPL"


def test_normalize_symbol_rejects_empty() -> None:
    with pytest.raises(RealizedTradeValidationError, match="Symbol is required"):
        normalize_symbol("   ")


def test_normalize_currency_accepts_supported_values() -> None:
    assert normalize_currency("usd") == "USD"
    assert normalize_currency("TWD") == "TWD"


def test_normalize_currency_rejects_unsupported_values() -> None:
    with pytest.raises(RealizedTradeValidationError, match="Unsupported currency"):
        normalize_currency("EUR")


def test_service_create_rejects_buy_date_after_sell_date(monkeypatch) -> None:
    from app.services import realized_trade_service as module
    from app.services.realized_trade_service import RealizedTradeService
    from app.schemas.realized_trade import RealizedTradeCreate

    service = RealizedTradeService()
    payload = RealizedTradeCreate(
        symbol="AAPL",
        currency="USD",
        buyPrice=200,
        sellPrice=180,
        shares=1,
        buyDate=date(2026, 9, 2),
        sellDate=date(2026, 9, 1),
    )

    monkeypatch.setattr(module.realized_trade_repository, "create_trade", lambda **_: None)
    with pytest.raises(RealizedTradeValidationError, match="buyDate must be on or before sellDate"):
        service.create_trade("user-1", payload)


def test_service_update_clears_buy_date_when_explicitly_null(monkeypatch) -> None:
    from datetime import date
    from types import SimpleNamespace

    from app.services import realized_trade_service as module
    from app.services.realized_trade_service import RealizedTradeService
    from app.schemas.realized_trade import RealizedTradeUpdate

    existing = SimpleNamespace(
        symbol="AAPL",
        currency="USD",
        buy_price=Decimal("100"),
        sell_price=Decimal("120"),
        shares=Decimal("1"),
        buy_date=date(2026, 7, 1),
        sell_date=date(2026, 8, 1),
    )
    captured: dict[str, object] = {}

    def fake_get_trade(user_id: str, trade_id: int):
        return existing

    def fake_update_trade(user_id: str, trade_id: int, **kwargs):
        captured.update(kwargs)
        return existing

    monkeypatch.setattr(module.realized_trade_repository, "get_trade", fake_get_trade)
    monkeypatch.setattr(module.realized_trade_repository, "update_trade", fake_update_trade)

    service = RealizedTradeService()
    payload = RealizedTradeUpdate.model_validate({"buyDate": None})
    service.update_trade("user-1", 1, payload)

    assert captured["buy_date"] is None
