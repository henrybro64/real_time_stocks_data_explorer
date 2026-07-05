import pytest

from app.services import key_stats_service
from app.services.quote_service import quote_service
from tests.support import quote_record, stock_record


def _patch_common(monkeypatch, stock, quote, *, finnhub_enrich=None) -> None:
    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: stock)
    monkeypatch.setattr(quote_service, "get_quote", lambda stock_id: quote)
    monkeypatch.setattr(key_stats_service, "_us_from_yahoo", lambda current_stock, base: dict(base))
    monkeypatch.setattr(key_stats_service, "_tw_from_candles", lambda current_stock, base: dict(base))
    monkeypatch.setattr(
        key_stats_service,
        "_finnhub_enrich",
        finnhub_enrich or (lambda payload, current_stock: dict(payload)),
    )


def test_get_key_statistics_uses_twse_official_daily_pe(monkeypatch) -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    quote = quote_record(
        "TW:2330",
        last_price=945.0,
        change=5.0,
        change_percent=0.53,
        previous_close=940.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=942.0,
        high=948.0,
        low=938.0,
        volume=123_456,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_fetch_twse_daily_pe_snapshot",
        lambda target_day: (
            {"2330": {"peRatio": 32.87, "peStatus": None, "priceUsed": 2445.0}},
            "2026-07-03",
        ),
    )

    payload = key_stats_service.get_key_statistics("TW:2330")

    assert payload is not None
    assert payload["peRatio"] == 32.87
    assert payload["peTtm"] == 32.87
    assert payload["peRatioType"] == "official_daily"
    assert payload["peRatioSource"] == "TWSE official daily P/E"
    assert payload["priceUsed"] == 2445.0
    assert payload["priceAsOf"] == "2026-07-03"
    assert payload["valuationAsOf"] == "2026-07-03"
    assert payload["valuationWarning"] is None


def test_get_key_statistics_uses_tpex_official_daily_pe(monkeypatch) -> None:
    stock = stock_record(
        "TW:3293",
        market="TW",
        symbol="3293",
        name="鈊象",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    quote = quote_record(
        "TW:3293",
        last_price=796.0,
        change=3.0,
        change_percent=0.38,
        previous_close=793.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=790.0,
        high=800.0,
        low=788.0,
        volume=20_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_fetch_tpex_daily_pe_snapshot",
        lambda target_day: (
            {"3293": {"peRatio": 19.74, "peStatus": None, "priceUsed": None}},
            "2026-07-03",
        ),
    )

    payload = key_stats_service.get_key_statistics("TW:3293")

    assert payload is not None
    assert payload["peRatio"] == 19.74
    assert payload["peTtm"] == 19.74
    assert payload["peRatioType"] == "official_daily"
    assert payload["peRatioSource"] == "TPEx official daily P/E"
    assert payload["priceUsed"] is None
    assert payload["valuationAsOf"] == "2026-07-03"
    assert payload["valuationWarning"] is None


def test_get_key_statistics_uses_provider_supplied_us_trailing_pe(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=210.5,
        change=1.5,
        change_percent=0.72,
        previous_close=209.0,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=208.5,
        high=211.2,
        low=207.9,
        volume=75_000_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_load_yfinance_info",
        lambda current_stock: {
            "quoteType": "EQUITY",
            "trailingPE": 29.4,
            "trailingEps": 7.15,
        },
    )

    payload = key_stats_service.get_key_statistics("US:AAPL")

    assert payload is not None
    assert payload["peRatio"] == 29.4
    assert payload["peTtm"] == 29.4
    assert payload["peRatioType"] == "provider_ttm"
    assert payload["peRatioSource"] == "Yahoo Finance trailing P/E"
    assert payload["epsTtm"] == 7.15
    assert payload["valuationAsOf"] == "2026-07-03"
    assert payload["valuationWarning"] is None


def test_get_key_statistics_calculates_us_pe_from_latest_price_and_eps(monkeypatch) -> None:
    stock = stock_record(
        "US:MSFT",
        market="US",
        symbol="MSFT",
        name="Microsoft Corporation",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:MSFT",
        last_price=125.0,
        change=2.0,
        change_percent=1.63,
        previous_close=123.0,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=122.5,
        high=126.0,
        low=121.8,
        volume=30_000_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_load_yfinance_info",
        lambda current_stock: {
            "quoteType": "EQUITY",
            "trailingEps": 5.0,
        },
    )

    payload = key_stats_service.get_key_statistics("US:MSFT")

    assert payload is not None
    assert payload["peRatio"] == 25.0
    assert payload["peTtm"] == 25.0
    assert payload["peRatioType"] == "calculated_ttm"
    assert payload["peRatioSource"] == "Calculated from latest price and EPS TTM"
    assert payload["priceUsed"] == 125.0
    assert payload["priceAsOf"] == "2026-07-03"
    assert payload["epsTtm"] == 5.0
    assert payload["valuationWarning"] is None


def test_get_key_statistics_returns_warning_for_bad_us_eps(monkeypatch) -> None:
    stock = stock_record(
        "US:TSLA",
        market="US",
        symbol="TSLA",
        name="Tesla, Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:TSLA",
        last_price=180.0,
        change=-1.0,
        change_percent=-0.55,
        previous_close=181.0,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=182.0,
        high=183.5,
        low=179.1,
        volume=40_000_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_load_yfinance_info",
        lambda current_stock: {
            "quoteType": "EQUITY",
            "trailingEps": 0,
        },
    )

    payload = key_stats_service.get_key_statistics("US:TSLA")

    assert payload is not None
    assert payload["peRatio"] is None
    assert payload["peTtm"] is None
    assert payload["valuationWarning"] == "P/E could not be calculated because EPS TTM was missing or non-positive."


def test_get_key_statistics_rejects_etf_valuation(monkeypatch) -> None:
    stock = stock_record(
        "US:VTI",
        market="US",
        symbol="VTI",
        name="Vanguard Total Stock Market ETF",
        exchange="NYSEARCA",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:VTI",
        last_price=300.0,
        change=0.5,
        change_percent=0.17,
        previous_close=299.5,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=299.0,
        high=301.0,
        low=298.4,
        volume=5_000_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_load_yfinance_info",
        lambda current_stock: {
            "quoteType": "ETF",
            "trailingPE": 18.0,
            "trailingEps": 12.0,
        },
    )

    payload = key_stats_service.get_key_statistics("US:VTI")

    assert payload is not None
    assert payload["peRatio"] is None
    assert payload["peTtm"] is None
    assert payload["valuationWarning"] == "P/E is not reported for ETF or fund instruments."


def test_get_key_statistics_reports_malformed_official_pe(monkeypatch) -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    quote = quote_record(
        "TW:2330",
        last_price=945.0,
        change=5.0,
        change_percent=0.53,
        previous_close=940.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=942.0,
        high=948.0,
        low=938.0,
        volume=123_456,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_fetch_twse_daily_pe_snapshot",
        lambda target_day: (
            {"2330": {"peRatio": None, "peStatus": "malformed", "priceUsed": None}},
            "2026-07-03",
        ),
    )

    payload = key_stats_service.get_key_statistics("TW:2330")

    assert payload is not None
    assert payload["peRatio"] is None
    assert payload["peTtm"] is None
    assert payload["valuationAsOf"] == "2026-07-03"
    assert payload["valuationWarning"] == "TWSE official daily P/E returned a malformed P/E value."


def test_get_key_statistics_uses_finnhub_provider_trailing_pe(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=210.5,
        change=1.5,
        change_percent=0.72,
        previous_close=209.0,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=208.5,
        high=211.2,
        low=207.9,
        volume=75_000_000,
    )

    def finnhub_enrich(payload, current_stock):
        enriched = dict(payload)
        enriched["peTtm"] = 31.5
        enriched["epsTtm"] = 6.8
        return enriched

    def yfinance_should_not_load(_current_stock):
        raise AssertionError("yfinance should not load when Finnhub P/E is available")

    _patch_common(monkeypatch, stock, quote, finnhub_enrich=finnhub_enrich)
    monkeypatch.setattr(key_stats_service, "_load_yfinance_info", yfinance_should_not_load)

    payload = key_stats_service.get_key_statistics("US:AAPL")

    assert payload is not None
    assert payload["peRatio"] == 31.5
    assert payload["peTtm"] == 31.5
    assert payload["peRatioType"] == "provider_ttm"
    assert payload["peRatioSource"] == "Finnhub stock metric"
    assert payload["epsTtm"] == 6.8
    assert payload["valuationWarning"] is None


def test_get_key_statistics_returns_warning_for_negative_us_eps(monkeypatch) -> None:
    stock = stock_record(
        "US:XYZ",
        market="US",
        symbol="XYZ",
        name="Example Corp.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:XYZ",
        last_price=12.0,
        change=-0.5,
        change_percent=-4.0,
        previous_close=12.5,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=12.4,
        high=12.6,
        low=11.8,
        volume=1_000_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_load_yfinance_info",
        lambda current_stock: {
            "quoteType": "EQUITY",
            "trailingEps": -2.5,
        },
    )

    payload = key_stats_service.get_key_statistics("US:XYZ")

    assert payload is not None
    assert payload["peRatio"] is None
    assert payload["peTtm"] is None
    assert payload["epsTtm"] == -2.5
    assert payload["valuationWarning"] == "P/E could not be calculated because EPS TTM was missing or non-positive."


def test_get_key_statistics_rejects_tw_etf_valuation(monkeypatch) -> None:
    stock = stock_record(
        "TW:0050",
        market="TW",
        symbol="0050",
        name="元大台灣50",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    quote = quote_record(
        "TW:0050",
        last_price=180.0,
        change=1.0,
        change_percent=0.56,
        previous_close=179.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=179.5,
        high=181.0,
        low=178.5,
        volume=50_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_fetch_twse_daily_pe_snapshot",
        lambda target_day: (
            {"0050": {"peRatio": None, "peStatus": "missing", "priceUsed": None}},
            "2026-07-03",
        ),
    )

    payload = key_stats_service.get_key_statistics("TW:0050")

    assert payload is not None
    assert payload["peRatio"] is None
    assert payload["peTtm"] is None
    assert payload["valuationAsOf"] == "2026-07-03"
    assert payload["valuationWarning"] == "P/E is not reported for ETF or fund instruments."


@pytest.mark.parametrize(
    ("raw_value", "expected_pe", "expected_status"),
    [
        ("18.42", 18.42, None),
        ("1,234.56", 1234.56, None),
        ("--", None, "missing"),
        ("N/A", None, "missing"),
        ("", None, "missing"),
        ("not-a-number", None, "malformed"),
    ],
)
def test_parse_official_pe_value_handles_localized_numeric_fields(
    raw_value: str,
    expected_pe: float | None,
    expected_status: str | None,
) -> None:
    pe_ratio, pe_status = key_stats_service._parse_official_pe_value(raw_value)

    assert pe_ratio == expected_pe
    assert pe_status == expected_status


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1,234.50", 1234.5),
        ("--", None),
        ("N/A", None),
    ],
)
def test_parse_number_handles_official_field_markers(raw_value: str, expected: float | None) -> None:
    assert key_stats_service._parse_number(raw_value) == expected


def test_get_key_statistics_ignores_non_finite_yfinance_values(monkeypatch) -> None:
    stock = stock_record(
        "US:XYZ",
        market="US",
        symbol="XYZ",
        name="Example Corp.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = quote_record(
        "US:XYZ",
        last_price=12.0,
        change=-0.5,
        change_percent=-4.0,
        previous_close=12.5,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=12.4,
        high=12.6,
        low=11.8,
        volume=1_000_000,
    )
    _patch_common(monkeypatch, stock, quote)
    monkeypatch.setattr(
        key_stats_service,
        "_load_yfinance_info",
        lambda current_stock: {
            "quoteType": "EQUITY",
            "trailingPE": float("nan"),
            "trailingEps": float("nan"),
        },
    )

    payload = key_stats_service.get_key_statistics("US:XYZ")

    assert payload is not None
    assert payload["peRatio"] is None
    assert payload["peTtm"] is None
    assert payload["epsTtm"] is None
    assert payload["valuationWarning"] == "P/E could not be calculated because EPS TTM was missing or non-positive."


@pytest.mark.parametrize("raw_value", [float("nan"), float("inf"), float("-inf")])
def test_parse_number_rejects_non_finite_numeric_values(raw_value: float) -> None:
    assert key_stats_service._parse_number(raw_value) is None


@pytest.fixture
def _clear_yfinance_info_cache():
    key_stats_service._yfinance_info_cache.clear()
    yield
    key_stats_service._yfinance_info_cache.clear()


def test_load_yfinance_info_uses_ttl_cache_and_refetches_after_expiry(
    monkeypatch,
    _clear_yfinance_info_cache,
) -> None:
    stock = stock_record(
        "US:AAA",
        market="US",
        symbol="AAA",
        name="Alpha Corp.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    call_count = {"count": 0}
    current_time = {"value": 1_000_000.0}

    def fake_fetch(current_stock):  # noqa: ARG001
        call_count["count"] += 1
        return {"quoteType": "EQUITY", "trailingPE": call_count["count"]}

    monkeypatch.setattr(key_stats_service, "_fetch_yfinance_info_uncached", fake_fetch)
    monkeypatch.setattr(key_stats_service.time, "time", lambda: current_time["value"])

    first = key_stats_service._load_yfinance_info(stock)
    second = key_stats_service._load_yfinance_info(stock)
    current_time["value"] += key_stats_service._YFINANCE_INFO_TTL_SECONDS + 1
    third = key_stats_service._load_yfinance_info(stock)

    assert first == {"quoteType": "EQUITY", "trailingPE": 1}
    assert second == first
    assert third == {"quoteType": "EQUITY", "trailingPE": 2}
    assert call_count["count"] == 2


def test_preload_yfinance_info_batches_us_symbols(monkeypatch, _clear_yfinance_info_cache) -> None:
    stocks = [
        stock_record(
            f"US:{symbol}",
            market="US",
            symbol=symbol,
            name=f"{symbol} Inc.",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        )
        for symbol in ("AAA", "BBB", "CCC")
    ]
    batch_calls: list[str] = []

    class FakeTickers:
        def __init__(self, symbols: str) -> None:
            batch_calls.append(symbols)
            self.tickers = {
                symbol: type("Ticker", (), {"info": {"quoteType": "EQUITY", "trailingPE": 10.0}})()
                for symbol in symbols.split()
            }

    fake_yf = type("yf", (), {"Tickers": FakeTickers})()
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)
    monkeypatch.setattr(key_stats_service, "_YFINANCE_INFO_BATCH_SIZE", 2)

    key_stats_service.preload_yfinance_info(stocks)

    assert batch_calls == ["AAA BBB", "CCC"]
    assert key_stats_service._load_yfinance_info(stocks[0]) == {
        "quoteType": "EQUITY",
        "trailingPE": 10.0,
    }


def test_get_valuation_snapshots_preloads_us_yfinance_once(monkeypatch) -> None:
    us_stock = stock_record(
        "US:AAA",
        market="US",
        symbol="AAA",
        name="Alpha Corp.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    tw_stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    us_quote = quote_record(
        "US:AAA",
        last_price=10.0,
        change=0.5,
        change_percent=5.0,
        previous_close=9.5,
        timestamp="2026-07-03T16:00:00-04:00",
        is_market_open=False,
        open_price=9.6,
        high=10.2,
        low=9.4,
        volume=1_000_000,
    )
    tw_quote = quote_record(
        "TW:2330",
        last_price=945.0,
        change=5.0,
        change_percent=0.53,
        previous_close=940.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=942.0,
        high=948.0,
        low=938.0,
        volume=123_456,
    )
    preload_calls: list[list] = []

    def fake_preload(stocks):
        preload_calls.append(list(stocks))

    monkeypatch.setattr(key_stats_service, "preload_yfinance_info", fake_preload)
    monkeypatch.setattr(
        key_stats_service,
        "get_valuation_snapshot",
        lambda stock, quote: {"peRatio": 12.0, "stockId": stock.id},  # noqa: ARG005
    )

    snapshots = key_stats_service.get_valuation_snapshots(
        [(us_stock, us_quote), (tw_stock, tw_quote)]
    )

    assert preload_calls == [[us_stock]]
    assert snapshots["US:AAA"]["peRatio"] == 12.0
    assert snapshots["TW:2330"]["peRatio"] == 12.0
