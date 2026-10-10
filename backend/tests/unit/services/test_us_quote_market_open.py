"""U.S. quote ``isMarketOpen`` must follow the live session rule (#262)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.stock import StockRecord
from app.services.providers import us_provider
from app.services.providers.yahoo_chart import coherent_price_change


def _us_stock() -> StockRecord:
    return StockRecord(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def test_us_quote_is_closed_when_built_after_hours(monkeypatch) -> None:
    evening = datetime(2026, 10, 7, 18, 0, tzinfo=ZoneInfo("America/New_York"))

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return evening if tz is None else evening.astimezone(tz)

    monkeypatch.setattr("app.services.market_clock.datetime", _Frozen)
    priced = coherent_price_change(190.0, 188.0)
    assert priced is not None
    quote = us_provider._quote_from_yahoo_fields(
        _us_stock(),
        {
            "regularMarketVolume": 1_000_000,
            "regularMarketOpen": 189.0,
            "regularMarketDayHigh": 191.0,
            "regularMarketDayLow": 188.5,
        },
        priced,
        datetime(2026, 10, 7, 16, 0, 1, tzinfo=ZoneInfo("America/New_York")),
    )

    assert quote.is_market_open is False


def test_us_quote_is_open_during_session(monkeypatch) -> None:
    during = datetime(2026, 10, 7, 15, 0, tzinfo=ZoneInfo("America/New_York"))

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return during if tz is None else during.astimezone(tz)

    monkeypatch.setattr("app.services.market_clock.datetime", _Frozen)
    priced = coherent_price_change(190.0, 188.0)
    assert priced is not None
    quote = us_provider._quote_from_yahoo_fields(
        _us_stock(),
        {"regularMarketVolume": 1_000_000},
        priced,
        datetime(2026, 10, 7, 14, 59, tzinfo=ZoneInfo("America/New_York")),
    )

    assert quote.is_market_open is True
