import pytest

from app.services.providers import us_provider


@pytest.fixture(autouse=True)
def _reset_catalog_cache(monkeypatch) -> None:
    monkeypatch.setattr(us_provider.USProvider, "_catalog_cache", None)
    monkeypatch.setattr(us_provider.USProvider, "_catalog_cached_at", None)


def _empty_catalog_payload() -> dict[str, object]:
    return {
        "fields": ["ticker", "name", "exchange"],
        "data": [],
    }


def _chart_payload(symbol: str, *, name: str, exchange: str) -> dict[str, object]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "longName": name,
                        "fullExchangeName": exchange,
                        "exchangeTimezoneName": "America/New_York",
                        "currency": "USD",
                        "regularMarketPrice": 91.23,
                    }
                }
            ],
            "error": None,
        }
    }


@pytest.mark.parametrize(
    ("stock_id", "name"),
    [
        ("US:IBAT", "iShares Energy Storage & Materials ETF"),
        ("US:ITA", "iShares U.S. Aerospace & Defense ETF"),
        ("US:VPU", "Vanguard Utilities ETF"),
        ("US:XLE", "Energy Select Sector SPDR Fund"),
    ],
)
def test_get_stock_fetches_us_etf_from_yahoo_when_catalog_misses_symbol(
    monkeypatch,
    stock_id: str,
    name: str,
) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)
    yahoo_calls = {"count": 0}
    symbol = stock_id.split(":", 1)[1]

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        yahoo_calls["count"] += 1
        assert f"/{symbol}?" in url
        return _chart_payload(
            symbol,
            name=name,
            exchange="NYSEArca",
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()

    stock = provider.get_stock(stock_id)
    assert stock is not None
    assert stock.id == stock_id
    assert stock.symbol == symbol
    assert stock.name == name
    assert stock.name_en == name
    assert stock.exchange == "AMEX"
    assert stock.currency == "USD"
    assert stock.timezone == "America/New_York"

    cached = provider.get_stock(stock_id)
    assert cached == stock
    assert yahoo_calls["count"] == 1


def test_search_resolves_prefixed_us_symbol_when_catalog_misses_it(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        assert "/IBAT?" in url
        return _chart_payload(
            "IBAT",
            name="iShares Energy Storage & Materials ETF",
            exchange="NYSEArca",
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    matches = provider.search("US:IBAT")

    assert [item.id for item in matches] == ["US:IBAT"]
    assert matches[0].symbol == "IBAT"
    assert matches[0].name == "iShares Energy Storage & Materials ETF"
