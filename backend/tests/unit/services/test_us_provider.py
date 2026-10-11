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
def test_search_stays_catalog_only_when_symbol_misses_catalog(
    monkeypatch,
    query: str,
) -> None:
    """Search must not spend Yahoo budget on keystrokes (#272)."""
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fail_yahoo(_stock_id: str):
        raise AssertionError("search must not call Yahoo chart metadata")

    monkeypatch.setattr(us_provider, "_fetch_yahoo_chart_stock", fail_yahoo)
    provider = us_provider.USProvider()
    assert provider.search(query) == []


def test_search_uses_catalog_name_match_without_yahoo(monkeypatch) -> None:
    catalog = {
        "US:NOPE": us_provider.StockRecord(
            id="US:NOPE",
            market="US",
            symbol="NOPE",
            name="Company with ibat in the name",
            name_en="Company with ibat in the name",
            exchange="NYSE",
            currency="USD",
            timezone="America/New_York",
        )
    }

    def fail_yahoo(_stock_id: str):
        raise AssertionError("search must not call Yahoo chart metadata")

    monkeypatch.setattr(
        us_provider.USProvider,
        "_stock_catalog",
        lambda self: catalog,
    )
    monkeypatch.setattr(us_provider, "_fetch_yahoo_chart_stock", fail_yahoo)
    provider = us_provider.USProvider()
    matches = provider.search("IBAT")

    assert [item.id for item in matches] == ["US:NOPE"]
    assert "ibat" in matches[0].name.lower()


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
        return _chart_payload_from_meta(
            {
                "symbol": "ITA",
                "longName": "iShares U.S. Aerospace & Defense ETF",
                "fullExchangeName": "Cboe US",
                "exchangeName": "BTS",
                "exchangeTimezoneName": "America/New_York",
                "currency": "USD",
                "instrumentType": "ETF",
                "regularMarketPrice": 91.23,
                "chartPreviousClose": 90.0,
                "regularMarketTime": 1_712_000_000,
            },
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()
    stock = provider.get_stock("US:ITA")

    assert stock is not None
    assert stock.id == "US:ITA"
    assert stock.exchange == "CBOE"


def test_get_stock_accepts_instrument_type_when_quote_type_empty(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        return _chart_payload_from_meta(
            {
                "symbol": "ITA",
                "longName": "iShares U.S. Aerospace & Defense ETF",
                "fullExchangeName": "Cboe US",
                "exchangeName": "BTS",
                "exchangeTimezoneName": "America/New_York",
                "currency": "USD",
                "quoteType": "",
                "instrumentType": "ETF",
                "regularMarketPrice": 91.23,
                "chartPreviousClose": 90.0,
                "regularMarketTime": 1_712_000_000,
            },
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    provider = us_provider.USProvider()

    assert provider.get_stock("US:ITA") is not None


def test_search_returns_catalog_name_hits_without_yahoo_live_symbol(
    monkeypatch,
) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fake_catalog() -> dict[str, us_provider.StockRecord]:
        return {
            f"US:CO{i:03d}": us_provider.StockRecord(
                id=f"US:CO{i:03d}",
                market="US",
                symbol=f"CO{i:03d}",
                name=f"Capital{i}ita industries",
                name_en=f"Capital{i}ita industries",
                exchange="NYSE",
                currency="USD",
                timezone="America/New_York",
            )
            for i in range(50)
        }

    def fail_yahoo(_stock_id: str):
        raise AssertionError("search must not call Yahoo chart metadata")

    monkeypatch.setattr(
        us_provider.USProvider,
        "_stock_catalog",
        lambda self: fake_catalog(),
    )
    monkeypatch.setattr(us_provider, "_fetch_yahoo_chart_stock", fail_yahoo)

    provider = us_provider.USProvider()
    matches = provider.search("ITA")

    assert matches
    assert all(item.id.startswith("US:CO") for item in matches)
    assert provider.get_catalog_stock("US:ITA") is None


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


def test_search_returns_prefix_catalog_matches_without_yahoo(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        us_provider,
        "_load_catalog_payload",
        _catalog_payload_with_ita_prefix_collision,
    )

    def fail_yahoo(_stock_id: str):
        raise AssertionError("search must not call Yahoo chart metadata")

    monkeypatch.setattr(us_provider, "_fetch_yahoo_chart_stock", fail_yahoo)

    provider = us_provider.USProvider()
    provider.refresh_catalog()
    matches = provider.search("ITA")

    assert [item.id for item in matches] == ["US:ITAP"]
    assert provider.get_catalog_stock("US:ITA") is None


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


@pytest.mark.parametrize(
    ("range_name", "min_candles", "expected"),
    [
        ("1D", None, ("5m", "1d")),
        ("1M", None, ("1d", "1mo")),
        ("3M", None, ("1d", "3mo")),
        ("3M", 65, ("1d", "3mo")),
        ("3M", 90, ("1d", "6mo")),
        ("3M", 125, ("1d", "1y")),  # 3M + SMA warm-up must escalate past 6mo
        ("6M", 190, ("1d", "1y")),
        ("1Y", 320, ("1d", "2y")),
    ],
)
def test_yahoo_history_window_escalates_for_sma_warmup(
    range_name: str,
    min_candles: int | None,
    expected: tuple[str, str],
) -> None:
    assert us_provider._yahoo_history_window(range_name, min_candles=min_candles) == expected


def test_get_catalog_stock_does_not_fetch_yahoo_chart(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_catalog_payload", _empty_catalog_payload)

    def fail_yahoo(_stock_id: str):
        raise AssertionError("yahoo chart metadata should not run")

    monkeypatch.setattr(us_provider, "_fetch_yahoo_chart_stock", fail_yahoo)
    provider = us_provider.USProvider()

    assert provider.get_catalog_stock("US:AAPL") is not None
    assert provider.get_catalog_stock("US:JPM") is None


def test_yahoo_v7_quotes_batch_aborts_remaining_chunks_on_auth_denied(monkeypatch) -> None:
    from app.models.stock import StockRecord
    from app.services.http_client import HttpFetchError
    from app.services.quote_batch import QuoteBatchInterrupted

    stocks = [
        StockRecord(
            id=f"US:S{i:02d}",
            market="US",
            symbol=f"S{i:02d}",
            name=f"Stock {i}",
            exchange="NYSE",
            currency="USD",
            timezone="America/New_York",
        )
        for i in range(45)  # two chunks with chunk_size=40
    ]
    calls = {"count": 0}

    def fake_load(_url, _params, _headers, *, retries=2):
        calls["count"] += 1
        raise HttpFetchError(
            "denied",
            status_code=401,
            classification="auth_denied",
        )

    monkeypatch.setattr(us_provider, "_load_json_with_headers", fake_load)
    with pytest.raises(QuoteBatchInterrupted) as exc_info:
        us_provider.yahoo_v7_quotes_batch(stocks)
    assert calls["count"] == 1
    assert exc_info.value.quotes == {}
    assert exc_info.value.error is not None
    assert exc_info.value.error.classification == "auth_denied"
    assert len(exc_info.value.deferred_ids) == len(stocks)


def test_yahoo_v7_quotes_batch_aborts_on_rate_limited(monkeypatch) -> None:
    from app.models.stock import StockRecord
    from app.services.http_client import HttpFetchError
    from app.services.quote_batch import QuoteBatchInterrupted

    stocks = [
        StockRecord(
            id=f"US:S{i:02d}",
            market="US",
            symbol=f"S{i:02d}",
            name=f"Stock {i}",
            exchange="NYSE",
            currency="USD",
            timezone="America/New_York",
        )
        for i in range(45)
    ]
    calls = {"count": 0}

    def fake_load(_url, _params, _headers, *, retries=2):
        calls["count"] += 1
        raise HttpFetchError(
            "rate limited",
            status_code=429,
            classification="rate_limited",
        )

    monkeypatch.setattr(us_provider, "_load_json_with_headers", fake_load)
    with pytest.raises(QuoteBatchInterrupted) as exc_info:
        us_provider.yahoo_v7_quotes_batch(stocks)
    assert calls["count"] == 1
    assert exc_info.value.quotes == {}
    assert exc_info.value.error is not None
    assert exc_info.value.error.classification == "rate_limited"
    assert len(exc_info.value.deferred_ids) == len(stocks)


# --- Previous close and market time (#258 follow-up) -----------------------------


def _aapl() -> "us_provider.StockRecord":
    return us_provider.StockRecord(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def test_yahoo_chart_quote_aborts_candidate_chain_on_rate_limited(monkeypatch) -> None:
    from app.services.http_client import HttpFetchError

    calls: list[str] = []

    def fake_load(url, _headers):
        calls.append(url)
        raise HttpFetchError(
            "rate limited",
            status_code=429,
            classification="rate_limited",
            retry_after_seconds=5.0,
        )

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load)
    with pytest.raises(HttpFetchError) as exc_info:
        us_provider._fetch_yahoo_chart_quote(_aapl())
    assert exc_info.value.classification == "rate_limited"
    assert len(calls) == 1
    assert "AAPL.US" not in calls[0]


def _ny_ts(month: int, day: int, hour: int = 9, minute: int = 30) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return int(datetime(2026, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")).timestamp())


def _aapl_five_day_chart(*, with_market_time: bool = True) -> dict[str, object]:
    """Recorded AAPL chart from 2026-10-07: chartPreviousClose (329.40) is Sep 29's close."""
    meta: dict[str, object] = {
        "symbol": "AAPL",
        "regularMarketPrice": 333.63,
        "chartPreviousClose": 329.4,
        "regularMarketDayHigh": 334.1,
        "regularMarketDayLow": 331.2,
        "regularMarketVolume": 41_000_000,
    }
    if with_market_time:
        meta["regularMarketTime"] = _ny_ts(10, 6, 16, 0)
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": [_ny_ts(9, 30), _ny_ts(10, 1), _ny_ts(10, 2), _ny_ts(10, 5), _ny_ts(10, 6)],
                    "indicators": {"quote": [{"close": [333.02, 330.32, 333.69, 332.89, 333.63]}]},
                }
            ],
            "error": None,
        }
    }


def test_yahoo_chart_quote_uses_the_prior_session_close(monkeypatch) -> None:
    monkeypatch.setattr(us_provider, "_load_json_get", lambda _url, _headers: _aapl_five_day_chart())

    quote = us_provider._fetch_yahoo_chart_quote(_aapl())

    assert quote is not None
    assert quote.last_price == pytest.approx(333.63)
    assert quote.previous_close == pytest.approx(332.89)  # Oct 5, not Sep 29's 329.40
    assert quote.change == pytest.approx(0.74)
    assert quote.change_percent == pytest.approx(0.22)
    assert quote.timestamp == "2026-10-06T16:00:00-04:00"


def test_yahoo_chart_quote_without_a_market_time_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        us_provider,
        "_load_json_get",
        lambda _url, _headers: _aapl_five_day_chart(with_market_time=False),
    )

    assert us_provider._fetch_yahoo_chart_quote(_aapl()) is None


def test_yahoo_v7_quote_requires_a_market_time() -> None:
    item = {"regularMarketPrice": 333.63, "regularMarketPreviousClose": 332.89}

    assert us_provider.quote_from_yahoo_v7_item(item, _aapl()) is None

    quote = us_provider.quote_from_yahoo_v7_item(
        {**item, "regularMarketTime": _ny_ts(10, 6, 16, 0)},
        _aapl(),
    )
    assert quote is not None
    assert quote.change == pytest.approx(0.74)
    assert quote.change_percent == pytest.approx(0.22)


def test_live_quote_is_unavailable_rather_than_a_change_since_open(monkeypatch) -> None:
    """Stooq has no previous close, so it is no longer a quote fallback."""
    from app.services.http_client import HttpFetchError

    def fail(*_args, **_kwargs):
        raise HttpFetchError("blocked", classification="network_failure")

    monkeypatch.setattr(us_provider, "_load_json_get", fail)
    monkeypatch.setattr(us_provider, "fetch_json", fail)

    assert us_provider._fetch_live_quote(_aapl()) is None
    assert not hasattr(us_provider, "_fetch_stooq_quote")


def test_sub_cent_previous_close_is_not_a_quote_rather_than_an_error(monkeypatch) -> None:
    chart = _aapl_five_day_chart()
    result = chart["chart"]["result"][0]  # type: ignore[index]
    result["meta"]["regularMarketPrice"] = 0.01  # type: ignore[index]
    result["indicators"]["quote"][0]["close"] = [0.003, 0.003, 0.003, 0.004, 0.01]  # type: ignore[index]
    monkeypatch.setattr(us_provider, "_load_json_get", lambda _url, _headers: chart)

    assert us_provider._fetch_yahoo_chart_quote(_aapl()) is None
