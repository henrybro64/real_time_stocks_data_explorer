"""Daily price history store / cache-merge behavior (#269)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord, QuoteRecord
from app.services.daily_price_history_service import DailyPriceHistoryService
from tests.support import stock_record


def _tw_stock():
    return stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="TSMC",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


class _FakeRepo:
    def __init__(self) -> None:
        self.rows: list[CandleRecord] = []
        self.upserts: list[list[CandleRecord]] = []

    def is_available(self) -> bool:
        return True

    def list_bars(self, *, market: str, symbol: str, limit: int):
        return []

    def upsert_candles(self, *, market, symbol, candles, source):
        self.upserts.append(list(candles))
        self.rows = list(candles)
        return type("Stats", (), {"inserted": len(candles), "updated": 0})()


class _FakeProvider:
    def __init__(self, candles: list[CandleRecord]) -> None:
        self.candles = candles
        self.calls = 0

    def fetch_daily_history_uncached(self, stock, *, limit: int, range_name: str):
        self.calls += 1
        return self.candles[-limit:]


def test_second_request_uses_store_without_upstream(monkeypatch) -> None:
    candles = [
        CandleRecord(
            time="2026-10-01",
            open=100,
            high=110,
            low=99,
            close=105,
            volume=1000,
        ),
        CandleRecord(
            time="2026-10-02",
            open=105,
            high=112,
            low=104,
            close=111,
            volume=1100,
        ),
    ]
    repo = _FakeRepo()
    provider = _FakeProvider(candles)
    service = DailyPriceHistoryService(repository=repo)
    stock = _tw_stock()

    # Force "current month fresh" after first upsert by stubbing list_bars.
    def list_bars(*, market, symbol, limit):
        class Row:
            def __init__(self, candle: CandleRecord):
                self.date = datetime.fromisoformat(candle.time).date()
                self.open = candle.open
                self.high = candle.high
                self.low = candle.low
                self.close = candle.close
                self.volume = candle.volume

        return [Row(c) for c in repo.rows[-limit:]]

    repo.list_bars = list_bars  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda *_a, **_k: False,
    )

    first = service.get_daily_history(stock, "1M", limit=90, provider=provider)
    second = service.get_daily_history(stock, "1M", limit=90, provider=provider)

    assert len(first) == 2
    assert len(second) == 2
    assert provider.calls == 1


def test_forming_candle_comes_from_quote_cache_not_provider(monkeypatch) -> None:
    repo = _FakeRepo()
    repo.rows = [
        CandleRecord(
            time="2026-10-09",
            open=100,
            high=110,
            low=99,
            close=105,
            volume=1000,
        )
    ]
    provider = _FakeProvider([])
    service = DailyPriceHistoryService(repository=repo)
    stock = _tw_stock()

    def list_bars(*, market, symbol, limit):
        class Row:
            def __init__(self, candle: CandleRecord):
                self.date = datetime.fromisoformat(candle.time).date()
                self.open = candle.open
                self.high = candle.high
                self.low = candle.low
                self.close = candle.close
                self.volume = candle.volume

        return [Row(c) for c in repo.rows[-limit:]]

    repo.list_bars = list_bars  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.market_session_window",
        lambda *_a, **_k: (
            datetime(2026, 10, 10, 9, 0, tzinfo=ZoneInfo("Asia/Taipei")).time(),
            datetime(2026, 10, 10, 13, 30, tzinfo=ZoneInfo("Asia/Taipei")).time(),
        ),
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.datetime",
        type(
            "FixedDateTime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(
                        2026, 10, 10, 10, 30, tzinfo=tz or ZoneInfo("Asia/Taipei")
                    )
                ),
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_market_open",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.get_cached_quote",
        lambda _stock_id: QuoteRecord(
            id="TW:2330",
            last_price=120.0,
            change=1.0,
            change_percent=1.0,
            previous_close=119.0,
            timestamp="2026-10-10T10:00:00+08:00",
            is_market_open=True,
            open=118.0,
            high=121.0,
            low=117.0,
            volume=5000,
        ),
    )
    repo.rows = [
        CandleRecord(time=day, open=100, high=110, low=99, close=105, volume=1000)
        for day in (
            "2026-05-15",
            "2026-06-15",
            "2026-07-15",
            "2026-08-14",
            "2026-09-15",
            "2026-10-09",
        )
    ]

    candles = service.get_daily_history(stock, "1M", limit=90, provider=provider)
    assert provider.calls == 0
    assert candles[-1].close == 120.0


class _MonthProvider:
    def __init__(self) -> None:
        self.months: list[str] = []
        self.fail = False

    def fetch_daily_month(self, stock, month):
        self.months.append(month.isoformat())
        if self.fail:
            raise RuntimeError("upstream down")
        return [
            CandleRecord(
                time=month.isoformat(),
                open=1,
                high=2,
                low=1,
                close=1.5,
                volume=10,
            )
        ]


def test_missing_months_are_fetched_once_per_month(monkeypatch) -> None:
    repo = _FakeRepo()
    provider = _MonthProvider()
    service = DailyPriceHistoryService(repository=repo)
    waits: list[str] = []
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda host: waits.append(host),
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.datetime",
        type(
            "FixedDateTime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 10, 11, 12, 0, tzinfo=tz)
                ),
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.months_needed_for_limit",
        lambda _limit: 2,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda *_a, **_k: False,
    )

    service.get_daily_history(_tw_stock(), "1M", limit=90, provider=provider)
    service.get_daily_history(_tw_stock(), "1M", limit=90, provider=provider)

    assert provider.months == ["2026-10-01", "2026-09-01"]
    assert waits == ["twse", "twse"]


def test_failed_month_cools_down_before_retry(monkeypatch) -> None:
    repo = _FakeRepo()
    provider = _MonthProvider()
    provider.fail = True
    service = DailyPriceHistoryService(repository=repo)
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda _host: None,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.months_needed_for_limit",
        lambda _limit: 2,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda *_a, **_k: False,
    )
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "app.services.daily_price_history_service.time.monotonic",
        lambda: clock["now"],
    )

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        service.get_daily_history(_tw_stock(), "1M", limit=90, provider=provider)
    service.get_daily_history(_tw_stock(), "1M", limit=90, provider=provider)
    assert len(provider.months) == 2

    clock["now"] += 15 * 60
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        service.get_daily_history(_tw_stock(), "1M", limit=90, provider=provider)
    assert len(provider.months) == 4


def test_closed_market_refreshes_only_the_lagging_month(monkeypatch) -> None:
    repo = _FakeRepo()
    repo.rows = [
        CandleRecord(time="2026-10-09", open=1, high=2, low=1, close=1.5, volume=10)
    ]

    def list_bars(*, market, symbol, limit):
        class Row:
            def __init__(self, candle: CandleRecord):
                self.date = datetime.fromisoformat(candle.time).date()
                self.open = candle.open
                self.high = candle.high
                self.low = candle.low
                self.close = candle.close
                self.volume = candle.volume

        return [Row(c) for c in repo.rows[-limit:]]

    repo.list_bars = list_bars  # type: ignore[method-assign]
    provider = _MonthProvider()
    service = DailyPriceHistoryService(repository=repo)
    stock = _tw_stock()
    monkeypatch.setattr(
        "app.services.daily_price_history_service._wait_for_host_slot",
        lambda _host: None,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.datetime",
        type(
            "FixedDateTime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(
                        2026, 10, 10, 14, 0, tzinfo=tz or ZoneInfo("Asia/Taipei")
                    )
                ),
                "combine": staticmethod(datetime.combine),
                "min": datetime.min,
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_market_open",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.is_trading_day",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "app.services.daily_price_history_service.market_session_window",
        lambda *_a, **_k: (
            datetime(2026, 10, 10, 9, 0).time(),
            datetime(2026, 10, 10, 13, 30).time(),
        ),
    )

    assert service.refresh_closed_current_month(stock, provider) is True
    assert provider.months == ["2026-10-01"]
    repo.rows.append(
        CandleRecord(time="2026-10-10", open=1, high=2, low=1, close=2, volume=10)
    )
    assert service.refresh_closed_current_month(stock, provider) is False
    assert provider.months == ["2026-10-01"]
