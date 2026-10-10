from __future__ import annotations

from datetime import date, datetime
import threading
import time
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord, QuoteRecord
from app.services.http_client import HttpFetchError
from app.services import market_clock
from app.services.providers import tw_provider
from tests.support import stock_record

_REAL_FETCH_YAHOO_LISTING_QUOTE = tw_provider.TWProvider._fetch_yahoo_listing_quote


TWSE_STOCK = stock_record(
    "TW:2330",
    market="TW",
    symbol="2330",
    name="台積電",
    exchange="TWSE",
    currency="TWD",
    timezone="Asia/Taipei",
    name_en="TSMC",
    name_zh_tw="台積電",
)

TPEX_STOCK = stock_record(
    "TW:6488",
    market="TW",
    symbol="6488",
    name="環球晶",
    exchange="TPEx",
    currency="TWD",
    timezone="Asia/Taipei",
    name_en="GlobalWafers",
    name_zh_tw="環球晶",
)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 7, 13, 12, 0, tzinfo=tz or ZoneInfo("Asia/Taipei"))


def _freeze_provider_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tw_provider, "datetime", _FrozenDateTime)
    monkeypatch.setattr(
        "app.services.daily_price_history_service.datetime",
        _FrozenDateTime,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda market, now=None: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_market_open",
        lambda market, now=None: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda _host: None,
    )


def _seed_cached_session_quote(
    monkeypatch: pytest.MonkeyPatch,
    quote: QuoteRecord | None,
) -> None:
    """Forming candles come from the quote cache now (#269), not a live MIS call."""
    monkeypatch.setattr(
        "app.services.daily_price_history_service.get_cached_quote",
        lambda stock_id: quote if quote is None or quote.id == stock_id else quote,
    )


def _quote(stock_id: str) -> QuoteRecord:
    return QuoteRecord(
        id=stock_id,
        last_price=950.0,
        change=5.0,
        change_percent=0.53,
        previous_close=945.0,
        open=946.0,
        high=955.0,
        low=940.0,
        volume=1_000_000,
        timestamp="2026-07-13T10:00:00+08:00",
        is_market_open=True,
    )


def _twse_row(minguo_day: str, volume: str = "1,000") -> list[str]:
    return [
        minguo_day,
        volume,
        "1,000,000",
        "100.00",
        "110.00",
        "90.00",
        "105.00",
        "+5.00",
        "1,234",
    ]


def _tpex_row(minguo_day: str, thousand_shares: str = "4,452") -> list[str]:
    return [
        minguo_day,
        thousand_shares,
        "4,333,718",
        "1,015.00",
        "1,040.00",
        "936.00",
        "950.00",
        "-65.00",
        "6,880",
    ]


def _raise_assertion(message: str):
    raise AssertionError(message)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> tw_provider.TWProvider:
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "get_stock",
        lambda self, stock_id: {
            TWSE_STOCK.id: TWSE_STOCK,
            TPEX_STOCK.id: TPEX_STOCK,
        }.get(stock_id),
    )
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_quote", lambda self, stock: None)
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_yahoo_tw_quote", lambda self, stock: None)
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_yahoo_listing_quote", lambda self, stock: None)
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda _host: None,
    )
    _seed_cached_session_quote(monkeypatch, None)
    return tw_provider.TWProvider()


def _yahoo_intraday_payload() -> dict:
    tz = ZoneInfo("Asia/Taipei")
    stamps = [
        datetime(2026, 7, 13, 9, 0, tzinfo=tz),
        datetime(2026, 7, 13, 9, 5, tzinfo=tz),
        datetime(2026, 7, 13, 9, 10, tzinfo=tz),
    ]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "Asia/Taipei"},
                    "timestamp": [int(item.timestamp()) for item in stamps],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, None, 102.0],
                                "high": [101.0, 103.0, 104.0],
                                "low": [99.5, 100.0, 101.5],
                                "close": [100.5, None, 103.5],
                                "volume": [1000, 50, 2500],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def test_get_history_1d_uses_yahoo_intraday_ohlc_for_twse_and_tpex(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    def fake_fetch(url: str, **_kwargs):
        urls.append(url)
        if "6488.TWO" in url or "2330.TW" in url:
            return _yahoo_intraday_payload()
        raise AssertionError(f"unexpected intraday url {url}")

    monkeypatch.setattr(tw_provider, "fetch_json", fake_fetch)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_daily_history",
        lambda *args, **kwargs: _raise_assertion("daily fetch should not run for 1D"),
    )

    for stock_id, symbol in ((TWSE_STOCK.id, "2330.TW"), (TPEX_STOCK.id, "6488.TWO")):
        candles = provider.get_history(stock_id, "1D")
        assert [candle.time for candle in candles] == [
            "2026-07-13T09:00:00+08:00",
            "2026-07-13T09:10:00+08:00",
        ]
        assert [(candle.open, candle.high, candle.low, candle.close, candle.volume) for candle in candles] == [
            (100.0, 101.0, 99.5, 100.5, 1000),
            (102.0, 104.0, 101.5, 103.5, 2500),
        ]
        assert any(symbol in url and "interval=5m" in url and "range=1d" in url for url in urls)

    assert not hasattr(tw_provider, "_approximate_intraday_candles")


def test_get_history_1d_raises_when_the_listing_chart_fails(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    def fail_fetch(url: str, **_kwargs):
        urls.append(url)
        raise HttpFetchError("yahoo chart unavailable")

    monkeypatch.setattr(tw_provider, "fetch_json", fail_fetch)
    for stock_id, symbol in ((TWSE_STOCK.id, "2330.TW"), (TPEX_STOCK.id, "6488.TWO")):
        with pytest.raises(RuntimeError, match="intraday history temporarily unavailable"):
            provider.get_history(stock_id, "1D")
        assert urls[-1].endswith(f"/{symbol}?interval=5m&range=1d")

    assert [url.rsplit("/", 1)[-1].split("?", 1)[0] for url in urls] == ["2330.TW", "6488.TWO"]


def test_get_history_1d_raises_when_the_chart_payload_is_unusable(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_args, **_kwargs: {"chart": {"result": None, "error": {"code": "Not Found"}}},
    )
    with pytest.raises(RuntimeError, match="intraday history temporarily unavailable"):
        provider.get_history(TWSE_STOCK.id, "1D")


def test_get_history_1d_returns_empty_when_the_listing_has_no_session_bars(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_args, **_kwargs: {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {"exchangeTimezoneName": "Asia/Taipei"},
                        "timestamp": [],
                        "indicators": {"quote": [{}]},
                    }
                ],
            }
        },
    )
    assert provider.get_history(TWSE_STOCK.id, "1D") == []


def test_daily_history_merges_forming_candle_when_official_history_is_empty(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_provider_clock(monkeypatch)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_daily_history",
        lambda self, stock, limit, *, range_name: [],
    )
    _seed_cached_session_quote(monkeypatch, _quote(TWSE_STOCK.id))

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    assert len(candles) == 1
    assert candles[0].time.startswith("2026-07-13")
    assert candles[0].close == 950.0


def test_daily_history_keeps_the_requested_limit_after_merging_today(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_provider_clock(monkeypatch)
    official = [
        CandleRecord(
            time=f"2026-07-{day:02d}T13:30:00+08:00",
            open=100,
            high=110,
            low=90,
            close=105,
            volume=1_000,
        )
        for day in range(1, 8)
    ]
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_daily_history",
        lambda self, stock, limit, *, range_name: official,
    )
    _seed_cached_session_quote(monkeypatch, _quote(TWSE_STOCK.id))

    candles = provider.get_history(TWSE_STOCK.id, "1W")
    assert len(candles) == tw_provider._daily_limit("1W")
    assert candles[0].time.startswith("2026-07-02")
    assert candles[-1].time.startswith("2026-07-13")
    assert candles[-1].close == 950.0


def test_daily_history_uses_cached_quote_when_session_fields_present(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_provider_clock(monkeypatch)
    _seed_cached_session_quote(monkeypatch, _quote(TWSE_STOCK.id))
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/10")],
    )

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    today = [candle for candle in candles if candle.time.startswith("2026-07-13")]
    assert len(today) == 1
    assert today[0].open == 946.0
    assert today[0].volume == 1_000_000


def test_daily_history_merges_forming_candle_during_session(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_provider_clock(monkeypatch)
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/10")],
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_tpex_month_rows",
        lambda self, stock, month_date: [_tpex_row("115/07/10")],
    )

    for stock_id in (TWSE_STOCK.id, TPEX_STOCK.id):
        _seed_cached_session_quote(monkeypatch, _quote(stock_id))
        candles = provider.get_history(stock_id, "1M")
        today = [candle for candle in candles if candle.time.startswith("2026-07-13")]
        assert len(today) == 1
        assert today[0].open == 946.0
        assert today[0].high == 955.0
        assert today[0].low == 940.0
        assert today[0].close == 950.0
        assert today[0].volume == 1_000_000
        assert candles[-1].time == today[0].time


def test_daily_history_updates_existing_today_candle_instead_of_duplicating(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_provider_clock(monkeypatch)
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    _seed_cached_session_quote(monkeypatch, _quote(TWSE_STOCK.id))
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/13")],
    )

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    today = [candle for candle in candles if candle.time.startswith("2026-07-13")]
    assert len(today) == 1
    assert today[0].close == 950.0
    assert today[0].open == 946.0


def test_daily_history_keeps_official_candle_after_close(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AfterClose(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 7, 13, 14, 0, tzinfo=tz or ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(tw_provider, "datetime", _AfterClose)
    monkeypatch.setattr(
        "app.services.daily_price_history_service.datetime",
        _AfterClose,
    )
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda market, now=None: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_market_open",
        lambda market, now=None: False,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda _host: None,
    )
    _seed_cached_session_quote(monkeypatch, _quote(TWSE_STOCK.id))
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/13")],
    )

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    today = [candle for candle in candles if "2026-07-13" in candle.time]
    assert len(today) == 1
    assert today[0].close == 105.0


def test_daily_history_appends_session_candle_after_close_when_official_row_is_missing(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AfterClose(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 7, 13, 14, 0, tzinfo=tz or ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(tw_provider, "datetime", _AfterClose)
    monkeypatch.setattr(
        "app.services.daily_price_history_service.datetime",
        _AfterClose,
    )
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda market, now=None: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_market_open",
        lambda market, now=None: False,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda _host: None,
    )
    _seed_cached_session_quote(monkeypatch, _quote(TWSE_STOCK.id))
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/10")],
    )

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    today = [candle for candle in candles if candle.time.startswith("2026-07-13")]
    assert len(today) == 1
    assert today[0].close == 950.0


def test_daily_history_uses_cached_quote_without_live_mis_fallback(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a cached quote, history must not call MIS/Yahoo for forming candles (#269)."""
    _freeze_provider_clock(monkeypatch)
    _seed_cached_session_quote(monkeypatch, None)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_quote",
        lambda self, stock: (_ for _ in ()).throw(AssertionError("MIS must not be called")),
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_yahoo_listing_quote",
        lambda self, stock: (_ for _ in ()).throw(AssertionError("Yahoo must not be called")),
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/10")],
    )

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    assert all(not candle.time.startswith("2026-07-13") for candle in candles)


def test_daily_history_ignores_incomplete_cached_quote(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_provider_clock(monkeypatch)
    incomplete = QuoteRecord(
        id=TWSE_STOCK.id,
        last_price=950.0,
        change=5.0,
        change_percent=0.53,
        previous_close=945.0,
        open=None,
        high=None,
        low=None,
        volume=None,
        timestamp="2026-07-13T10:00:00+08:00",
        is_market_open=True,
    )
    _seed_cached_session_quote(monkeypatch, incomplete)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/10")],
    )

    candles = provider.get_history(TWSE_STOCK.id, "1M")
    assert all(not candle.time.startswith("2026-07-13") for candle in candles)


def test_daily_history_does_not_invent_a_candle_before_open_or_on_weekends(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_calls = {"count": 0}

    def counting_quote(self, stock):
        quote_calls["count"] += 1
        return _quote(stock.id)

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_quote", counting_quote)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda self, stock, month_date: [_twse_row("115/07/10")],
    )

    class _BeforeOpen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 7, 13, 8, 30, tzinfo=tz or ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(tw_provider, "datetime", _BeforeOpen)
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    before_open = provider.get_history(TWSE_STOCK.id, "1M")
    assert all("2026-07-13" not in candle.time for candle in before_open)
    assert quote_calls["count"] == 0

    class _Weekend(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 7, 18, 12, 0, tzinfo=tz or ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(tw_provider, "datetime", _Weekend)
    monkeypatch.setattr(tw_provider, "is_trading_day", market_clock.is_trading_day)
    weekend = provider.get_history(TWSE_STOCK.id, "1M")
    assert all("2026-07-13" not in candle.time for candle in weekend)
    assert quote_calls["count"] == 0

    class _Holiday(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 7, 15, 12, 0, tzinfo=tz or ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(tw_provider, "datetime", _Holiday)
    monkeypatch.setattr(
        market_clock,
        "_tw_market_holidays",
        lambda year: frozenset({date(2026, 7, 15)}),
    )
    holiday = provider.get_history(TWSE_STOCK.id, "1M")
    assert all("2026-07-15" not in candle.time for candle in holiday)
    assert quote_calls["count"] == 0


@pytest.mark.parametrize("range_name", ["1W", "1M", "3M", "6M", "1Y"])
def test_get_history_selects_twse_source_for_daily_ranges(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
    range_name: str,
) -> None:
    calls: list[str] = []

    def fake_twse(self, stock, month_date: date) -> list[list[object]]:
        calls.append(f"twse:{stock.symbol}:{month_date.isoformat()}")
        return [_twse_row("115/07/01"), _twse_row("115/07/02")]

    def fake_tpex(self, stock, month_date: date) -> list[list[object]]:
        raise AssertionError("TPEx fetch should not run for TWSE stock")

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_twse_month_rows", fake_twse)
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", fake_tpex)
    _freeze_provider_clock(monkeypatch)

    candles = provider.get_history(TWSE_STOCK.id, range_name)

    assert candles
    assert all(call.startswith("twse:") for call in calls)
    assert len(candles) <= tw_provider._daily_limit(range_name)
    assert candles[0].volume == 1000


@pytest.mark.parametrize("range_name", ["1W", "1M", "3M", "6M", "1Y"])
def test_get_history_selects_tpex_source_for_daily_ranges(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
    range_name: str,
) -> None:
    calls: list[str] = []

    def fake_twse(self, stock, month_date: date) -> list[list[object]]:
        raise AssertionError("TWSE fetch should not run for TPEx stock")

    def fake_tpex(self, stock, month_date: date) -> list[list[object]]:
        calls.append(f"tpex:{stock.symbol}:{month_date.isoformat()}")
        return [_tpex_row("115/07/01"), _tpex_row("115/07/02")]

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_twse_month_rows", fake_twse)
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", fake_tpex)
    _freeze_provider_clock(monkeypatch)

    candles = provider.get_history(TPEX_STOCK.id, range_name)

    assert candles
    assert all(call.startswith("tpex:") for call in calls)
    assert len(candles) <= tw_provider._daily_limit(range_name)
    assert candles[0].open == pytest.approx(1015.0)
    assert candles[0].high == pytest.approx(1040.0)
    assert candles[0].low == pytest.approx(936.0)
    assert candles[0].close == pytest.approx(950.0)
    assert candles[0].volume == 4_452_000


def test_parse_tpex_daily_rows_sorts_and_dedupes(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tpex(self, stock, month_date: date) -> list[list[object]]:
        return [
            _tpex_row("115/07/03"),
            _tpex_row("115/07/01"),
            _tpex_row("115/07/02", thousand_shares="1,000"),
            _tpex_row("115/07/02", thousand_shares="2,000"),
            ["bad"],
            "not-a-row",
        ]

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", fake_tpex)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda *args, **kwargs: _raise_assertion("unexpected"),
    )
    _freeze_provider_clock(monkeypatch)

    candles = provider.get_history(TPEX_STOCK.id, "1W")

    assert [item.time[:10] for item in candles] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    ]
    assert candles[1].volume == 2_000_000


def test_valid_empty_upstream_returns_empty_list(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_tpex_month_rows",
        lambda self, stock, month_date: [],
    )
    _freeze_provider_clock(monkeypatch)

    assert provider.get_history(TPEX_STOCK.id, "1W") == []


def test_partial_month_failures_with_no_candles_raise_unavailable(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def flaky_fetch(self, stock, month_date: date) -> list[list[object]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise HttpFetchError("transient TPEx outage")
        return []

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", flaky_fetch)
    _freeze_provider_clock(monkeypatch)

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        provider.get_history(TPEX_STOCK.id, "1W")


def test_twse_rejected_stat_is_unavailable_not_silent_empty(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *args, **kwargs: {"stat": "系統忙碌中，請稍後再試"},
    )
    _freeze_provider_clock(monkeypatch)

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        provider.get_history(TWSE_STOCK.id, "1W")


def test_twse_no_data_stat_remains_legitimate_empty(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *args, **kwargs: {"stat": "很抱歉，沒有符合條件的資料!"},
    )
    _freeze_provider_clock(monkeypatch)

    assert provider.get_history(TWSE_STOCK.id, "1W") == []


def test_newer_month_hard_failure_still_serves_older_candles(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def flaky_fetch(self, stock, month_date: date) -> list[list[object]]:
        if month_date == date(2026, 7, 1):
            raise HttpFetchError("current month timeout")
        return [_tpex_row("115/06/30")]

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", flaky_fetch)
    _freeze_provider_clock(monkeypatch)
    _seed_cached_session_quote(monkeypatch, None)

    candles = provider.get_history(TPEX_STOCK.id, "1W")
    assert candles
    assert candles[-1].time.startswith("2026-06-30")


def test_tpex_volume_scales_thousand_shares_not_lot_size(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fx_etf = stock_record(
        "TW:00677K",
        market="TW",
        symbol="00677K",
        name="富邦VIX",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="Fubon VIX",
        name_zh_tw="富邦VIX",
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "get_stock",
        lambda self, stock_id: fx_etf if stock_id == fx_etf.id else None,
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_tpex_month_rows",
        lambda self, stock, month_date: [_tpex_row("115/07/01", thousand_shares="10")],
    )
    _freeze_provider_clock(monkeypatch)

    candles = provider.get_history(fx_etf.id, "1W")

    assert candles
    # tradingStock volume is 成交仟股; 10 → 10,000 shares (not lot-size * 10).
    assert candles[0].volume == 10_000


def test_provider_timeouts_raise_runtime_error_instead_of_silent_empty(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_fetch(self, stock, month_date: date) -> list[list[object]]:
        raise HttpFetchError("timeout talking to TPEx")

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", failing_fetch)
    _freeze_provider_clock(monkeypatch)

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        provider.get_history(TPEX_STOCK.id, "1W")


def test_extract_tpex_daily_rows_supports_tables_and_legacy_shapes() -> None:
    modern = {
        "stat": "ok",
        "tables": [{"data": [_tpex_row("115/06/01")]}],
    }
    legacy_aa = {"aaData": [_tpex_row("115/06/02")]}
    legacy_data = {"data": [_tpex_row("115/06/03")]}

    assert len(tw_provider._extract_tpex_daily_rows(modern)) == 1
    assert len(tw_provider._extract_tpex_daily_rows(legacy_aa)) == 1
    assert len(tw_provider._extract_tpex_daily_rows(legacy_data)) == 1
    assert tw_provider._extract_tpex_daily_rows({"tables": []}) == []
    assert tw_provider._extract_tpex_daily_rows({"tables": [{"data": []}]}) == []


def test_extract_tpex_daily_rows_rejects_unrecognized_payloads() -> None:
    with pytest.raises(ValueError, match="not a JSON object"):
        tw_provider._extract_tpex_daily_rows([])
    with pytest.raises(ValueError, match="missing tables/aaData/data"):
        tw_provider._extract_tpex_daily_rows({"stat": "ok", "message": "blocked"})
    with pytest.raises(ValueError, match="missing usable data"):
        tw_provider._extract_tpex_daily_rows({"tables": [{"title": "daily"}]})
    with pytest.raises(ValueError, match="tables\\[\\]\\.data field is not a list"):
        tw_provider._extract_tpex_daily_rows({"tables": [{"data": {"rows": []}}]})


def test_fetch_tpex_month_rows_uses_official_endpoint(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_fetch_tpex_data(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return {"tables": [{"data": [_tpex_row("115/07/01")]}]}

    monkeypatch.setattr(
        "app.services.taiwan_market_data.fetch_tpex_data",
        fake_fetch_tpex_data,
    )

    rows = provider._fetch_tpex_month_rows(TPEX_STOCK, date(2026, 7, 1))

    assert captured["url"] == tw_provider._TPEX_HISTORY_URL
    assert captured["params"] == {
        "code": "6488",
        "date": "2026/07/01",
        "response": "json",
    }
    assert rows == [_tpex_row("115/07/01")]


def test_daily_limit_matrix() -> None:
    assert tw_provider._daily_limit("1W") == 7
    assert tw_provider._daily_limit("1M") == 30
    assert tw_provider._daily_limit("3M") == 65
    assert tw_provider._daily_limit("6M") == 130
    assert tw_provider._daily_limit("1Y") == 260


def test_surplus_older_month_is_not_fetched_once_limit_is_met(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_tpex(self, stock, month_date: date) -> list[list[object]]:
        calls.append(month_date.isoformat())
        if month_date <= date(2026, 2, 1):
            raise HttpFetchError("oldest surplus month failed")
        return [
            _tpex_row(f"115/{month_date.month:02d}/{day:02d}")
            for day in range(1, 21)
        ]

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", fake_tpex)
    _freeze_provider_clock(monkeypatch)

    # limit=90 would otherwise expand months_to_fetch into older surplus months;
    # dense recent months should satisfy the budget first and skip the failing ones.
    candles = provider.get_history(TPEX_STOCK.id, "1M", limit=90)

    assert len(candles) == 90
    assert "2026-02-01" not in calls
    assert "2026-01-01" not in calls
    assert all(call >= "2026-03-01" for call in calls)


def test_get_history_passes_explicit_limit_for_sma_warmup(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_daily(self, stock, *, limit: int, range_name: str):
        captured["limit"] = limit
        captured["range"] = range_name
        return [
            CandleRecord(
                time="2026-07-01T13:30:00+08:00",
                open=1,
                high=2,
                low=1,
                close=1.5,
                volume=1000,
            )
        ]

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_daily_history", fake_daily)

    candles = provider.get_history(TPEX_STOCK.id, "3M", limit=125)

    assert captured["limit"] == 125
    assert captured["range"] == "3M"
    assert len(candles) == 1


def test_stock_catalog_refresh_is_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tw_provider.TWProvider, "_catalog_cache", None)
    monkeypatch.setattr(tw_provider.TWProvider, "_catalog_cached_at", None)
    load_count = 0
    count_lock = threading.Lock()

    def slow_json(_url: str, _params: dict[str, str]) -> list[object]:
        nonlocal load_count
        with count_lock:
            load_count += 1
        time.sleep(0.1)
        return []

    monkeypatch.setattr(tw_provider, "_load_json", slow_json)
    monkeypatch.setattr(tw_provider, "_load_text", lambda _url: "")

    provider = tw_provider.TWProvider()
    catalogs: list[dict] = []

    def worker() -> None:
        catalogs.append(provider.refresh_catalog())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert load_count == 1
    assert len(catalogs) == 2
    assert catalogs[0] is catalogs[1]
    monkeypatch.setattr(tw_provider.TWProvider, "_catalog_cache", None)
    monkeypatch.setattr(tw_provider.TWProvider, "_catalog_cached_at", None)
