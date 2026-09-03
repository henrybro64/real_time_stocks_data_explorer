from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import realized_trade_service as realized_trade_module
from app.services.realized_trade_service import (
    RealizedTradeService,
    RealizedTradeValidationError,
    calculate_realized_metrics,
    normalize_currency,
    normalize_symbol,
)
from app.schemas.realized_trade import RealizedTradeCreate, RealizedTradeUpdate


def _existing_trade(**overrides: object) -> SimpleNamespace:
    base = {
        "symbol": "AAPL",
        "currency": "USD",
        "buy_price": Decimal("100"),
        "sell_price": Decimal("120"),
        "shares": Decimal("1"),
        "cumulative_dividends": Decimal("0"),
        "buy_date": date(2026, 7, 1),
        "sell_date": date(2026, 8, 1),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _install_update_capture(
    monkeypatch: pytest.MonkeyPatch,
    existing: SimpleNamespace,
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_update_trade(user_id: str, trade_id: int, updates: dict[str, object]):
        captured.update(updates)
        for field, value in updates.items():
            setattr(existing, field, value)
        return existing

    monkeypatch.setattr(
        realized_trade_module.realized_trade_repository,
        "update_trade",
        fake_update_trade,
    )
    return captured


@pytest.mark.parametrize(
    (
        "buy_price",
        "sell_price",
        "shares",
        "dividends",
        "expected_price_gain",
        "expected_gain",
        "expected_return",
    ),
    [
        (
            Decimal("920"),
            Decimal("1020"),
            Decimal("1000"),
            Decimal("0"),
            100_000.0,
            100_000.0,
            10.8696,
        ),
        (
            Decimal("800"),
            Decimal("900"),
            Decimal("1000"),
            Decimal("18000"),
            100_000.0,
            118_000.0,
            14.75,
        ),
        (
            Decimal("100"),
            Decimal("95"),
            Decimal("100"),
            Decimal("200"),
            -500.0,
            -300.0,
            -3.0,
        ),
        (
            Decimal("100"),
            Decimal("95"),
            Decimal("100"),
            Decimal("600"),
            -500.0,
            100.0,
            1.0,
        ),
        (
            Decimal("200"),
            Decimal("150"),
            Decimal("10"),
            Decimal("0"),
            -500.0,
            -500.0,
            -25.0,
        ),
        (
            Decimal("180.25"),
            Decimal("195.50"),
            Decimal("10.5"),
            Decimal("1.25"),
            160.13,
            161.38,
            8.5265,
        ),
        (
            Decimal("100"),
            Decimal("100"),
            Decimal("3"),
            Decimal("0"),
            0.0,
            0.0,
            0.0,
        ),
    ],
)
def test_calculate_realized_metrics(
    buy_price: Decimal,
    sell_price: Decimal,
    shares: Decimal,
    dividends: Decimal,
    expected_price_gain: float,
    expected_gain: float,
    expected_return: float,
) -> None:
    metrics = calculate_realized_metrics(buy_price, sell_price, shares, dividends)
    assert metrics.price_gain == expected_price_gain
    assert metrics.realized_gain == expected_gain
    assert metrics.realized_return_percent == expected_return


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("   ", "Symbol is required"),
        ("A" * 33, "at most 32 characters"),
    ],
)
def test_normalize_symbol_rejects_invalid_values(value: str, match: str) -> None:
    with pytest.raises(RealizedTradeValidationError, match=match):
        normalize_symbol(value)


def test_normalize_symbol_trims_and_uppercases() -> None:
    assert normalize_symbol("  aapl  ") == "AAPL"


@pytest.mark.parametrize("currency", ["usd", "TWD"])
def test_normalize_currency_accepts_supported_values(currency: str) -> None:
    assert normalize_currency(currency) == currency.upper()


def test_normalize_currency_rejects_unsupported_values() -> None:
    with pytest.raises(RealizedTradeValidationError, match="Unsupported currency"):
        normalize_currency("EUR")


@pytest.mark.parametrize(
    ("payload_kwargs", "match"),
    [
        (
            {
                "buyPrice": 200,
                "sellPrice": 180,
                "buyDate": date(2026, 9, 2),
                "sellDate": date(2026, 9, 1),
            },
            "buyDate must be on or before sellDate",
        ),
        (
            {"buyPrice": 0.0000001},
            "buyPrice must be greater than 0",
        ),
        (
            {"cumulativeDividends": -1},
            "greater than or equal to 0",
        ),
    ],
)
def test_service_create_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
    payload_kwargs: dict[str, object],
    match: str,
) -> None:
    service = RealizedTradeService()
    try:
        payload = RealizedTradeCreate.model_validate(
            {
                "symbol": "AAPL",
                "currency": "USD",
                "buyPrice": 100,
                "sellPrice": 120,
                "shares": 1,
                "sellDate": date(2026, 8, 1),
                **payload_kwargs,
            },
        )
    except Exception as exc:
        assert "greater than or equal to 0" in str(exc)
        return
    monkeypatch.setattr(realized_trade_module.realized_trade_repository, "create_trade", lambda **_: None)
    with pytest.raises(RealizedTradeValidationError, match=match):
        service.create_trade("user-1", payload)


def test_service_rejects_oversized_cumulative_dividends_as_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Huge finite dividends must raise RealizedTradeValidationError (API 400), not 500."""
    service = RealizedTradeService()
    payload = RealizedTradeCreate.model_validate(
        {
            "symbol": "AAPL",
            "currency": "USD",
            "buyPrice": 100,
            "sellPrice": 120,
            "shares": 1,
            "sellDate": date(2026, 8, 1),
            "cumulativeDividends": 1e22,
        },
    )
    monkeypatch.setattr(
        realized_trade_module.realized_trade_repository,
        "create_trade",
        lambda **_: None,
    )
    with pytest.raises(RealizedTradeValidationError, match="exceeds allowed precision"):
        service.create_trade("user-1", payload)

def test_service_create_defaults_cumulative_dividends_to_zero(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_trade(**kwargs):
        captured.update(kwargs)
        return _existing_trade()

    monkeypatch.setattr(
        realized_trade_module.realized_trade_repository,
        "create_trade",
        fake_create_trade,
    )
    service = RealizedTradeService()
    service.create_trade(
        "user-1",
        RealizedTradeCreate.model_validate(
            {
                "symbol": "AAPL",
                "currency": "USD",
                "buyPrice": 100,
                "sellPrice": 120,
                "shares": 1,
                "sellDate": date(2026, 8, 1),
            },
        ),
    )
    assert captured["cumulative_dividends"] == Decimal("0")


def test_service_update_clears_buy_date_when_explicitly_null(monkeypatch) -> None:
    existing = _existing_trade()
    captured = _install_update_capture(monkeypatch, existing)
    service = RealizedTradeService()
    service.update_trade("user-1", 1, RealizedTradeUpdate.model_validate({"buyDate": None}))
    assert captured == {"buy_date": None}


def test_service_update_passes_only_changed_fields(monkeypatch) -> None:
    existing = _existing_trade(buy_date=None)
    captured = _install_update_capture(monkeypatch, existing)
    service = RealizedTradeService()
    service.update_trade("user-1", 1, RealizedTradeUpdate(sellPrice=150))
    assert captured == {"sell_price": Decimal("150")}


def test_service_update_recalculates_when_only_dividends_change(monkeypatch) -> None:
    existing = _existing_trade(
        buy_price=Decimal("800"),
        sell_price=Decimal("900"),
        shares=Decimal("1000"),
        cumulative_dividends=Decimal("0"),
    )
    captured = _install_update_capture(monkeypatch, existing)
    service = RealizedTradeService()
    updated = service.update_trade(
        "user-1",
        1,
        RealizedTradeUpdate(cumulativeDividends=18000),
    )
    assert captured == {"cumulative_dividends": Decimal("18000")}
    assert updated.cumulative_dividends == Decimal("18000")
    metrics = calculate_realized_metrics(
        updated.buy_price,
        updated.sell_price,
        updated.shares,
        updated.cumulative_dividends,
    )
    assert metrics.price_gain == 100_000.0
    assert metrics.realized_gain == 118_000.0
    assert metrics.realized_return_percent == 14.75
