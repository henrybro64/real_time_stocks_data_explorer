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
