"""Taiwan live quote integrity (#258).

MIS ``y`` is the previous/reference close. It must never become ``last_price``
because the latest-trade field ``z`` is missing, and ``change`` /
``change_percent`` must always come from the same price pair that is returned.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.quote import quote_to_response
from app.services import market_clock
from app.services.providers import tw_provider

_TZ = ZoneInfo("Asia/Taipei")


@pytest.fixture(autouse=True)
def _no_holiday_calendar_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep quote tests off the network: market status reads the TWSE holiday page."""
    monkeypatch.setattr(market_clock, "_tw_market_holidays", lambda _year: frozenset())


def _stock(symbol: str = "2368", exchange: str = "TWSE") -> StockRecord:
    return StockRecord(
        id=f"TW:{symbol}",
        market="TW",
        symbol=symbol,
        name="金像電",
        exchange=exchange,
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _use_stock(monkeypatch: pytest.MonkeyPatch, stock: StockRecord) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()


def _mis_2368(**overrides: str) -> dict[str, list[dict[str, str]]]:
    """The observed 2368 session: open 1,160, high 1,215, previous close 1,105."""
    row = {
        "z": "1180.00",
        "pz": "1175.00",
        "y": "1105.00",
        "u": "1215.00",
        "w": "995.00",
        "o": "1160.00",
        "h": "1215.00",
        "l": "1150.00",
        "v": "8421",
        "d": "20260713",
        "t": "10:42:15",
    }
    row.update(overrides)
    return {"msgArray": [row]}


def _ts(day: int, hour: int, minute: int) -> int:
    return int(datetime(2026, 7, day, hour, minute, tzinfo=_TZ).timestamp())


def _yahoo_chart(
    *,
    price: float = 1182.0,
    market_time: int | None = None,
    chart_previous_close: float | None = 980.0,
    previous_close: float | None = None,
    bars: list[tuple[int, float | None]] | None = None,
) -> dict[str, object]:
    """Yahoo ``interval=1d&range=5d`` chart; ``bars`` are (timestamp, close)."""
    if bars is None:
        bars = [
            (_ts(7, 13, 30), 1010.0),
            (_ts(8, 13, 30), 1040.0),
            (_ts(9, 13, 30), 1072.0),
            (_ts(10, 13, 30), 1105.0),
            (_ts(13, 10, 40), price),
        ]
    meta: dict[str, object] = {
        "regularMarketPrice": price,
        "regularMarketTime": market_time if market_time is not None else _ts(13, 10, 40),
        "regularMarketDayHigh": 1215.0,
        "regularMarketDayLow": 1150.0,
        "regularMarketVolume": 8_400_000,
    }
    if chart_previous_close is not None:
        meta["chartPreviousClose"] = chart_previous_close
    if previous_close is not None:
        meta["previousClose"] = previous_close
    closes = [close for _ts_value, close in bars]
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": [ts_value for ts_value, _close in bars],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1160.0 if c is not None else None for c in closes],
                                "high": [1215.0 if c is not None else None for c in closes],
                                "low": [1150.0 if c is not None else None for c in closes],
                                "close": closes,
                                "volume": [8_400_000 if c is not None else None for c in closes],
                            }
                        ]
                    },
                }
            ]
        }
    }


def _no_yahoo(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}

    def fail(*_args, **_kwargs):
        calls["count"] += 1
        return None

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_yahoo_tw_quote", fail)
    return calls


def _assert_coherent(quote) -> None:
    expected_change = round(quote.last_price - quote.previous_close, 2)
    assert quote.change == pytest.approx(expected_change)
    assert quote.change_percent == pytest.approx(
        round(expected_change / quote.previous_close * 100, 2)
    )
    if quote.last_price != quote.previous_close:
        assert quote.change != 0


# --- MIS field provenance -------------------------------------------------


def test_mis_latest_trade_is_the_current_price(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368())

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(1180.0)
    assert quote.previous_close == pytest.approx(1105.0)
    assert quote.change == pytest.approx(75.0)
    assert quote.change_percent == pytest.approx(6.79)
    _assert_coherent(quote)


@pytest.mark.parametrize("missing", ["-", "", "--"])
def test_missing_trade_never_reports_previous_close_as_current(monkeypatch, missing: str) -> None:
    """The 2368 failure: last 1,105 == previous close with open 1,160 / high 1,215."""
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z=missing, pz="-")
    )
    yahoo_calls = _no_yahoo(monkeypatch)

    assert tw_provider.TWProvider()._fetch_quote(stock) is None
    assert tw_provider.TWProvider().get_quote(stock.id) is None
    assert yahoo_calls["count"] == 1


@pytest.mark.parametrize("symbol", ["2368", "6488"])
def test_previous_close_inside_the_session_range_is_still_not_a_price(monkeypatch, symbol) -> None:
    """Recorded 2026-10-07 rows: y lies inside today's range, z and trade are absent."""
    stock = _stock(symbol, exchange="TWSE" if symbol == "2368" else "TPEx")
    _use_stock(monkeypatch, stock)
    rows = {
        "2368": {"y": "1260.0000", "o": "1265.0000", "h": "1310.0000", "l": "1255.0000", "v": "7485"},
        "6488": {"y": "1205.0000", "o": "1185.0000", "h": "1240.0000", "l": "1180.0000", "v": "7585"},
    }
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", pz="-", d="20261007", t="10:46:01", **rows[symbol]),
    )
    yahoo_calls = _no_yahoo(monkeypatch)

    assert tw_provider.TWProvider()._fetch_quote(stock) is None
    assert tw_provider.TWProvider().get_quote(stock.id) is None
    assert yahoo_calls["count"] == 1


def test_missing_trade_field_entirely_is_not_a_quote(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    payload = _mis_2368()
    del payload["msgArray"][0]["z"]
    del payload["msgArray"][0]["pz"]
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: payload)

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


def test_missing_trade_falls_back_to_a_coherent_yahoo_quote(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(tw_provider, "fetch_json", lambda *_a, **_k: _yahoo_chart(price=1182.0))

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(1182.0)
    # The prior session's bar, not the 5-day window's chartPreviousClose (980).
    assert quote.previous_close == pytest.approx(1105.0)
    assert quote.change == pytest.approx(77.0)
    assert quote.change_percent == pytest.approx(6.97)
    _assert_coherent(quote)
    # Yahoo has no official band; MIS limit fields are not mixed in.
    assert quote.price_limit_status == "unknown"
    assert quote.limit_up_price is None
    assert quote.timestamp == datetime(2026, 7, 13, 10, 40, tzinfo=_TZ).isoformat()


def test_fallback_failure_does_not_manufacture_a_price(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: {"chart": {"result": None, "error": {"code": "Not Found"}}},
    )

    assert tw_provider.TWProvider().get_quote(stock.id) is None


def test_trade_equal_to_previous_close_is_a_real_unchanged_quote(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="1105.00", o="1100.00", h="1110.00", l="1095.00"),
    )
    yahoo_calls = _no_yahoo(monkeypatch)

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(1105.0)
    assert quote.change == 0
    assert quote.change_percent == 0
    assert yahoo_calls["count"] == 0
    response = quote_to_response(quote)
    assert response.lastPrice == pytest.approx(1105.0)
    assert response.changePercent == 0


def test_trade_outside_its_own_session_range_is_rejected(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    # A trade at the reference price while the session never traded below 1,150.
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="1105.00"))

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


def _trade(price: str, at: str, volume: int = 1) -> dict[str, object]:
    return {"t": at, "v": volume, "z": price, "ft": 20}


def test_live_snapshot_uses_the_latest_trade_when_z_is_blank(monkeypatch) -> None:
    """Live MIS rows are 5-second snapshots: ``z`` is usually "-" between trades."""
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", pz="-", t="10:42:15", trade=_trade("1175.0000", "10:41:50")),
    )

    quote = tw_provider.TWProvider()._fetch_quote(stock)

    assert quote is not None
    assert quote.last_price == pytest.approx(1175.0)
    assert quote.change == pytest.approx(70.0)
    _assert_coherent(quote)
    # Stamped with the trade, not the later snapshot.
    assert quote.timestamp == datetime(2026, 7, 13, 10, 41, 50, tzinfo=_TZ).isoformat()


def test_recorded_live_tsmc_row_reports_its_latest_trade(monkeypatch) -> None:
    """A real 2330 MIS row captured mid-session on 2026-10-07 (z was "-")."""
    stock = _stock("2330")
    _use_stock(monkeypatch, stock)
    row = {
        "c": "2330", "ex": "tse", "d": "20261007", "t": "10:46:45", "%": "10:46:45",
        "z": "-", "pz": "-", "tv": "-", "ps": "-", "ts": "0", "ip": "0",
        "o": "2565.0000", "h": "2580.0000", "l": "2560.0000", "y": "2585.0000",
        "u": "2840.0000", "w": "2330.0000", "v": "7345",
        "a": "2580.0000_2585.0000_2590.0000_2595.0000_2600.0000_",
        "b": "2575.0000_2570.0000_2565.0000_2560.0000_2555.0000_",
        "trade": {"t": "10:46:18", "v": 1, "z": "2580.0000", "ft": 20},
    }
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: {"msgArray": [row]})

    quote = tw_provider.TWProvider()._fetch_quote(stock)

    assert quote is not None
    assert quote.last_price == pytest.approx(2580.0)
    assert quote.previous_close == pytest.approx(2585.0)
    assert quote.change == pytest.approx(-5.0)
    assert quote.change_percent == pytest.approx(-0.19)
    assert quote.timestamp == datetime(2026, 10, 7, 10, 46, 18, tzinfo=_TZ).isoformat()
    assert quote.price_limit_status == "none"


def test_recorded_live_trade_equal_to_previous_close_is_valid(monkeypatch) -> None:
    """3105 traded at exactly its 592 reference on 2026-10-07: a real unchanged quote."""
    stock = _stock("3105", exchange="TPEx")
    _use_stock(monkeypatch, stock)
    row = {
        "c": "3105", "ex": "otc", "d": "20261007", "t": "10:47:00", "z": "-", "pz": "-",
        "o": "600.0000", "h": "613.0000", "l": "582.0000", "y": "592.0000",
        "u": "651.0000", "w": "533.0000", "v": "14795",
        "trade": {"t": "10:45:50", "v": 6, "z": "592.0000", "ft": 12},
    }
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: {"msgArray": [row]})

    quote = tw_provider.TWProvider()._fetch_quote(stock)

    assert quote is not None
    assert quote.last_price == pytest.approx(592.0)
    assert quote.change == 0
    assert quote.change_percent == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"pz": "1175.00"},  # undocumented, never a trade
        {"trade": _trade("1105.0000", "10:41:50")},  # outside the session's high/low
        {"trade": _trade("1175.0000", "10:50:00")},  # stamped after the snapshot
        {"trade": _trade("1175.0000", "10:41:50"), "v": "0"},  # no session volume
        {"trade": _trade("1175.0000", "10:41:50"), "v": "-"},
        {"trade": _trade("-", "10:41:50")},
        {"trade": _trade("0.0000", "10:41:50")},
        {"trade": _trade("1175.0000", "-")},
        {"trade": "1175.0000"},  # not an object
    ],
)
def test_untrustworthy_trade_is_rejected(monkeypatch, overrides: dict[str, object]) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", **overrides)
    )

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


def test_trade_object_is_preferred_over_snapshot_z(monkeypatch) -> None:
    """``trade`` is the matched trade; ``z`` could carry a trial-match price."""
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="1190.00", trade=_trade("1180.0000", "10:42:15")),
    )

    quote = tw_provider.TWProvider()._fetch_quote(stock)

    assert quote is not None
    assert quote.last_price == pytest.approx(1180.0)


def test_pre_open_snapshot_without_trades_is_not_a_zero_change_quote(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", pz="-", o="-", h="-", l="-", v="-", t="08:45:00"),
    )
    _no_yahoo(monkeypatch)

    assert tw_provider.TWProvider().get_quote(stock.id) is None


# --- Limit-up / limit-down -------------------------------------------------


def test_limit_up_with_missing_trade_is_not_previous_close_or_limit_price(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", pz="-", o="1215.00", h="1215.00", l="1215.00"),
    )
    _no_yahoo(monkeypatch)

    assert tw_provider.TWProvider().get_quote(stock.id) is None


def test_limit_up_from_the_trade_object_is_classified(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(
            z="-", o="1200.00", h="1215.00", l="1195.00", trade=_trade("1215.0000", "10:40:02")
        ),
    )

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(1215.0)
    assert quote.change == pytest.approx(110.0)
    assert quote.price_limit_status == "limit_up"
    assert quote.limit_up_price == pytest.approx(1215.0)


def test_limit_down_from_the_trade_object_is_classified(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(
            z="-", o="1000.00", h="1010.00", l="995.00", trade=_trade("995.0000", "10:40:02")
        ),
    )

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(995.0)
    assert quote.price_limit_status == "limit_down"


def test_limit_up_trade_is_classified(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="1215.00"))

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(1215.0)
    assert quote.price_limit_status == "limit_up"
    _assert_coherent(quote)


def test_limit_down_with_missing_trade_is_not_previous_close(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", pz="-", o="995.00", h="995.00", l="995.00"),
    )
    _no_yahoo(monkeypatch)

    assert tw_provider.TWProvider().get_quote(stock.id) is None


def test_limit_down_trade_is_classified(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="995.00", o="1000.00", h="1010.00", l="995.00"),
    )

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(995.0)
    assert quote.change == pytest.approx(-110.0)
    assert quote.change_percent == pytest.approx(-9.95)
    assert quote.price_limit_status == "limit_down"


# --- TPEx -------------------------------------------------------------------


def test_tpex_missing_trade_uses_the_two_listing_fallback(monkeypatch) -> None:
    stock = _stock("6488", exchange="TPEx")
    _use_stock(monkeypatch, stock)
    mis_params: list[dict[str, str]] = []
    yahoo_urls: list[str] = []

    def load_json(_url, params, **_kwargs):
        mis_params.append(dict(params))
        return _mis_2368(z="-", pz="-")

    def fetch_json(url: str, **_kwargs):
        yahoo_urls.append(url)
        return _yahoo_chart(price=1182.0)

    monkeypatch.setattr(tw_provider, "_load_json", load_json)
    monkeypatch.setattr(tw_provider, "fetch_json", fetch_json)

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert mis_params[0]["ex_ch"] == "otc_6488.tw"
    assert "/6488.TWO?" in yahoo_urls[0]
    assert quote is not None
    assert quote.last_price == pytest.approx(1182.0)
    _assert_coherent(quote)


# --- Timestamp and market status ---------------------------------------------


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, day: int, hour: int, minute: int) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 7, day, hour, minute, tzinfo=tz or _TZ)

    monkeypatch.setattr(tw_provider, "datetime", _Frozen)


def test_mis_timestamp_and_market_status_come_from_the_snapshot(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    _freeze_clock(monkeypatch, 13, 10, 43)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368())

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.timestamp == datetime(2026, 7, 13, 10, 42, 15, tzinfo=_TZ).isoformat()
    assert quote.is_market_open is True


def test_session_close_quote_is_not_open_after_the_close(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    _freeze_clock(monkeypatch, 13, 13, 45)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(t="13:30:00"))

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.timestamp.startswith("2026-07-13T13:30:00")
    assert quote.is_market_open is False


def test_previous_session_fallback_is_not_open_during_today_session(monkeypatch) -> None:
    """Yesterday's 13:30 price must not claim the market is open today."""
    stock = _stock()
    _use_stock(monkeypatch, stock)
    _freeze_clock(monkeypatch, 13, 10, 45)
    bars = [(_ts(9, 13, 30), 1072.0), (_ts(10, 13, 30), 1105.0)]
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: _yahoo_chart(price=1105.0, market_time=_ts(10, 13, 30), bars=bars),
    )

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.timestamp.startswith("2026-07-10T13:30")
    assert quote.is_market_open is False


def test_current_session_fallback_is_open_during_the_session(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    _freeze_clock(monkeypatch, 13, 10, 45)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(tw_provider, "fetch_json", lambda *_a, **_k: _yahoo_chart(price=1182.0))

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.is_market_open is True


@pytest.mark.parametrize(
    "overrides",
    [{"d": "-", "t": "-"}, {"d": "", "t": ""}, {"d": "20260713", "t": "not-a-time"}],
)
def test_mis_snapshot_without_a_timestamp_is_rejected(monkeypatch, overrides) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    payload = _mis_2368(**overrides)
    payload["msgArray"][0].pop("%", None)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: payload)

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


@pytest.mark.parametrize(
    ("raw_date", "trade_time", "snapshot_time"),
    [
        ("20260711", "10:41:50", "10:42:15"),  # Saturday
        ("20260713", "08:45:00", "08:45:10"),  # pre-open trial matching
        ("20260713", "14:10:00", "14:10:05"),  # after the session
    ],
)
def test_trade_outside_a_trading_session_is_rejected(
    monkeypatch, raw_date: str, trade_time: str, snapshot_time: str
) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(
            z="-", d=raw_date, t=snapshot_time, trade=_trade("1175.0000", trade_time)
        ),
    )

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


def test_trade_stamped_in_the_future_is_rejected(monkeypatch) -> None:
    """Before the open, yesterday's 13:30 trade must not be read as today's."""
    stock = _stock()
    _use_stock(monkeypatch, stock)
    _freeze_clock(monkeypatch, 13, 8, 45)
    payload = _mis_2368(z="-", t="-", trade=_trade("1175.0000", "13:30:00"))
    payload["msgArray"][0].pop("%", None)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: payload)

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


@pytest.mark.parametrize("closing_time", ["13:30:00", "13:30:01", "13:30:59"])
def test_closing_auction_trade_within_the_close_minute_is_kept(monkeypatch, closing_time) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", t="13:31:05", trade=_trade("1185.0000", closing_time)),
    )

    quote = tw_provider.TWProvider()._fetch_quote(stock)

    assert quote is not None
    assert quote.last_price == pytest.approx(1185.0)
    assert quote.timestamp == f"2026-07-13T{closing_time}+08:00"


def test_trade_after_the_close_minute_is_rejected(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", t="13:31:05", trade=_trade("1185.0000", "13:31:00")),
    )

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


def test_pre_open_snapshot_z_is_not_a_trade(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="1175.00", t="08:55:00")
    )

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


def test_trade_on_a_holiday_is_rejected(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        market_clock,
        "_tw_market_holidays",
        lambda _year: frozenset({datetime(2026, 7, 13).date()}),
    )
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", trade=_trade("1175.0000", "10:41:50")),
    )

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


# --- Yahoo fallback backoff ------------------------------------------------------


def test_failed_yahoo_fallback_backs_off_but_mis_is_still_tried(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    clock = {"now": 1000.0}
    mis_calls = {"count": 0}

    def load_json(*_args, **_kwargs):
        mis_calls["count"] += 1
        return _mis_2368(z="-", pz="-")

    monkeypatch.setattr(tw_provider, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tw_provider, "_load_json", load_json)
    yahoo_calls = _no_yahoo(monkeypatch)
    provider = tw_provider.TWProvider()

    assert provider.get_quote(stock.id) is None
    assert provider.get_quote(stock.id) is None
    assert mis_calls["count"] == 2
    assert yahoo_calls["count"] == 1

    clock["now"] += tw_provider._YAHOO_QUOTE_FAILURE_BACKOFF_SECONDS + 1
    assert provider.get_quote(stock.id) is None
    assert yahoo_calls["count"] == 2


def test_yahoo_backoff_is_per_stock(monkeypatch) -> None:
    stock = _stock()
    other = _stock("2330")
    tw_provider.reset_mis_circuit_for_tests()
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_stock_catalog",
        lambda self: {stock.id: stock, other.id: other},
    )
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()
    monkeypatch.setattr(tw_provider, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    yahoo_calls: list[str] = []

    def fetch_yahoo(self, target):
        yahoo_calls.append(target.id)
        return _yahoo_quote(target) if target.id == other.id else None

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_yahoo_tw_quote", fetch_yahoo)
    provider = tw_provider.TWProvider()

    assert provider.get_quote(stock.id) is None
    assert provider.get_quote(other.id) is not None
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()
    assert provider.get_quote(stock.id) is None  # backed off
    assert provider.get_quote(other.id) is not None  # a successful stock is not
    assert yahoo_calls == [stock.id, other.id, other.id]


def _yahoo_quote(stock: StockRecord) -> QuoteRecord:
    return QuoteRecord(
        id=stock.id,
        last_price=1182.0,
        change=77.0,
        change_percent=6.97,
        previous_close=1105.0,
        timestamp=datetime(2026, 7, 13, 10, 40, tzinfo=_TZ).isoformat(),
        is_market_open=True,
    )


def test_yahoo_quote_without_a_trade_time_is_rejected(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    chart = _yahoo_chart()
    del chart["chart"]["result"][0]["meta"]["regularMarketTime"]  # type: ignore[index]
    monkeypatch.setattr(tw_provider, "fetch_json", lambda *_a, **_k: chart)

    assert tw_provider.TWProvider()._fetch_yahoo_tw_quote(stock) is None


def test_stale_yahoo_quote_keeps_its_own_session_and_timestamp(monkeypatch) -> None:
    """A previous-session Yahoo price is labelled with that session, not now."""
    stock = _stock()
    _use_stock(monkeypatch, stock)
    bars = [
        (_ts(7, 13, 30), 1010.0),
        (_ts(8, 13, 30), 1040.0),
        (_ts(9, 13, 30), 1072.0),
        (_ts(10, 13, 30), 1105.0),
    ]
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: _yahoo_chart(price=1105.0, market_time=_ts(10, 13, 30), bars=bars),
    )

    quote = tw_provider.TWProvider()._fetch_yahoo_tw_quote(stock)

    assert quote is not None
    assert quote.timestamp.startswith("2026-07-10T13:30")
    assert quote.last_price == pytest.approx(1105.0)
    assert quote.previous_close == pytest.approx(1072.0)
    assert quote.change == pytest.approx(33.0)
    _assert_coherent(quote)


# --- Yahoo previous close ------------------------------------------------------


def test_yahoo_previous_close_skips_a_missing_today_close(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    bars = [
        (_ts(9, 13, 30), 1072.0),
        (_ts(10, 13, 30), 1105.0),
        (_ts(13, 10, 40), None),
    ]
    monkeypatch.setattr(
        tw_provider, "fetch_json", lambda *_a, **_k: _yahoo_chart(price=1182.0, bars=bars)
    )

    quote = tw_provider.TWProvider()._fetch_yahoo_tw_quote(stock)

    assert quote is not None
    assert quote.previous_close == pytest.approx(1105.0)


def test_yahoo_uses_meta_previous_close_without_bars(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: _yahoo_chart(
            price=1182.0, previous_close=1105.0, chart_previous_close=980.0, bars=[]
        ),
    )

    quote = tw_provider.TWProvider()._fetch_yahoo_tw_quote(stock)

    assert quote is not None
    assert quote.previous_close == pytest.approx(1105.0)


def test_yahoo_chart_previous_close_is_used_only_when_the_window_starts_today(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: _yahoo_chart(
            price=1182.0, chart_previous_close=1105.0, bars=[(_ts(13, 10, 40), 1182.0)]
        ),
    )

    quote = tw_provider.TWProvider()._fetch_yahoo_tw_quote(stock)

    assert quote is not None
    assert quote.previous_close == pytest.approx(1105.0)


def test_yahoo_without_a_verifiable_previous_close_is_rejected(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    bars = [(_ts(10, 13, 30), None), (_ts(13, 10, 40), 1182.0)]
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: _yahoo_chart(price=1182.0, chart_previous_close=980.0, bars=bars),
    )

    assert tw_provider.TWProvider()._fetch_yahoo_tw_quote(stock) is None


# --- Daily chart forming candle -------------------------------------------------


class _DuringSession(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 7, 13, 10, 45, tzinfo=tz or _TZ)


def test_session_candle_does_not_close_at_previous_close_when_trade_is_missing(
    monkeypatch,
) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "datetime", _DuringSession)
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(tw_provider, "fetch_json", lambda *_a, **_k: _yahoo_chart(price=1182.0))

    candles = tw_provider.TWProvider()._merge_live_session_daily_candle(stock, [])

    assert len(candles) == 1
    assert candles[0].close == pytest.approx(1182.0)
    assert candles[0].open == pytest.approx(1160.0)
    assert candles[0].high == pytest.approx(1215.0)


def test_session_candle_is_omitted_when_no_trustworthy_price_exists(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "datetime", _DuringSession)
    monkeypatch.setattr(tw_provider, "is_trading_day", lambda market, now=None: True)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(
        tw_provider,
        "fetch_json",
        lambda *_a, **_k: {"chart": {"result": None, "error": {"code": "Not Found"}}},
    )

    assert tw_provider.TWProvider()._merge_live_session_daily_candle(stock, []) == []


# --- API contract ---------------------------------------------------------------


def test_api_response_carries_the_corrected_non_zero_move(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="-"))
    monkeypatch.setattr(tw_provider, "fetch_json", lambda *_a, **_k: _yahoo_chart(price=1182.0))

    quote = tw_provider.TWProvider().get_quote(stock.id)
    assert quote is not None
    response = quote_to_response(quote)

    assert response.lastPrice == pytest.approx(1182.0)
    assert response.previousClose == pytest.approx(1105.0)
    assert response.change == pytest.approx(77.0)
    assert response.changePercent == pytest.approx(6.97)
    assert response.lastPrice != response.previousClose
