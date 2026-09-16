"""HTTP-boundary tests for tw-profitability quarters query (#230).

Direct Python calls pass int literals and cannot detect FastAPI query-string
Literal[int] validation failures. These tests exercise real query parsing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes import tw_profitability as profitability_route
from app.main import app
from app.services.tw_profitability_service import (
    TaiwanProfitabilityPoint,
    TaiwanProfitabilityResult,
)

client = TestClient(app)


def _sample_result(*, quarters: int = 12) -> TaiwanProfitabilityResult:
    point = TaiwanProfitabilityPoint(
        fiscal_quarter="2025Q3",
        period_end="2025-09-30",
        gross_margin_percent=54.12,
        operating_margin_percent=42.3,
        pretax_margin_percent=43.1,
        net_margin_percent=38.2,
        quarter_end_close=None,
    )
    return TaiwanProfitabilityResult(
        stock_id="TW:2330",
        quarters=quarters,
        as_of="2025-09-30",
        source="yahoo_finance_quarterly_income",
        stale=False,
        points=(point,),
    )


@pytest.mark.parametrize("quarters", [8, 12, 20])
@pytest.mark.parametrize(
    "stock_path",
    [
        "TW:2330",
        "TW%3A2330",  # URL-encoded stock id must keep routing (#230)
    ],
)
def test_http_valid_quarters_query_strings_reach_service_as_int(
    monkeypatch: pytest.MonkeyPatch,
    quarters: int,
    stock_path: str,
) -> None:
    seen: dict[str, object] = {}

    def fake_get(stock_id: str, *, quarters: int = 12) -> TaiwanProfitabilityResult:
        seen["stock_id"] = stock_id
        seen["quarters"] = quarters
        seen["quarters_type"] = type(quarters)
        return _sample_result(quarters=quarters)

    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        fake_get,
    )

    response = client.get(f"/api/stocks/{stock_path}/tw-profitability?quarters={quarters}")

    assert response.status_code == 200
    assert response.json()["quarters"] == quarters
    assert seen["quarters"] == quarters
    assert seen["quarters_type"] is int
    assert seen["stock_id"] == "TW:2330"


def test_http_omitted_quarters_defaults_to_12(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_get(stock_id: str, *, quarters: int = 12) -> TaiwanProfitabilityResult:
        seen["stock_id"] = stock_id
        seen["quarters"] = quarters
        return _sample_result(quarters=quarters)

    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        fake_get,
    )

    response = client.get("/api/stocks/TW%3A2330/tw-profitability")

    assert response.status_code == 200
    assert seen["quarters"] == 12
    assert seen["stock_id"] == "TW:2330"
    assert response.json()["quarters"] == 12
    assert response.json()["stockId"] == "TW:2330"


def test_http_unsupported_quarters_returns_400_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_get(stock_id: str, *, quarters: int = 12) -> TaiwanProfitabilityResult:
        calls["n"] += 1
        return _sample_result(quarters=quarters)

    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        fake_get,
    )

    response = client.get("/api/stocks/TW:2330/tw-profitability?quarters=10")

    assert response.status_code == 400
    assert response.json()["detail"] == "quarters must be 8, 12, or 20"
    assert calls["n"] == 0


def test_http_malformed_quarters_returns_422_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_get(stock_id: str, *, quarters: int = 12) -> TaiwanProfitabilityResult:
        calls["n"] += 1
        return _sample_result(quarters=quarters)

    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        fake_get,
    )

    response = client.get("/api/stocks/TW:2330/tw-profitability?quarters=abc")

    assert response.status_code == 422
    assert calls["n"] == 0


def test_http_valid_quarters_does_not_return_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        lambda stock_id, quarters=12: _sample_result(quarters=quarters),
    )

    response = client.get("/api/stocks/TW%3A2330/tw-profitability?quarters=12")

    assert response.status_code != 422
    assert response.status_code == 200
