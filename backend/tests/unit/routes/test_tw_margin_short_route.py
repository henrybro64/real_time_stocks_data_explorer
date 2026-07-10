from fastapi import HTTPException
import pytest

from app.api.routes import tw_margin_short as margin_short_route
from tests.support import sample_margin_short_result


def test_tw_margin_short_schema_preserves_lot_and_amount_fields() -> None:
    payload = margin_short_route._tw_margin_short_schema(sample_margin_short_result()).model_dump()

    assert payload["stockId"] == "TW:1303"
    assert payload["symbol"] == "1303"
    assert payload["market"] == "TWSE"
    assert payload["latestDate"] == "2026-07-09"
    assert payload["summary"]["marginBalanceLots"] == pytest.approx(31_339.0)
    assert payload["summary"]["shortMarginBalanceLots"] == pytest.approx(0.0)
    assert payload["summary"]["borrowedShortBalanceLots"] == pytest.approx(12_345.0)
    assert payload["summary"]["borrowedShortBalanceChangeLots"] == pytest.approx(141.0)
    assert payload["summary"]["borrowedShortBalanceChangeAmount10k"] == 2609
    assert payload["history"][0]["closePrice"] == pytest.approx(185.0)


def test_margin_short_route_delegates_to_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(stock_id: str, *, days: int):
        captured["stock_id"] = stock_id
        captured["days"] = days
        return sample_margin_short_result()

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        fake_get,
    )

    response = margin_short_route.get_tw_margin_short("TW:1303", days=30)

    assert captured == {"stock_id": "TW:1303", "days": 30}
    assert response.latestDate == "2026-07-09"
    assert response.summary is not None
    assert response.summary.borrowedShortBalanceChangeAmount10k == 2609


def test_margin_short_route_maps_not_found_errors(monkeypatch) -> None:
    def raise_not_found(*args, **kwargs):
        raise ValueError("Stock not found")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_not_found,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:9999", days=30)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Stock not found"
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_upstream_errors(monkeypatch) -> None:
    def raise_upstream(*args, **kwargs):
        raise RuntimeError("Taiwan margin-short data is temporarily unavailable from official sources.")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_upstream,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:1303", days=30)
    except HTTPException as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_unknown_market_as_bad_request(monkeypatch) -> None:
    def raise_unknown(*args, **kwargs):
        raise ValueError("Taiwan margin-short market is unavailable for this stock")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_unknown,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:1303", days=30)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_non_tw_stock_as_not_found(monkeypatch) -> None:
    def raise_non_tw(*args, **kwargs):
        raise ValueError("Taiwan margin-short data is only available for TW stocks")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_non_tw,
    )

    try:
        margin_short_route.get_tw_margin_short("US:AAPL", days=30)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert "only available for TW stocks" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")
