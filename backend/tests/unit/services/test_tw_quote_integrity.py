"""Taiwan live quote integrity (#258).

MIS ``y`` is the previous/reference close. It must never become ``last_price``
because the latest-trade field ``z`` is missing, and ``change`` /
``change_percent`` must always come from the same price pair that is returned.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.stock import StockRecord
from app.schemas.quote import quote_to_response
from app.services.providers import tw_provider

_TZ = ZoneInfo("Asia/Taipei")


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


def test_previous_trade_inside_the_session_range_is_used(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", pz="1175.00")
    )

    quote = tw_provider.TWProvider()._fetch_quote(stock)

    assert quote is not None
    assert quote.last_price == pytest.approx(1175.0)
    assert quote.change == pytest.approx(70.0)
    _assert_coherent(quote)
    assert quote.price_limit_status == "unknown"


@pytest.mark.parametrize(
    "overrides",
    [
        {"pz": "1105.00"},  # equals previous close, outside the session range
        {"pz": "1175.00", "v": "0"},  # no session volume
        {"pz": "1175.00", "v": "-"},
        {"pz": "1175.00", "h": "-", "l": "-"},  # no session range
    ],
)
def test_uncorroborated_previous_trade_is_rejected(monkeypatch, overrides: dict[str, str]) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider, "_load_json", lambda *_a, **_k: _mis_2368(z="-", **overrides)
    )

    assert tw_provider.TWProvider()._fetch_quote(stock) is None


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


def test_limit_up_with_corroborated_previous_trade_stays_unclassified(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: _mis_2368(z="-", pz="1215.00", o="1200.00", h="1215.00", l="1195.00"),
    )

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.last_price == pytest.approx(1215.0)
    assert quote.change == pytest.approx(110.0)
    assert quote.price_limit_status == "unknown"
    assert quote.limit_up_price == pytest.approx(1215.0)


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


def test_mis_timestamp_and_market_status_come_from_the_snapshot(monkeypatch) -> None:
    stock = _stock()
    _use_stock(monkeypatch, stock)
    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: _mis_2368())

    quote = tw_provider.TWProvider().get_quote(stock.id)

    assert quote is not None
    assert quote.timestamp == datetime(2026, 7, 13, 10, 42, 15, tzinfo=_TZ).isoformat()
    assert quote.is_market_open is True


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
