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


def _chart_payload(
    symbol: str,
    *,
    name: str,
    exchange: str,
    quote_type: str = "ETF",
    currency: str = "USD",
    last_price: float = 91.23,
    previous_close: float = 90.0,
) -> dict[str, object]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "longName": name,
                        "fullExchangeName": exchange,
                        "exchangeTimezoneName": "America/New_York",
                        "currency": currency,
                        "quoteType": quote_type,
                        "regularMarketPrice": last_price,
                        "chartPreviousClose": previous_close,
                        "regularMarketTime": 1_712_000_000,
                    }
                }
            ],
            "error": None,
        }
    }


def _chart_payload_from_meta(meta: dict[str, object]) -> dict[str, object]:
    return {"chart": {"result": [{"meta": meta}], "error": None}}


def _catalog_payload_with_ibat_name_collision() -> dict[str, object]:
    return {
        "fields": ["ticker", "name", "exchange"],
        "data": [
            ["NOPE", "Company with ibat in the name", "NYSE"],
        ],
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


@pytest.mark.parametrize("query", ["IBAT", "US:IBAT"])
def test_search_resolves_us_etf_symbol_when_catalog_misses_it(
    monkeypatch,
    query: str,
) -> None:
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
    matches = provider.search(query)

    assert [item.id for item in matches] == ["US:IBAT"]
    assert matches[0].symbol == "IBAT"
    assert matches[0].name == "iShares Energy Storage & Materials ETF"


def test_search_resolves_ibat_when_unrelated_name_match_exists(monkeypatch) -> None:
    monkeypatch.setattr(
        us_provider,
        "_load_catalog_payload",
        _catalog_payload_with_ibat_name_collision,
    )

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
    matches = provider.search("IBAT")

    assert matches[0].id == "US:IBAT"
    assert matches[0].symbol == "IBAT"
    assert any(item.id == "US:NOPE" for item in matches)


def test_get_live_quote_works_after_dynamic_stock_lookup(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        assert "/IBAT?" in url
        return _chart_payload(
            "IBAT",
            name="iShares Energy Storage & Materials ETF",
            exchange="NYSEArca",
            last_price=42.5,
            previous_close=41.0,
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    stock = provider.get_stock("US:IBAT")
    quote = provider.get_live_quote("US:IBAT")

    assert stock is not None
    assert quote is not None
    assert quote.id == "US:IBAT"
    assert quote.last_price == 42.5
    assert quote.change == 1.5
    assert quote.previous_close == 41.0


def test_get_stock_rejects_crypto_pair_symbols_without_yahoo_call(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fail_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del url, headers
        raise AssertionError("Yahoo should not be called for crypto pair symbols")

    monkeypatch.setattr(us_provider, "_load_json_get", fail_load_json_get)

    provider = us_provider.USProvider()

    assert provider.get_stock("US:BTC-USD") is None
    assert provider.search("BTC-USD") == []


def test_get_stock_rejects_yahoo_cryptocurrency_metadata(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        return _chart_payload(
            "BTC-USD",
            name="Bitcoin USD",
            exchange="CCC",
            quote_type="CRYPTOCURRENCY",
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()

    assert provider.get_stock("US:BTCUSD") is None


def test_get_stock_accepts_cboe_listed_etf_metadata(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        assert "/ITA?" in url
        return _chart_payload(
            "ITA",
            name="iShares U.S. Aerospace & Defense ETF",
            exchange="Cboe US",
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    stock = provider.get_stock("US:ITA")

    assert stock is not None
    assert stock.id == "US:ITA"
    assert stock.exchange == "CBOE"


def test_get_stock_accepts_cboe_us_venue_via_bts_exchange_code(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        return _chart_payload_from_meta(
            {
                "symbol": "ITA",
                "longName": "iShares U.S. Aerospace & Defense ETF",
                "exchangeName": "BTS",
                "exchangeTimezoneName": "America/New_York",
                "currency": "USD",
                "quoteType": "ETF",
                "regularMarketPrice": 91.23,
                "chartPreviousClose": 90.0,
                "regularMarketTime": 1_712_000_000,
            },
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    stock = provider.get_stock("US:ITA")

    assert stock is not None
    assert stock.exchange == "CBOE"


def test_get_stock_rejects_cboe_ca_and_non_usd_metadata(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        return _chart_payload_from_meta(
            {
                "symbol": "META.NE",
                "longName": "Meta Platforms Inc.",
                "fullExchangeName": "Cboe CA",
                "exchangeName": "NEO",
                "exchangeTimezoneName": "America/Toronto",
                "currency": "CAD",
                "regularMarketPrice": 91.23,
                "chartPreviousClose": 90.0,
                "regularMarketTime": 1_712_000_000,
            },
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()

    assert provider.get_stock("US:META.NE") is None
    assert provider.search("META.NE") == []


def _catalog_payload_with_ita_prefix_collision() -> dict[str, object]:
    return {
        "fields": ["ticker", "name", "exchange"],
        "data": [
            ["ITAP", "Some ITAP Company", "NASDAQ"],
        ],
    }


def test_search_resolves_ita_when_prefix_matches_exist_without_exact_symbol(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        us_provider,
        "_load_catalog_payload",
        _catalog_payload_with_ita_prefix_collision,
    )

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        assert "/ITA?" in url
        return _chart_payload(
            "ITA",
            name="iShares U.S. Aerospace & Defense ETF",
            exchange="Cboe US",
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    matches = provider.search("ITA")

    assert matches[0].id == "US:ITA"
    assert matches[0].symbol == "ITA"
    assert any(item.id == "US:ITAP" for item in matches)


def test_get_stock_accepts_class_share_symbols_with_hyphen(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        assert "/BRK-B?" in url
        return _chart_payload(
            "BRK-B",
            name="Berkshire Hathaway Inc.",
            exchange="NYSE",
            quote_type="EQUITY",
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    stock = provider.get_stock("US:BRK-B")

    assert stock is not None
    assert stock.id == "US:BRK-B"
    assert stock.symbol == "BRK-B"
    assert stock.exchange == "NYSE"
