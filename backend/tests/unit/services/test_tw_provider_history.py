from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord, QuoteRecord
from app.services.http_client import HttpFetchError
from app.services.providers import tw_provider
from tests.support import stock_record


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


def _tpex_row(minguo_day: str, lots: str = "4,452") -> list[str]:
    return [
        minguo_day,
        lots,
        "4,333,718",
        "1,015.00",
        "1,040.00",
        "936.00",
        "950.00",
        "-65.00",
        "6,880",
    ]


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
    return tw_provider.TWProvider()


def test_get_history_1d_uses_quote_candles_for_twse_and_tpex(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_quote",
        lambda self, stock: _quote(stock.id),
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_daily_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("daily fetch should not run for 1D")),
    )

    for stock_id in (TWSE_STOCK.id, TPEX_STOCK.id):
        candles = provider.get_history(stock_id, "1D")
        assert candles
        assert all(isinstance(item, CandleRecord) for item in candles)


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
    assert candles[0].open == 1015.0
    assert candles[0].high == 1040.0
    assert candles[0].low == 936.0
    assert candles[0].close == 950.0
    assert candles[0].volume == 4_452_000


def test_parse_tpex_daily_rows_sorts_and_dedupes(
    provider: tw_provider.TWProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tpex(self, stock, month_date: date) -> list[list[object]]:
        return [
            _tpex_row("115/07/03"),
            _tpex_row("115/07/01"),
            _tpex_row("115/07/02", lots="1,000"),
            _tpex_row("115/07/02", lots="2,000"),
            ["bad"],
            "not-a-row",
        ]

    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_tpex_month_rows", fake_tpex)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_twse_month_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
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


def test_tpex_volume_uses_taiwan_shares_per_lot_for_fx_etfs(
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
        lambda self, stock, month_date: [_tpex_row("115/07/01", lots="10")],
    )
    _freeze_provider_clock(monkeypatch)

    candles = provider.get_history(fx_etf.id, "1W")

    assert candles
    assert candles[0].volume == 1_000  # 10 lots * 100 shares/lot


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
