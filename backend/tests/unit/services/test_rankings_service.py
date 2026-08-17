from datetime import date, datetime, timezone
import threading
from types import SimpleNamespace

import pytest

from app.schemas.rankings import RankingSourceType, RankingType
from app.services import quote_redis_cache as quote_cache
from app.services import rankings_service as rankings_service_module
from app.services.quote_service import QuoteService
from app.services.rankings_service import (
    TW_TPEX_RANKING_STOCK_IDS,
    TW_TWSE_RANKING_STOCK_IDS,
    RankingsService,
)
from app.services.rankings_volume_baseline_cache import InMemoryVolumeBaselineStore
from tests.support import (
    age_memory_cached_quote,
    overlapping_lookup,
    quote_record,
    stock_record,
)


def _empty_sp500_quotes(_stocks):
    """Bypass S&P 500 batched quotes so US mover tests hit quote or Yahoo screener paths."""
    return {}


class _NullExternalSource:
    """External source stub that pretends Yahoo is unavailable.

    Tests that exercise the US S&P 500 quote snapshot path get this by default
    via the ``_disable_external_source`` autouse fixture, so they never hit the
    real Yahoo network or a Redis cache.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, RankingType]] = []

    def fetch(self, market, ranking_type, limit, now=None, tw_board=None):  # noqa: ARG002
        self.calls.append((market, ranking_type))
        return None


@pytest.fixture(autouse=True)
def _disable_external_source(monkeypatch):
    """Swap the module-level external source for a null stub.

    Most US rankings tests want to assert the S&P 500 quote snapshot path. Without
    this the service would silently short-circuit through the live Yahoo fetcher.
    Tests that need to drive the external path inject their own source via
    ``RankingsService(external_source=...)``.
    """
    monkeypatch.setattr(
        rankings_service_module,
        "rankings_external_source",
        _NullExternalSource(),
    )


class FakeQuoteSource:
    def __init__(
        self,
        stocks: dict[str, object],
        quotes: dict[str, object],
        live_quotes: dict[str, object] | None = None,
    ) -> None:
        self._stocks = stocks
        self._quotes = quotes
        self._live_quotes = live_quotes if live_quotes is not None else quotes

    def get_quotes(self, stock_ids: list[str]):
        envelopes = []
        for stock_id in stock_ids:
            stock = self._stocks.get(stock_id)
            quote = self._quotes.get(stock_id)
            if stock is not None and quote is not None:
                envelopes.append((stock, quote))
        return envelopes

    def get_live_quotes(self, stock_ids: list[str]):
        envelopes = []
        for stock_id in stock_ids:
            stock = self._stocks.get(stock_id)
            quote = self._live_quotes.get(stock_id)
            if stock is not None and quote is not None:
                envelopes.append((stock, quote))
        return envelopes

    def get_stock(self, stock_id: str):
        return self._stocks.get(stock_id)


class StaticExternalSource:
    def __init__(self, envelopes):
        self._envelopes = envelopes
        self.calls: list[tuple[str, RankingType, int, str | None]] = []

    def fetch(self, market, ranking_type, limit, now=None, tw_board=None):  # noqa: ARG002
        self.calls.append((market, ranking_type, limit, tw_board))
        envelopes = self._envelopes
        if market == "TW" and tw_board in {"TWSE", "TPEx"}:
            envelopes = [pair for pair in envelopes if pair[0].exchange == tw_board]
        return envelopes[:limit]


def _tw_stock(stock_id: str, symbol: str, name: str, *, exchange: str = "TWSE"):
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=name,
        exchange=exchange,
        currency="TWD",
        timezone="Asia/Taipei",
        name_en=name,
        name_zh_tw=name,
    )


def _us_stock(stock_id: str, symbol: str, name: str):
    return stock_record(
        stock_id,
        market="US",
        symbol=symbol,
        name=name,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en=name,
    )


def _quote(
    stock_id: str,
    volume: int,
    timestamp: str,
    *,
    change: float = 1.0,
    change_percent: float = 1.0,
    last_price: float = 100.0,
    previous_close: float = 99.0,
):
    return quote_record(
        stock_id,
        last_price=last_price,
        change=change,
        change_percent=change_percent,
        previous_close=previous_close,
        timestamp=timestamp,
        is_market_open=True,
        volume=volume,
    )


_TW_QUOTE_NAMES = {
    "TW:2330": ("2330", "台積電"),
    "TW:2317": ("2317", "鴻海"),
    "TW:2454": ("2454", "聯發科"),
}
_CACHE_ITEM_DEFAULT = object()


def _tw_quote_source(last_prices: dict[str, float]) -> FakeQuoteSource:
    stocks = {}
    quotes = {}
    for index, (stock_id, last_price) in enumerate(last_prices.items(), start=1):
        symbol, name = _TW_QUOTE_NAMES[stock_id]
        stocks[stock_id] = _tw_stock(stock_id, symbol, name)
        quotes[stock_id] = _quote(
            stock_id,
            index * 1000,
            "2026-05-12T10:00:00+08:00",
            last_price=last_price,
        )
    return FakeQuoteSource(stocks=stocks, quotes=quotes)


def _tw_net_buy_service(quote_source, rows: dict[str, int]) -> RankingsService:
    service = RankingsService(quote_source)
    service._fetch_tw_institutional_snapshot = (  # type: ignore[method-assign]
        lambda now, tw_board="TWSE", bound_rows=rows: (date(2026, 5, 11), bound_rows)
    )
    return service


def _track_get_quotes(quote_source) -> list[list[str]]:
    calls: list[list[str]] = []
    original = quote_source.get_quotes

    def tracking(stock_ids: list[str]):
        calls.append(list(stock_ids))
        return original(stock_ids)

    quote_source.get_quotes = tracking  # type: ignore[method-assign]
    return calls


def _install_cache_first(quote_source, builder) -> list[list[str]]:
    calls: list[list[str]] = []

    def cache_first(stock_ids: list[str]):
        calls.append(list(stock_ids))
        return builder(quote_source, stock_ids)

    quote_source.get_quotes_cache_first = cache_first  # type: ignore[method-assign]
    return calls


def _cached_item(quote_source, stock_id: str, *, quote=_CACHE_ITEM_DEFAULT, stale: bool = False):
    resolved = quote_source._quotes[stock_id] if quote is _CACHE_ITEM_DEFAULT else quote
    return SimpleNamespace(stock=quote_source.get_stock(stock_id), quote=resolved, stale=stale)


def _valuation_snapshot(
    pe_ratio: float | None,
    *,
    pe_ratio_type: str | None = "official_daily",
    pe_ratio_source: str | None = "TWSE official daily P/E",
    valuation_as_of: str | None = "2026-07-03",
    valuation_warning: str | None = None,
):
    return {
        "peRatio": pe_ratio,
        "peRatioType": pe_ratio_type,
        "peRatioSource": pe_ratio_source,
        "valuationAsOf": valuation_as_of,
        "valuationWarning": valuation_warning,
    }


def test_volume_ranking_sorts_by_volume_descending() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
                "US:NVDA": _us_stock("US:NVDA", "NVDA", "NVIDIA Corporation"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00"),
                "US:MSFT": _quote("US:MSFT", 300, "2026-05-12T10:00:00-04:00"),
                "US:NVDA": _quote("US:NVDA", 200, "2026-05-12T10:00:00-04:00"),
            },
        ),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.volume, limit=3)

    assert [item.stock.symbol for item in response.items] == ["MSFT", "NVDA", "AAPL"]
    assert response.sourceType == RankingSourceType.free_unofficial
    assert response.usRankingsScope == "quote"


def test_volume_up_ranking_uses_existing_baseline() -> None:
    volume_baselines = InMemoryVolumeBaselineStore()
    volume_baselines.set("US:AAPL", 100)
    volume_baselines.set("US:MSFT", 100)
    volume_baselines.set("US:NVDA", 100)
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
                "US:NVDA": _us_stock("US:NVDA", "NVDA", "NVIDIA Corporation"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 150, "2026-05-12T10:00:00-04:00"),
                "US:MSFT": _quote("US:MSFT", 120, "2026-05-12T10:00:00-04:00"),
                "US:NVDA": _quote("US:NVDA", 200, "2026-05-12T10:00:00-04:00"),
            },
        ),
        volume_baseline_store=volume_baselines,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.volume_up, limit=3)

    assert [item.stock.symbol for item in response.items] == ["NVDA", "AAPL", "MSFT"]
    assert [item.volumeDeltaPercent for item in response.items] == [100.0, 50.0, 20.0]


def test_volume_up_ranks_zero_delta_above_negative_deltas() -> None:
    volume_baselines = InMemoryVolumeBaselineStore()
    volume_baselines.set("US:AAPL", 100)
    volume_baselines.set("US:MSFT", 100)
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00"),
                "US:MSFT": _quote("US:MSFT", 95, "2026-05-12T10:00:00-04:00"),
            },
        ),
        volume_baseline_store=volume_baselines,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.volume_up, limit=2)

    assert [item.stock.symbol for item in response.items] == ["AAPL", "MSFT"]
    assert [item.volumeDeltaPercent for item in response.items] == [0.0, -5.0]


def test_volume_up_returns_null_delta_when_baseline_is_missing() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 150, "2026-05-12T10:00:00-04:00")},
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.volume_up, limit=1)

    assert response.items == []
    assert "Baseline unavailable" in response.dataFreshnessLabel


def test_volume_down_sorts_by_largest_decrease_first() -> None:
    volume_baselines = InMemoryVolumeBaselineStore()
    volume_baselines.set("US:AAPL", 200)
    volume_baselines.set("US:MSFT", 100)
    volume_baselines.set("US:NVDA", 300)
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
                "US:NVDA": _us_stock("US:NVDA", "NVDA", "NVIDIA Corporation"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00"),
                "US:MSFT": _quote("US:MSFT", 90, "2026-05-12T10:00:00-04:00"),
                "US:NVDA": _quote("US:NVDA", 120, "2026-05-12T10:00:00-04:00"),
            },
        ),
        volume_baseline_store=volume_baselines,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.volume_down, limit=3)

    assert [item.stock.symbol for item in response.items] == ["NVDA", "AAPL", "MSFT"]
    assert [item.volumeDeltaPercent for item in response.items] == [-60.0, -50.0, -10.0]


def test_change_up_sorts_by_highest_change_percent_descending() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
                "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
                "TW:2454": _tw_stock("TW:2454", "2454", "聯發科"),
            },
            quotes={
                "TW:2330": _quote(
                    "TW:2330",
                    1000,
                    "2026-05-12T10:00:00+08:00",
                    change=8.0,
                    change_percent=8.0,
                    last_price=108.0,
                    previous_close=100.0,
                ),
                "TW:2317": _quote(
                    "TW:2317",
                    1200,
                    "2026-05-12T10:00:00+08:00",
                    change=3.5,
                    change_percent=3.5,
                    last_price=103.5,
                    previous_close=100.0,
                ),
                "TW:2454": _quote(
                    "TW:2454",
                    1500,
                    "2026-05-12T10:00:00+08:00",
                    change=-2.0,
                    change_percent=-2.0,
                    last_price=98.0,
                    previous_close=100.0,
                ),
            },
        )
    )

    response = service.get_rankings("TW", RankingType.change_up, limit=3)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert [item.changePercent for item in response.items] == [8.0, 3.5]
    assert response.sourceType == RankingSourceType.free_unofficial


def test_change_down_sorts_by_largest_drop_first() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
                "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
                "TW:2454": _tw_stock("TW:2454", "2454", "聯發科"),
            },
            quotes={
                "TW:2330": _quote(
                    "TW:2330",
                    1000,
                    "2026-05-12T10:00:00+08:00",
                    change=-7.2,
                    change_percent=-7.2,
                    last_price=92.8,
                    previous_close=100.0,
                ),
                "TW:2317": _quote(
                    "TW:2317",
                    1200,
                    "2026-05-12T10:00:00+08:00",
                    change=-1.5,
                    change_percent=-1.5,
                    last_price=98.5,
                    previous_close=100.0,
                ),
                "TW:2454": _quote(
                    "TW:2454",
                    1500,
                    "2026-05-12T10:00:00+08:00",
                    change=2.5,
                    change_percent=2.5,
                    last_price=102.5,
                    previous_close=100.0,
                ),
            },
        )
    )

    response = service.get_rankings("TW", RankingType.change_down, limit=3)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert [item.changePercent for item in response.items] == [-7.2, -1.5]


def test_twse_low_pe_rankings_sort_by_pe_ratio_ascending(monkeypatch) -> None:
    external_source = StaticExternalSource(
        [
            (
                _tw_stock("TW:2330", "2330", "台積電"),
                _quote("TW:2330", 310000, "2026-07-03T13:20:00+08:00", last_price=1010.0),
            ),
            (
                _tw_stock("TW:2454", "2454", "聯發科"),
                _quote("TW:2454", 220000, "2026-07-03T13:20:00+08:00", last_price=1280.0),
            ),
            (
                _tw_stock("TW:2303", "2303", "聯電"),
                _quote("TW:2303", 450000, "2026-07-03T13:20:00+08:00", last_price=58.0),
            ),
        ]
    )
    valuations = {
        "TW:2330": _valuation_snapshot(18.5),
        "TW:2454": _valuation_snapshot(25.2),
        "TW:2303": _valuation_snapshot(12.1),
    }
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
    )
    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        external_source=external_source,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("TW", RankingType.valuation_low_pe, limit=3, tw_board="TWSE")

    assert external_source.calls == [("TW", RankingType.volume, 100, "TWSE")]
    assert response.twBoard == "TWSE"
    assert [item.stock.symbol for item in response.items] == ["2303", "2330", "2454"]
    assert [item.peRatio for item in response.items] == [12.1, 18.5, 25.2]
    assert response.source == (
        "Valuation rankings use the highest-volume current quote candidates, then sort by "
        "P/E from the app's available valuation sources."
    )
    assert response.twRankingsScope == "yahoo"


def test_tpex_low_pe_rankings_respect_requested_board(monkeypatch) -> None:
    external_source = StaticExternalSource(
        [
            (
                _tw_stock("TW:6488", "6488", "環球晶", exchange="TPEx"),
                _quote("TW:6488", 180000, "2026-07-03T13:20:00+08:00", last_price=480.0),
            ),
            (
                _tw_stock("TW:8299", "8299", "群聯", exchange="TPEx"),
                _quote("TW:8299", 90000, "2026-07-03T13:20:00+08:00", last_price=620.0),
            ),
            (
                _tw_stock("TW:2330", "2330", "台積電"),
                _quote("TW:2330", 500000, "2026-07-03T13:20:00+08:00", last_price=1010.0),
            ),
        ]
    )
    valuations = {
        "TW:6488": _valuation_snapshot(14.2, pe_ratio_source="TPEx official daily P/E"),
        "TW:8299": _valuation_snapshot(20.8, pe_ratio_source="TPEx official daily P/E"),
        "TW:2330": _valuation_snapshot(18.5),
    }
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
    )
    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        external_source=external_source,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("TW", RankingType.valuation_low_pe, limit=5, tw_board="TPEx")

    assert external_source.calls == [("TW", RankingType.volume, 100, "TPEx")]
    assert response.twBoard == "TPEx"
    assert [item.stock.symbol for item in response.items] == ["6488", "8299"]
    assert all(item.stock.exchange == "TPEx" for item in response.items)
    assert [item.peRatioSource for item in response.items] == [
        "TPEx official daily P/E",
        "TPEx official daily P/E",
    ]


def test_us_low_pe_rankings_exclude_missing_or_invalid_pe_rows(monkeypatch) -> None:
    external_source = StaticExternalSource(
        [
            (
                _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                _quote("US:AAPL", 9000000, "2026-07-03T10:10:00-04:00", last_price=210.0),
            ),
            (
                _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
                _quote("US:MSFT", 8000000, "2026-07-03T10:10:00-04:00", last_price=480.0),
            ),
            (
                _us_stock("US:XLK", "XLK", "Technology Select Sector SPDR Fund"),
                _quote("US:XLK", 7000000, "2026-07-03T10:10:00-04:00", last_price=240.0),
            ),
            (
                _us_stock("US:TSLA", "TSLA", "Tesla, Inc."),
                _quote("US:TSLA", 6000000, "2026-07-03T10:10:00-04:00", last_price=315.0),
            ),
        ]
    )
    valuations = {
        "US:AAPL": _valuation_snapshot(
            22.4,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Yahoo Finance trailing P/E",
        ),
        "US:MSFT": _valuation_snapshot(
            31.0,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Yahoo Finance trailing P/E",
        ),
        "US:XLK": _valuation_snapshot(
            None,
            pe_ratio_type=None,
            pe_ratio_source=None,
            valuation_as_of=None,
            valuation_warning="P/E is not reported for ETF or fund instruments.",
        ),
        "US:TSLA": _valuation_snapshot(
            0.0,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Yahoo Finance trailing P/E",
        ),
    }
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
    )
    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        external_source=external_source,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.valuation_low_pe, limit=5)

    assert external_source.calls == [("US", RankingType.volume, 100, None)]
    assert response.usRankingsScope == "yahoo"
    assert [item.stock.symbol for item in response.items] == ["AAPL", "MSFT"]
    assert [item.peRatio for item in response.items] == [22.4, 31.0]


def test_tw_low_pe_rankings_exclude_etf_from_universe(monkeypatch) -> None:
    external_source = StaticExternalSource(
        [
            (
                _tw_stock("TW:0050", "0050", "元大台灣50"),
                _quote("TW:0050", 500000, "2026-07-03T13:20:00+08:00", last_price=180.0),
            ),
            (
                _tw_stock("TW:2330", "2330", "台積電"),
                _quote("TW:2330", 310000, "2026-07-03T13:20:00+08:00", last_price=1010.0),
            ),
        ]
    )
    valuations = {
        "TW:0050": _valuation_snapshot(
            None,
            valuation_warning="P/E is not reported for ETF or fund instruments.",
        ),
        "TW:2330": _valuation_snapshot(20.0),
    }
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
    )
    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        external_source=external_source,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("TW", RankingType.valuation_low_pe, limit=5, tw_board="TWSE")

    assert [item.stock.symbol for item in response.items] == ["2330"]
    assert response.items[0].peRatio == 20.0


def test_us_low_pe_rankings_exclude_negative_eps_rows(monkeypatch) -> None:
    external_source = StaticExternalSource(
        [
            (
                _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                _quote("US:AAPL", 9000000, "2026-07-03T10:10:00-04:00", last_price=210.0),
            ),
            (
                _us_stock("US:XYZ", "XYZ", "Example Corp."),
                _quote("US:XYZ", 8000000, "2026-07-03T10:10:00-04:00", last_price=12.0),
            ),
        ]
    )
    valuations = {
        "US:AAPL": _valuation_snapshot(
            22.4,
            pe_ratio_type="provider_ttm",
            pe_ratio_source="Yahoo Finance trailing P/E",
        ),
        "US:XYZ": _valuation_snapshot(
            None,
            pe_ratio_type=None,
            pe_ratio_source=None,
            valuation_warning="P/E could not be calculated because EPS TTM was missing or non-positive.",
        ),
    }
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
    )
    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        external_source=external_source,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.valuation_low_pe, limit=5)

    assert [item.stock.symbol for item in response.items] == ["AAPL"]


def test_low_pe_rankings_tie_break_equal_pe_by_higher_volume(monkeypatch) -> None:
    external_source = StaticExternalSource(
        [
            (
                _tw_stock("TW:2330", "2330", "台積電"),
                _quote("TW:2330", 100000, "2026-07-03T13:20:00+08:00", last_price=1010.0),
            ),
            (
                _tw_stock("TW:2303", "2303", "聯電"),
                _quote("TW:2303", 300000, "2026-07-03T13:20:00+08:00", last_price=58.0),
            ),
        ]
    )
    valuations = {
        "TW:2330": _valuation_snapshot(15.0),
        "TW:2303": _valuation_snapshot(15.0),
    }
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
    )
    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        external_source=external_source,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("TW", RankingType.valuation_low_pe, limit=5, tw_board="TWSE")

    assert [item.stock.symbol for item in response.items] == ["2303", "2330"]
    assert [item.volume for item in response.items] == [300000, 100000]


def test_positive_float_rejects_non_finite_values() -> None:
    from app.services.rankings_service import _positive_float

    assert _positive_float(float("nan")) is None
    assert _positive_float(float("inf")) is None


def test_change_down_falls_back_to_only_negative_movers_in_quote_path() -> None:
    """When Yahoo is unavailable, losers tab must not list gainers."""
    external = _StubExternalSource({})

    service = RankingsService(
        quote_source=FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
                "US:NVDA": _us_stock("US:NVDA", "NVDA", "NVIDIA Corporation"),
            },
            quotes={
                "US:AAPL": _quote(
                    "US:AAPL",
                    100,
                    "2026-05-12T10:00:00-04:00",
                    change_percent=3.0,
                    change=3.0,
                    last_price=103.0,
                    previous_close=100.0,
                ),
                "US:MSFT": _quote(
                    "US:MSFT",
                    200,
                    "2026-05-12T10:00:00-04:00",
                    change_percent=-2.0,
                    change=-2.0,
                    last_price=98.0,
                    previous_close=100.0,
                ),
                "US:NVDA": _quote(
                    "US:NVDA",
                    300,
                    "2026-05-12T10:00:00-04:00",
                    change_percent=-5.0,
                    change=-5.0,
                    last_price=95.0,
                    previous_close=100.0,
                ),
            },
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.change_down,
        limit=50,
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
    )

    assert [item.stock.symbol for item in response.items] == ["NVDA", "MSFT"]
    assert all(item.changePercent is not None and item.changePercent < 0 for item in response.items)
    assert response.usRankingsScope == "quote"


def test_limit_parameter_truncates_rankings() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00"),
                "US:MSFT": _quote("US:MSFT", 300, "2026-05-12T10:00:00-04:00"),
            },
        ),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings("US", RankingType.volume, limit=1)

    assert len(response.items) == 1
    assert response.items[0].stock.symbol == "MSFT"
    assert response.usRankingsScope == "quote"


def test_us_quote_rankings_exclude_symbols_without_live_quotes() -> None:
    fake = FakeQuoteSource(
        stocks={
            "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
            "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
            "US:NVDA": _us_stock("US:NVDA", "NVDA", "NVIDIA Corporation"),
        },
        quotes={
            "US:AAPL": _quote("US:AAPL", 999, "2026-05-12T10:00:00-04:00"),
            "US:MSFT": _quote("US:MSFT", 300, "2026-05-12T10:00:00-04:00"),
            "US:NVDA": _quote("US:NVDA", 200, "2026-05-12T10:00:00-04:00"),
        },
        live_quotes={
            "US:MSFT": _quote("US:MSFT", 300, "2026-05-12T10:00:00-04:00"),
            "US:NVDA": _quote("US:NVDA", 200, "2026-05-12T10:00:00-04:00"),
        },
    )

    def batch(stocks):
        return {s.id: q for s in stocks for q in [fake._live_quotes.get(s.id)] if q is not None}

    service = RankingsService(
        quote_source=fake,
        sp500_symbol_loader=lambda: ("AAPL", "MSFT", "NVDA"),
        sp500_quote_batch=batch,
    )

    response = service.get_rankings("US", RankingType.volume, limit=3)

    assert [item.stock.symbol for item in response.items] == ["MSFT", "NVDA"]


def test_market_closed_label_is_exposed_on_quote_rankings() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00")},
        ),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.volume,
        limit=1,
        now=datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert response.isMarketOpen is False
    assert "Market closed" in response.dataFreshnessLabel


def test_change_up_uses_price_move_freshness_label_when_market_open() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00")},
        ),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.change_up,
        limit=1,
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
    )

    assert response.isMarketOpen is True
    assert response.dataFreshnessLabel == (
        "Market open; price-move rankings use the latest quote snapshot."
    )
    assert response.usRankingsScope == "quote"


def test_change_down_uses_price_move_freshness_label_when_market_closed() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={
                "US:AAPL": _quote(
                    "US:AAPL",
                    100,
                    "2026-05-12T10:00:00-04:00",
                    change=-1.0,
                    change_percent=-1.0,
                    last_price=99.0,
                    previous_close=100.0,
                ),
            },
        ),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.change_down,
        limit=1,
        now=datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert response.isMarketOpen is False
    assert len(response.items) == 1
    assert response.items[0].stock.symbol == "AAPL"
    assert response.dataFreshnessLabel == (
        "Market closed; price-move rankings use the latest available quote snapshot."
    )
    assert response.usRankingsScope == "quote"


class _StubExternalSource:
    """Returns canned envelopes in a fixed order, in the spirit of Yahoo's pre-sorted output."""

    def __init__(self, envelopes_by_key: dict[tuple[str, RankingType], list]) -> None:
        self._envelopes_by_key = envelopes_by_key
        self.calls: list[tuple[str, RankingType, int, str | None]] = []

    def fetch(self, market, ranking_type, limit, now=None, tw_board=None):  # noqa: ARG002
        self.calls.append((market, ranking_type, limit, tw_board))
        envelopes = self._envelopes_by_key.get((market, ranking_type))
        if envelopes is None:
            return None
        return envelopes[:limit]


def test_volume_rankings_use_external_source_when_available() -> None:
    yahoo_envelopes = [
        (
            _tw_stock("TW:9999", "9999", "Yahoo-only"),
            _quote("TW:9999", 100, "2026-05-12T10:00:00+08:00"),
        ),
        (
            _tw_stock("TW:2330", "2330", "台積電"),
            _quote("TW:2330", 50, "2026-05-12T10:00:00+08:00"),
        ),
    ]
    external = _StubExternalSource({("TW", RankingType.volume): yahoo_envelopes})

    quote_source = FakeQuoteSource(
        stocks={
            # Curated stock that should NOT appear because the external source
            # short-circuits before the fan-out runs.
            "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
        },
        quotes={
            "TW:2317": _quote("TW:2317", 999_999, "2026-05-12T10:00:00+08:00"),
        },
    )
    service = RankingsService(
        quote_source=quote_source,
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
    )

    response = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=10,
        now=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
    )

    assert [item.stock.symbol for item in response.items] == ["9999", "2330"]
    assert "2317" not in [item.stock.symbol for item in response.items]
    assert external.calls == [("TW", RankingType.volume, 10, "TWSE")]
    assert response.source == "Yahoo Finance pre-ranked quote rankings."
    assert response.usRankingsScope is None
    assert response.twRankingsScope == "yahoo"
    assert response.twBoard == "TWSE"
    assert response.dataFreshnessLabel == (
        "Market open; volume rankings come from Yahoo Finance and refresh every few minutes."
    )


def test_tw_tpex_volume_rankings_preserve_requested_board_in_external_response() -> None:
    yahoo_envelopes = [
        (
            _tw_stock("TW:3293", "3293", "鈊象", exchange="TPEx"),
            _quote("TW:3293", 100, "2026-05-12T10:00:00+08:00"),
        ),
        (
            _tw_stock("TW:6488", "6488", "環球晶", exchange="TPEx"),
            _quote("TW:6488", 50, "2026-05-12T10:00:00+08:00"),
        ),
    ]
    external = _StubExternalSource({("TW", RankingType.volume): yahoo_envelopes})

    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
    )

    response = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=10,
        now=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
        tw_board="TPEx",
    )

    assert [item.stock.symbol for item in response.items] == ["3293", "6488"]
    assert response.twBoard == "TPEx"
    assert response.twRankingsScope == "yahoo"
    assert all(item.stock.exchange == "TPEx" for item in response.items)
    assert external.calls == [("TW", RankingType.volume, 10, "TPEx")]


def test_change_up_rankings_use_external_source_in_yahoo_order() -> None:
    yahoo_envelopes = [
        (
            _us_stock("US:AAA", "AAA", "Top Gainer"),
            _quote("US:AAA", 1000, "2026-05-12T10:00:00-04:00", change_percent=12.5),
        ),
        (
            _us_stock("US:BBB", "BBB", "Second Gainer"),
            _quote("US:BBB", 1000, "2026-05-12T10:00:00-04:00", change_percent=9.3),
        ),
    ]
    external = _StubExternalSource({("US", RankingType.change_up): yahoo_envelopes})

    service = RankingsService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.change_up,
        limit=5,
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
    )

    assert [item.stock.symbol for item in response.items] == ["AAA", "BBB"]
    assert external.calls == [("US", RankingType.change_up, 5, None)]
    assert response.usRankingsScope == "yahoo"
    assert response.dataFreshnessLabel == (
        "Market open; price-move rankings come from Yahoo Finance and refresh every few minutes."
    )


def test_us_sp500_change_up_uses_constituent_batch_before_yahoo_screener() -> None:
    """US change_up ranks S&P 500 snapshot + batched quotes and skips Yahoo screeners."""
    ts = "2026-05-12T10:00:00-04:00"
    external = _StubExternalSource({("US", RankingType.change_up): []})

    def sym_loader() -> tuple[str, ...]:
        return ("WIN1", "LOSE1", "FLAT1")

    def batch(stocks):
        out = {}
        for s in stocks:
            if s.symbol == "WIN1":
                out[s.id] = _quote(
                    s.id,
                    1000,
                    ts,
                    change_percent=7.0,
                    change=7.0,
                    last_price=107.0,
                    previous_close=100.0,
                )
            elif s.symbol == "LOSE1":
                out[s.id] = _quote(
                    s.id,
                    1000,
                    ts,
                    change_percent=-4.0,
                    change=-4.0,
                    last_price=96.0,
                    previous_close=100.0,
                )
            elif s.symbol == "FLAT1":
                out[s.id] = _quote(
                    s.id,
                    1000,
                    ts,
                    change_percent=0.0,
                    change=0.0,
                    last_price=100.0,
                    previous_close=100.0,
                )
        return out

    service = RankingsService(
        quote_source=FakeQuoteSource(
            stocks={
                "US:WIN1": _us_stock("US:WIN1", "WIN1", "Win One"),
                "US:LOSE1": _us_stock("US:LOSE1", "LOSE1", "Lose One"),
                "US:FLAT1": _us_stock("US:FLAT1", "FLAT1", "Flat One"),
            },
            quotes={},
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
        sp500_symbol_loader=sym_loader,
        sp500_quote_batch=batch,
    )

    response = service.get_rankings(
        "US",
        RankingType.change_up,
        limit=10,
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
    )

    assert [item.stock.symbol for item in response.items] == ["WIN1"]
    assert external.calls == []
    assert "S&P 500" in response.source
    assert response.usRankingsScope == "sp500"
    assert "S&P 500 movers ranked" in response.dataFreshnessLabel


def test_rankings_fall_back_to_curated_when_external_source_returns_none() -> None:
    external = _StubExternalSource({})  # always returns None

    service = RankingsService(
        quote_source=FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00")},
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.volume,
        limit=10,
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
    )

    assert [item.stock.symbol for item in response.items] == ["AAPL"]
    assert response.source == "Quote rankings from the app's free best-effort quote providers."
    assert response.usRankingsScope == "quote"
    assert response.dataFreshnessLabel == (
        "Market open; rankings use the latest quote snapshot."
    )


def test_volume_up_rankings_never_call_external_source() -> None:
    external = _StubExternalSource({})

    service = RankingsService(
        quote_source=FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00")},
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=external,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    service.get_rankings(
        "US",
        RankingType.volume_up,
        limit=10,
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
    )

    assert external.calls == [], "volume_up is our custom metric; should not query Yahoo"


def _hardcoded_tw_quote_source(stock_ids: list[str]) -> FakeQuoteSource:
    stocks = {}
    quotes = {}
    for index, stock_id in enumerate(stock_ids):
        symbol = stock_id.split(":", 1)[1]
        exchange = "TPEx" if stock_id in TW_TPEX_RANKING_STOCK_IDS else "TWSE"
        stocks[stock_id] = _tw_stock(stock_id, symbol, symbol, exchange=exchange)
        quotes[stock_id] = _quote(
            stock_id,
            volume=10_000 * (len(stock_ids) - index),
            timestamp="2026-05-12T10:00:00+08:00",
        )
    return FakeQuoteSource(stocks=stocks, quotes=quotes)


def _hardcoded_tw_rankings_service(
    stock_ids: list[str],
    *,
    volume_baseline_store=None,
    external_source=None,
    sp500_quote_batch=None,
) -> RankingsService:
    return RankingsService(
        quote_source=_hardcoded_tw_quote_source(stock_ids),
        volume_baseline_store=volume_baseline_store or InMemoryVolumeBaselineStore(),
        external_source=external_source if external_source is not None else _StubExternalSource({}),
        sp500_quote_batch=sp500_quote_batch,
    )


_TW_OPEN_RANKINGS_NOW = datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("ranking_type", "tw_board", "stock_ids"),
    [
        (RankingType.volume, "TWSE", TW_TWSE_RANKING_STOCK_IDS),
        (RankingType.change_up, "TWSE", TW_TWSE_RANKING_STOCK_IDS),
        (RankingType.volume, "TPEx", TW_TPEX_RANKING_STOCK_IDS),
        (RankingType.valuation_low_pe, "TWSE", TW_TWSE_RANKING_STOCK_IDS),
    ],
)
def test_tw_quote_rankings_mark_hardcoded_universe_as_fallback_when_yahoo_missing(
    ranking_type: RankingType,
    tw_board: str,
    stock_ids: list[str],
    monkeypatch,
) -> None:
    if ranking_type == RankingType.valuation_low_pe:
        valuations = {
            stock_id: _valuation_snapshot(10.0 + index)
            for index, stock_id in enumerate(stock_ids)
        }
        monkeypatch.setattr(
            rankings_service_module,
            "get_valuation_snapshots",
            lambda envelopes: {stock.id: valuations[stock.id] for stock, _ in envelopes},  # noqa: ARG005
        )
    external = _StubExternalSource({})
    service = _hardcoded_tw_rankings_service(
        stock_ids,
        external_source=external,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "TW",
        ranking_type,
        limit=10,
        now=_TW_OPEN_RANKINGS_NOW,
        tw_board=tw_board,
    )

    item_ids = [item.stock.id for item in response.items]
    assert item_ids
    assert set(item_ids) <= set(stock_ids)
    assert response.twRankingsScope == "fallback"
    assert response.source == rankings_service_module._TW_QUOTE_FALLBACK_SOURCE
    assert response.warningMessage == rankings_service_module._TW_QUOTE_FALLBACK_WARNING
    assert response.dataFreshnessLabel == rankings_service_module._TW_QUOTE_FALLBACK_FRESHNESS_OPEN
    assert response.twBoard == tw_board
    if ranking_type == RankingType.valuation_low_pe:
        assert external.calls == [("TW", RankingType.volume, 100, tw_board)]
    else:
        assert response.source != "Yahoo Finance pre-ranked quote rankings."
        assert "configured" not in response.source.lower()
        assert external.calls == [("TW", ranking_type, 10, tw_board)]


def test_tw_volume_up_rankings_use_quote_scope_not_fallback() -> None:
    volume_baselines = InMemoryVolumeBaselineStore()
    for stock_id in TW_TWSE_RANKING_STOCK_IDS:
        volume_baselines.set(stock_id, 100)
    service = _hardcoded_tw_rankings_service(
        TW_TWSE_RANKING_STOCK_IDS,
        volume_baseline_store=volume_baselines,
    )

    response = service.get_rankings(
        "TW",
        RankingType.volume_up,
        limit=10,
        now=_TW_OPEN_RANKINGS_NOW,
    )

    assert response.twRankingsScope == "quote"
    assert response.source == "Quote rankings from the app's free best-effort quote providers."
    assert response.source != rankings_service_module._TW_QUOTE_FALLBACK_SOURCE
    assert response.warningMessage != rankings_service_module._TW_QUOTE_FALLBACK_WARNING


def test_tw_bounded_quote_universe_is_quote_scope_not_hardcoded_fallback() -> None:
    def bounded_resolver(board):  # noqa: ARG001
        return ["TW:2330"]

    service = RankingsService(
        quote_source=FakeQuoteSource(
            stocks={"TW:2330": _tw_stock("TW:2330", "2330", "台積電")},
            quotes={"TW:2330": _quote("TW:2330", 1000, "2026-05-12T10:00:00+08:00")},
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
        external_source=_StubExternalSource({}),
        tw_stock_ids_resolver=bounded_resolver,
    )

    response = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=10,
        now=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
    )

    assert [item.stock.symbol for item in response.items] == ["2330"]
    assert response.twRankingsScope == "quote"
    assert response.source == "Quote rankings from the app's free best-effort quote providers."
    assert "hard-coded" not in response.source


def test_tw_volume_fallback_uses_closed_freshness_when_market_closed() -> None:
    service = _hardcoded_tw_rankings_service(TW_TWSE_RANKING_STOCK_IDS)

    response = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=10,
        now=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
    )

    assert response.isMarketOpen is False
    assert response.twRankingsScope == "fallback"
    assert response.dataFreshnessLabel == rankings_service_module._TW_QUOTE_FALLBACK_FRESHNESS_CLOSED


def test_us_net_buy_is_explicitly_unsupported() -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))

    response = service.get_rankings("US", RankingType.net_buy, limit=50)

    assert response.items == []
    assert response.sourceType == RankingSourceType.unsupported
    assert (
        response.unsupportedReason
        == "US institutional net-buy data is not available from free-only sources in this app."
    )


def test_us_net_sell_is_explicitly_unsupported() -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))

    response = service.get_rankings("US", RankingType.net_sell, limit=50)

    assert response.items == []
    assert (
        response.unsupportedReason
        == "US institutional net-sell data is not available from free-only sources in this app."
    )


def test_tw_tpex_net_buy_returns_tpex_daily_warning_and_quote_fields() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:6488": _tw_stock("TW:6488", "6488", "環球晶", exchange="TPEx"),
                "TW:8299": _tw_stock("TW:8299", "8299", "群聯", exchange="TPEx"),
            },
            quotes={
                "TW:6488": _quote(
                    "TW:6488",
                    1500,
                    "2026-05-12T10:00:00+08:00",
                    last_price=480.0,
                    change_percent=2.1,
                ),
                "TW:8299": _quote(
                    "TW:8299",
                    2500,
                    "2026-05-12T10:00:00+08:00",
                    last_price=600.0,
                    change_percent=-1.4,
                ),
            },
        )
    )
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:6488": 600000, "TW:8299": 100000},
    )

    response = service.get_rankings("TW", RankingType.net_buy, limit=2, tw_board="TPEx")

    assert [item.stock.symbol for item in response.items] == ["6488", "8299"]
    assert [item.netBuySellShares for item in response.items] == [600000, 100000]
    assert [item.lastPrice for item in response.items] == [480.0, 600.0]
    assert [item.changePercent for item in response.items] == [2.1, -1.4]
    assert response.source == "TPEx institutional daily data"
    assert response.sourceType == RankingSourceType.free_public
    assert response.warningMessage == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"


def test_tw_institutional_quote_enrichment_uses_one_batch_lookup_not_per_symbol() -> None:
    quote_source = _tw_quote_source({"TW:2330": 100.0, "TW:2317": 200.0, "TW:2454": 300.0})
    get_quotes_calls = _track_get_quotes(quote_source)
    service = _tw_net_buy_service(
        quote_source,
        {"TW:2330": 500000, "TW:2317": 400000, "TW:2454": 300000},
    )

    response = service.get_rankings("TW", RankingType.net_buy, limit=3)

    assert get_quotes_calls == [["TW:2330", "TW:2317", "TW:2454"]]
    assert [item.stock.symbol for item in response.items] == ["2330", "2317", "2454"]
    assert [item.lastPrice for item in response.items] == [100.0, 200.0, 300.0]


@pytest.mark.parametrize(
    ("cache_builder", "expected_get_quotes", "expected_cache_calls"),
    [
        pytest.param(
            lambda source, ids: [_cached_item(source, stock_id) for stock_id in ids],
            [],
            [["TW:2330", "TW:2317"]],
            id="fresh-hits-skip-get-quotes",
        ),
        pytest.param(
            lambda source, _ids: [
                _cached_item(source, "TW:2330"),
                _cached_item(source, "TW:2317", quote=None),
            ],
            [["TW:2317"]],
            None,
            id="fill-missing-in-one-batch",
        ),
        pytest.param(
            lambda source, _ids: [
                _cached_item(
                    source,
                    "TW:2330",
                    quote=_quote("TW:2330", 1, "2026-05-11T10:00:00+08:00", last_price=1.0),
                    stale=True,
                ),
                _cached_item(source, "TW:2317", quote=None),
            ],
            [["TW:2330", "TW:2317"]],
            None,
            id="refresh-stale-and-missing",
        ),
    ],
)
def test_tw_institutional_quote_enrichment_cache_first_then_fill(
    cache_builder,
    expected_get_quotes,
    expected_cache_calls,
) -> None:
    quote_source = _tw_quote_source({"TW:2330": 101.0, "TW:2317": 202.0})
    get_quotes_calls = _track_get_quotes(quote_source)
    cache_first_calls = _install_cache_first(quote_source, cache_builder)
    service = _tw_net_buy_service(quote_source, {"TW:2330": 900000, "TW:2317": 100000})

    response = service.get_rankings("TW", RankingType.net_buy, limit=2)

    if expected_cache_calls is not None:
        assert cache_first_calls == expected_cache_calls
    assert get_quotes_calls == expected_get_quotes
    assert [item.lastPrice for item in response.items] == [101.0, 202.0]


def test_tw_institutional_quote_enrichment_keeps_rank_order_if_quotes_return_reversed() -> None:
    quote_source = _tw_quote_source({"TW:2330": 10.0, "TW:2317": 20.0})
    quote_source.get_quotes = lambda stock_ids: [  # type: ignore[method-assign]
        (quote_source._stocks[stock_id], quote_source._quotes[stock_id])
        for stock_id in reversed(stock_ids)
    ]
    service = _tw_net_buy_service(quote_source, {"TW:2330": 500000, "TW:2317": 100000})

    response = service.get_rankings("TW", RankingType.net_buy, limit=2)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert [item.netBuySellShares for item in response.items] == [500000, 100000]
    assert [item.lastPrice for item in response.items] == [10.0, 20.0]


def test_tw_institutional_quote_enrichment_prefers_peek_over_cache_first() -> None:
    quote_source = _tw_quote_source({"TW:2330": 101.0, "TW:2317": 202.0})
    get_quotes_calls = _track_get_quotes(quote_source)
    cache_first_calls = _install_cache_first(
        quote_source,
        lambda source, ids: [_cached_item(source, stock_id) for stock_id in ids],
    )
    peek_calls: list[list[str]] = []

    def peek(stock_ids: list[str]):
        peek_calls.append(list(stock_ids))
        return [
            _cached_item(quote_source, "TW:2330"),
            _cached_item(quote_source, "TW:2317", quote=None),
        ]

    quote_source.peek_cached_quotes = peek  # type: ignore[method-assign]
    service = _tw_net_buy_service(quote_source, {"TW:2330": 900000, "TW:2317": 100000})

    response = service.get_rankings("TW", RankingType.net_buy, limit=2)

    assert peek_calls == [["TW:2330", "TW:2317"]]
    assert cache_first_calls == []
    assert get_quotes_calls == [["TW:2317"]]
    assert [item.lastPrice for item in response.items] == [101.0, 202.0]


def test_tw_institutional_quote_enrichment_does_not_enqueue_swr_refresh(
    monkeypatch,
) -> None:
    quote_cache.clear_quote_cache_for_tests()
    monkeypatch.setattr(quote_cache.settings, "redis_url", "")
    monkeypatch.setattr(quote_cache.settings, "quote_refresher_enabled", False)
    monkeypatch.setattr(quote_cache.settings, "quote_refresher_owns_upstream", False)

    fixture = _tw_quote_source({"TW:2330": 10.0, "TW:2317": 20.0})
    quote_cache.set_cached_quote("TW:2330", fixture._quotes["TW:2330"])
    age_memory_cached_quote("TW:2330")
    scheduled: list[str] = []

    class StubTWProvider:
        def __init__(self) -> None:
            self.quote_calls: list[str] = []

        def get_stock(self, stock_id: str):
            return fixture.get_stock(stock_id)

        def get_quote(self, stock_id: str):
            self.quote_calls.append(stock_id)
            return fixture._quotes[stock_id]

    provider = StubTWProvider()
    quote_source = QuoteService()
    quote_source.providers = {"TW": provider}
    quote_source._refresh_scheduler = (
        lambda _svc, stock_id, _token: scheduled.append(stock_id)
    )
    service = _tw_net_buy_service(quote_source, {"TW:2330": 500000, "TW:2317": 400000})

    try:
        response = service.get_rankings("TW", RankingType.net_buy, limit=2)
    finally:
        quote_cache.clear_quote_cache_for_tests()

    assert scheduled == []
    assert set(provider.quote_calls) == {"TW:2330", "TW:2317"}
    assert [item.lastPrice for item in response.items] == [10.0, 20.0]


def test_tw_institutional_quote_lookups_are_not_strictly_serial(monkeypatch) -> None:
    quote_cache.clear_quote_cache_for_tests()
    monkeypatch.setattr(quote_cache.settings, "redis_url", "")
    monkeypatch.setattr(quote_cache.settings, "quote_refresher_enabled", False)
    monkeypatch.setattr(quote_cache.settings, "quote_refresher_owns_upstream", False)

    fixture = _tw_quote_source({"TW:2330": 10.0, "TW:2317": 20.0})
    get_quote, stats = overlapping_lookup(fixture._quotes.__getitem__)

    class StubTWProvider:
        def get_stock(self, stock_id: str):
            return fixture.get_stock(stock_id)

        def get_quote(self, stock_id: str):
            return get_quote(stock_id)

    quote_source = QuoteService()
    quote_source.providers = {"TW": StubTWProvider()}
    monkeypatch.setattr(quote_source, "_enqueue_refresh", lambda stock_id: False)
    service = _tw_net_buy_service(quote_source, {"TW:2330": 500000, "TW:2317": 400000})

    try:
        response = service.get_rankings("TW", RankingType.net_buy, limit=2)
    finally:
        quote_cache.clear_quote_cache_for_tests()

    assert stats.max_in_flight >= 2
    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert [item.lastPrice for item in response.items] == [10.0, 20.0]


def test_tw_tpex_net_sell_returns_tpex_daily_warning() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:6488": _tw_stock("TW:6488", "6488", "環球晶", exchange="TPEx"),
                "TW:8299": _tw_stock("TW:8299", "8299", "群聯", exchange="TPEx"),
            },
            quotes={
                "TW:6488": _quote("TW:6488", 1000, "2026-05-12T10:00:00+08:00"),
                "TW:8299": _quote("TW:8299", 2000, "2026-05-12T10:00:00+08:00"),
            },
        )
    )
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:6488": -500000, "TW:8299": -100000},
    )

    response = service.get_rankings("TW", RankingType.net_sell, limit=2, tw_board="TPEx")

    assert [item.stock.symbol for item in response.items] == ["6488", "8299"]
    assert response.source == "TPEx institutional daily data"
    assert response.warningMessage == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"


def test_tw_net_buy_returns_daily_warning() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
                "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
            },
            quotes={
                "TW:2330": _quote("TW:2330", 1000, "2026-05-12T10:00:00+08:00"),
                "TW:2317": _quote("TW:2317", 2000, "2026-05-12T10:00:00+08:00"),
            },
        )
    )
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:2330": 500000, "TW:2317": 100000},
    )

    response = service.get_rankings("TW", RankingType.net_buy, limit=2)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert response.warningMessage == "台股三大法人買賣超為日資料，非盤中即時資料。"
    assert response.sourceType == RankingSourceType.free_public


def test_tw_net_buy_ranks_zero_above_negative_values() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
                "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
                "TW:2454": _tw_stock("TW:2454", "2454", "聯發科"),
            },
            quotes={
                "TW:2330": _quote("TW:2330", 1000, "2026-05-12T10:00:00+08:00"),
                "TW:2317": _quote("TW:2317", 2000, "2026-05-12T10:00:00+08:00"),
                "TW:2454": _quote("TW:2454", 3000, "2026-05-12T10:00:00+08:00"),
            },
        )
    )
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:2330": 500000, "TW:2317": 0, "TW:2454": -100000},
    )

    response = service.get_rankings("TW", RankingType.net_buy, limit=3)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317", "2454"]
    assert [item.netBuySellShares for item in response.items] == [500000, 0, -100000]


def test_tw_net_buy_uses_market_wide_public_rows_instead_of_curated_subset() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
                "TW:1101": _tw_stock("TW:1101", "1101", "台泥"),
            },
            quotes={
                "TW:2330": _quote("TW:2330", 1000, "2026-05-12T10:00:00+08:00"),
                "TW:1101": _quote("TW:1101", 2000, "2026-05-12T10:00:00+08:00"),
            },
        )
    )
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:1101": 900000, "TW:2330": 500000},
    )

    response = service.get_rankings("TW", RankingType.net_buy, limit=2)

    assert [item.stock.symbol for item in response.items] == ["1101", "2330"]
    assert [item.netBuySellShares for item in response.items] == [900000, 500000]


def test_tw_net_sell_returns_daily_warning() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
                "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
            },
            quotes={
                "TW:2330": _quote("TW:2330", 1000, "2026-05-12T10:00:00+08:00"),
                "TW:2317": _quote("TW:2317", 2000, "2026-05-12T10:00:00+08:00"),
            },
        )
    )
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:2330": -500000, "TW:2317": -100000},
    )

    response = service.get_rankings("TW", RankingType.net_sell, limit=2)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert response.warningMessage == "台股三大法人買賣超為日資料，非盤中即時資料。"


def test_fetch_twse_institutional_rows_parses_net_buy_sell_values(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    monkeypatch.setattr(
        rankings_service_module,
        "fetch_json",
        lambda *args, **kwargs: {
            "fields": ["證券代號", "三大法人買賣超股數"],
            "data": [["2330", "500,000"], ["2317", "-125000"]],
        },
    )

    rows = service._fetch_twse_institutional_rows(date(2026, 5, 11))

    assert rows == {"TW:2330": 500000, "TW:2317": -125000}


def test_fetch_tpex_institutional_rows_parses_total_net_buy_sell_values(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    monkeypatch.setattr(
        rankings_service_module,
        "fetch_json",
        lambda *args, **kwargs: {
            "date": "20260511",
            "tables": [
                {
                    "date": "115/05/11",
                    "fields": ["代號", "名稱", "三大法人買賣超股數合計"],
                    "data": [["6488", "環球晶", "500,000"], ["8299", "群聯", "-125000"]],
                }
            ],
            "stat": "ok",
        },
    )

    snapshot = service._fetch_tpex_institutional_rows(date(2026, 5, 11))

    assert snapshot == (
        date(2026, 5, 11),
        {"TW:6488": 500000, "TW:8299": -125000},
    )


def test_fetch_tpex_institutional_rows_falls_back_to_legacy_net_buy_sell_field(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    monkeypatch.setattr(
        rankings_service_module,
        "fetch_json",
        lambda *args, **kwargs: {
            "date": "20260511",
            "tables": [
                {
                    "date": "115/05/11",
                    "fields": ["代號", "名稱", "三大法人買賣超股數"],
                    "data": [["6488", "環球晶", "250,000"]],
                }
            ],
            "stat": "ok",
        },
    )

    snapshot = service._fetch_tpex_institutional_rows(date(2026, 5, 11))

    assert snapshot == (date(2026, 5, 11), {"TW:6488": 250000})


def _inline_tw_institutional_probe(probe, current_date, timeout_seconds, *, board):  # noqa: ARG001
    """Run probes inline so budget unit tests can drive a fake perf_counter clock."""
    return probe(current_date, timeout_seconds)


def _configure_tw_institutional_budget(
    monkeypatch,
    *,
    budget: float,
    min_probe: float = 0.2,
    timeout: float = 2.5,
    max_probes: int | None = None,
    clock: dict[str, float] | None = None,
) -> None:
    monkeypatch.setattr(
        RankingsService,
        "_call_tw_institutional_probe_with_deadline",
        staticmethod(_inline_tw_institutional_probe),
    )
    monkeypatch.setattr(
        rankings_service_module,
        "_TW_INSTITUTIONAL_FETCH_BUDGET_SECONDS",
        budget,
    )
    monkeypatch.setattr(
        rankings_service_module,
        "_TW_INSTITUTIONAL_MIN_PROBE_SECONDS",
        min_probe,
    )
    monkeypatch.setattr(
        rankings_service_module,
        "_TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS",
        timeout,
    )
    if max_probes is not None:
        monkeypatch.setattr(
            rankings_service_module,
            "_TW_INSTITUTIONAL_MAX_WEEKDAY_PROBES",
            max_probes,
        )
    if clock is not None:
        monkeypatch.setattr(
            rankings_service_module,
            "perf_counter",
            lambda: clock["now"],
        )


def test_fetch_tpex_institutional_snapshot_walks_back_until_rows_are_available(
    monkeypatch,
) -> None:
    _configure_tw_institutional_budget(monkeypatch, budget=6.0)
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    requested_dates: list[date] = []

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        requested_dates.append(current_date)
        if current_date == date(2026, 5, 8):
            return (date(2026, 5, 8), {"TW:6488": 123456})
        return (current_date, {})

    service._fetch_tpex_institutional_rows = fake_rows  # type: ignore[method-assign]

    # Monday 11:00 Taipei -> publishable start is Friday 2026-05-08 after
    # weekend skip from Sunday candidate (before 18:00).
    snapshot = service._fetch_tpex_institutional_snapshot(
        datetime(2026, 5, 11, 3, 0, tzinfo=timezone.utc),
    )

    assert snapshot == (date(2026, 5, 8), {"TW:6488": 123456})
    assert requested_dates[0] == date(2026, 5, 8)
    assert all(day.weekday() < 5 for day in requested_dates)


@pytest.mark.parametrize(
    ("board", "now", "hit_date", "attr_name", "hit_payload"),
    [
        (
            "TWSE",
            datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
            date(2026, 7, 10),
            "_fetch_twse_institutional_rows",
            {"TW:2330": 1},
        ),
        (
            "TWSE",
            datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc),
            date(2026, 7, 10),
            "_fetch_twse_institutional_rows",
            {"TW:2330": 42},
        ),
        (
            "TPEx",
            datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
            date(2026, 7, 10),
            "_fetch_tpex_institutional_rows",
            (date(2026, 7, 10), {"TW:6488": 9}),
        ),
    ],
)
def test_tw_institutional_snapshot_skips_weekend_dates_without_fetching(
    monkeypatch,
    board: str,
    now: datetime,
    hit_date: date,
    attr_name: str,
    hit_payload: object,
) -> None:
    _configure_tw_institutional_budget(monkeypatch, budget=6.0)
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    requested_dates: list[date] = []

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        requested_dates.append(current_date)
        if current_date != hit_date:
            return {} if board == "TWSE" else (current_date, {})
        return hit_payload

    setattr(service, attr_name, fake_rows)
    if board == "TWSE":
        snapshot = service._fetch_twse_institutional_snapshot(now)
        assert snapshot == (hit_date, hit_payload)
    else:
        snapshot = service._fetch_tpex_institutional_snapshot(now)
        assert snapshot == hit_payload
    assert requested_dates == [hit_date]
    assert date(2026, 7, 11) not in requested_dates
    assert date(2026, 7, 12) not in requested_dates


def test_iter_tw_institutional_candidate_dates_skips_weekends_and_respects_publish_cutoff() -> None:
    # Before 18:00 Taipei on Monday: start from Sunday, then skip to Friday.
    before_cutoff = list(
        RankingsService._iter_tw_institutional_candidate_dates(
            datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc),
        )
    )
    assert before_cutoff[0] == date(2026, 7, 10)
    assert all(day.weekday() < 5 for day in before_cutoff)

    # At 18:00 Taipei on Friday: today's trading day becomes publishable.
    at_cutoff = list(
        RankingsService._iter_tw_institutional_candidate_dates(
            datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        )
    )
    assert at_cutoff[0] == date(2026, 7, 10)
    assert all(day.weekday() < 5 for day in at_cutoff)


def test_iter_tw_institutional_candidate_dates_does_not_consult_holiday_calendar(
    monkeypatch,
) -> None:
    def _boom(year: int) -> frozenset[date]:
        raise AssertionError(f"holiday calendar must not run for {year}")

    monkeypatch.setattr("app.services.market_clock._tw_market_holidays", _boom)

    # After 18:00 on a weekday holiday the date is still a candidate; interactive
    # rankings absorb a closed weekday as one bounded probe instead of blocking on
    # TWSE holiday HTTP before the fetch budget starts.
    candidates = list(
        RankingsService._iter_tw_institutional_candidate_dates(
            datetime(2026, 4, 6, 11, 0, tzinfo=timezone.utc),
        )
    )

    assert candidates[0] == date(2026, 4, 6)
    assert all(day.weekday() < 5 for day in candidates)


def test_tw_institutional_snapshot_caps_weekday_probes_even_when_fast(monkeypatch) -> None:
    _configure_tw_institutional_budget(monkeypatch, budget=6.0, max_probes=3)
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    requested_dates: list[date] = []

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        requested_dates.append(current_date)
        return {}

    service._fetch_twse_institutional_rows = fake_rows  # type: ignore[method-assign]

    snapshot = service._fetch_twse_institutional_snapshot(
        datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
    )

    assert snapshot is None
    assert len(requested_dates) == 3
    assert all(day.weekday() < 5 for day in requested_dates)


def test_tw_institutional_snapshot_continues_walkback_when_empty_responses_are_fast(
    monkeypatch,
) -> None:
    _configure_tw_institutional_budget(monkeypatch, budget=6.0)
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    requested_dates: list[date] = []

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        requested_dates.append(current_date)
        if current_date == date(2026, 7, 7):
            return {"TW:2330": 7}
        return {}

    service._fetch_twse_institutional_rows = fake_rows  # type: ignore[method-assign]

    snapshot = service._fetch_twse_institutional_snapshot(
        datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
    )

    assert snapshot == (date(2026, 7, 7), {"TW:2330": 7})
    assert requested_dates == [
        date(2026, 7, 9),
        date(2026, 7, 8),
        date(2026, 7, 7),
    ]


def test_tw_institutional_fetch_budget_binds_wall_clock_over_uncapped_probes() -> None:
    """Budget must be the binding interactive limit, not probe×timeout×retries."""
    budget = rankings_service_module._TW_INSTITUTIONAL_FETCH_BUDGET_SECONDS
    timeout = rankings_service_module._TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS
    retries = rankings_service_module._TW_INSTITUTIONAL_REQUEST_RETRIES
    uncapped = (
        rankings_service_module._TW_INSTITUTIONAL_MAX_WEEKDAY_PROBES
        * timeout
        * (1 + retries)
    )

    assert retries == 0
    assert budget <= 10
    assert uncapped > budget
    assert budget + timeout <= 12


def test_tw_institutional_snapshot_wall_clock_stays_within_budget_plus_last_probe(
    monkeypatch,
) -> None:
    clock = {"now": 0.0}
    _configure_tw_institutional_budget(
        monkeypatch,
        budget=3.0,
        min_probe=0.4,
        timeout=2.5,
        clock=clock,
    )
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    timeouts: list[float] = []

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        timeouts.append(timeout_seconds)
        clock["now"] += timeout_seconds
        return {}

    service._fetch_twse_institutional_rows = fake_rows  # type: ignore[method-assign]

    snapshot = service._fetch_twse_institutional_snapshot(
        datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
    )

    assert snapshot is None
    assert timeouts
    assert sum(timeouts) <= 3.0 + 1e-9
    assert clock["now"] <= 3.0 + 1e-9


@pytest.mark.parametrize("board", ["TWSE", "TPEx"])
def test_tw_institutional_snapshot_stops_when_slow_responses_exhaust_budget(
    monkeypatch,
    board: str,
) -> None:
    clock = {"now": 0.0}
    _configure_tw_institutional_budget(
        monkeypatch,
        budget=1.0,
        min_probe=0.2,
        clock=clock,
    )
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    requested_dates: list[date] = []

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        requested_dates.append(current_date)
        clock["now"] += 0.7
        return {} if board == "TWSE" else (current_date, {})

    if board == "TWSE":
        service._fetch_twse_institutional_rows = fake_rows  # type: ignore[method-assign]
        snapshot = service._fetch_twse_institutional_snapshot(
            datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
        )
    else:
        service._fetch_tpex_institutional_rows = fake_rows  # type: ignore[method-assign]
        snapshot = service._fetch_tpex_institutional_snapshot(
            datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
        )

    assert snapshot is None
    assert len(requested_dates) == 2
    assert len(requested_dates) < rankings_service_module._TW_INSTITUTIONAL_MAX_WEEKDAY_PROBES


def test_tw_institutional_probe_enforces_wall_clock_deadline() -> None:
    import threading
    from time import perf_counter as real_perf_counter

    started = threading.Event()
    release = threading.Event()

    def hanging_probe(current_date: date, timeout_seconds: float):  # noqa: ARG001
        started.set()
        release.wait(30)
        return current_date, {"TW:2330": 1}

    t0 = real_perf_counter()
    result = RankingsService._call_tw_institutional_probe_with_deadline(
        hanging_probe,
        date(2026, 7, 10),
        0.2,
        board="TWSE",
    )
    elapsed = real_perf_counter() - t0
    release.set()

    assert started.wait(1.0) is True
    assert result is None
    assert elapsed < 1.0


def test_fetch_tpex_institutional_rows_posts_expected_request_payload(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    captured: dict[str, object] = {}

    def fake_fetch_json(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {
            "date": "20260511",
            "tables": [
                {
                    "date": "115/05/11",
                    "fields": ["代號", "三大法人買賣超股數合計"],
                    "data": [["6488", "500,000"]],
                }
            ],
            "stat": "ok",
        }

    monkeypatch.setattr(rankings_service_module, "fetch_json", fake_fetch_json)

    snapshot = service._fetch_tpex_institutional_rows(date(2026, 5, 11))

    assert snapshot == (date(2026, 5, 11), {"TW:6488": 500000})
    assert captured["url"] == rankings_service_module._TPEX_DAILY_TRADE_URL
    assert captured["kwargs"] == {
        "method": "POST",
        "data": {
            "response": "json",
            "type": "Daily",
            "sect": rankings_service_module._TPEX_DAILY_TRADE_SECT,
            "date": "2026/05/11",
        },
        "headers": {
            "User-Agent": rankings_service_module._BROWSER_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
        "timeout_seconds": rankings_service_module._TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS,
        "retries": rankings_service_module._TW_INSTITUTIONAL_REQUEST_RETRIES,
        "provider_name": "tpex",
        "operation_name": "rankings_tpex_institutional_daily",
        "resource_id": "2026-05-11",
    }


def test_fetch_tpex_institutional_rows_skips_malformed_numeric_fields(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    monkeypatch.setattr(
        rankings_service_module,
        "fetch_json",
        lambda *args, **kwargs: {
            "date": "20260511",
            "tables": [
                {
                    "date": "115/05/11",
                    "fields": ["代號", "三大法人買賣超股數合計"],
                    "data": [["6488", "500,000"], ["8299", "bad"], ["6643", "--"]],
                }
            ],
            "stat": "ok",
        },
    )

    snapshot = service._fetch_tpex_institutional_rows(date(2026, 5, 11))

    assert snapshot == (date(2026, 5, 11), {"TW:6488": 500000})


def test_tw_institutional_snapshot_passes_remaining_budget_as_timeout(monkeypatch) -> None:
    clock = {"now": 0.0}
    timeouts: list[float] = []
    _configure_tw_institutional_budget(
        monkeypatch,
        budget=3.0,
        min_probe=0.2,
        timeout=2.5,
        clock=clock,
    )
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):
        del current_date
        timeouts.append(timeout_seconds)
        clock["now"] += 1.0
        return {}

    service._fetch_twse_institutional_rows = fake_rows  # type: ignore[method-assign]

    service._fetch_twse_institutional_snapshot(
        datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
    )

    assert timeouts
    assert timeouts[0] == 2.5
    assert timeouts[1] == pytest.approx(2.0)


def test_fetch_twse_institutional_rows_uses_bounded_timeout_and_retries(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    captured: dict[str, object] = {}

    def fake_fetch_json(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {
            "fields": ["證券代號", "三大法人買賣超股數"],
            "data": [["2330", "1"]],
        }

    monkeypatch.setattr(rankings_service_module, "fetch_json", fake_fetch_json)

    rows = service._fetch_twse_institutional_rows(date(2026, 5, 11))

    assert rows == {"TW:2330": 1}
    assert captured["url"] == rankings_service_module._TWSE_T86_URL
    assert (
        captured["kwargs"]["timeout_seconds"]
        == rankings_service_module._TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS
    )
    assert (
        captured["kwargs"]["retries"]
        == rankings_service_module._TW_INSTITUTIONAL_REQUEST_RETRIES
    )


def test_tw_net_buy_degrades_when_budget_exhausted_before_rows_found(monkeypatch) -> None:
    clock = {"now": 0.0}
    _configure_tw_institutional_budget(
        monkeypatch,
        budget=0.5,
        min_probe=0.4,
        clock=clock,
    )
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))

    def fake_rows(current_date: date, *, timeout_seconds: float = 2.5):  # noqa: ARG001
        clock["now"] += 0.5
        return {}

    service._fetch_twse_institutional_rows = fake_rows  # type: ignore[method-assign]

    response = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=50,
        now=datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc),
    )

    assert response.items == []
    assert response.sourceType == RankingSourceType.unsupported
    assert (
        response.unsupportedReason
        == "TW institutional daily data is temporarily unavailable from the free public source."
    )


def test_fetch_tw_institutional_snapshot_dispatches_by_board() -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    expected = (date(2026, 5, 11), {"TW:6488": 1})

    def fail_if_called(_now):
        raise AssertionError("TWSE institutional snapshot should not be used for TPEx")

    service._fetch_twse_institutional_snapshot = fail_if_called  # type: ignore[method-assign]
    service._fetch_tpex_institutional_snapshot = lambda now: expected  # type: ignore[method-assign]

    snapshot = service._fetch_tw_institutional_snapshot(
        datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
        "TPEx",
    )

    assert snapshot == expected


def test_tw_net_buy_degrades_when_public_source_is_unavailable() -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": None  # type: ignore[method-assign]

    response = service.get_rankings("TW", RankingType.net_buy, limit=50)

    assert response.items == []
    assert response.sourceType == RankingSourceType.unsupported
    assert response.warningMessage == "台股三大法人買賣超為日資料，非盤中即時資料。"
    assert (
        response.unsupportedReason
        == "TW institutional daily data is temporarily unavailable from the free public source."
    )


def test_tw_tpex_net_buy_degrades_when_public_source_is_unavailable() -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    service._fetch_tw_institutional_snapshot = lambda now, tw_board="TWSE": None  # type: ignore[method-assign]

    response = service.get_rankings("TW", RankingType.net_buy, limit=50, tw_board="TPEx")

    assert response.items == []
    assert response.source == "TPEx institutional daily data"
    assert response.sourceType == RankingSourceType.unsupported
    assert response.warningMessage == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
    assert (
        response.unsupportedReason
        == "TPEx institutional daily data is temporarily unavailable from the free public source."
    )


def test_djia_universe_ranks_constituents_and_skips_yahoo() -> None:
    external = StaticExternalSource(
        [
            (
                _us_stock("US:ORCL", "ORCL", "Oracle"),
                _quote("US:ORCL", 9_000_000, "2026-05-12T10:00:00-04:00"),
            )
        ]
    )
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:MSFT": _us_stock("US:MSFT", "MSFT", "Microsoft Corporation"),
                "US:ORCL": _us_stock("US:ORCL", "ORCL", "Oracle"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00"),
                "US:MSFT": _quote("US:MSFT", 300, "2026-05-12T10:00:00-04:00"),
                "US:ORCL": _quote("US:ORCL", 9_000_000, "2026-05-12T10:00:00-04:00"),
            },
        ),
        external_source=external,
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "US",
        RankingType.volume,
        limit=50,
        us_universe="DJIA",
    )

    symbols = [item.stock.symbol for item in response.items]
    assert symbols == ["MSFT", "AAPL"]
    assert "ORCL" not in symbols
    assert len(response.items) <= 30
    assert response.usRankingsScope == "djia"
    assert response.universe == "DJIA"
    assert external.calls == []


def test_nasdaq100_universe_is_isolated_from_djia() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={
                "US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc."),
                "US:META": _us_stock("US:META", "META", "Meta Platforms"),
                "US:JPM": _us_stock("US:JPM", "JPM", "JPMorgan"),
            },
            quotes={
                "US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00"),
                "US:META": _quote("US:META", 400, "2026-05-12T10:00:00-04:00"),
                "US:JPM": _quote("US:JPM", 500, "2026-05-12T10:00:00-04:00"),
            },
        ),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    djia = service.get_rankings("US", RankingType.volume, limit=50, us_universe="DJIA")
    nasdaq = service.get_rankings("US", RankingType.volume, limit=50, us_universe="NASDAQ100")

    assert [item.stock.symbol for item in djia.items] == ["JPM", "AAPL"]
    assert [item.stock.symbol for item in nasdaq.items] == ["META", "AAPL"]
    assert nasdaq.usRankingsScope == "nasdaq100"
    assert nasdaq.universe == "NASDAQ100"


def test_refresh_does_not_use_hardcoded_tw_fallback_universe() -> None:
    service = _hardcoded_tw_rankings_service(
        TW_TWSE_RANKING_STOCK_IDS,
        external_source=_StubExternalSource({}),
        sp500_quote_batch=_empty_sp500_quotes,
    )

    response = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=10,
        now=_TW_OPEN_RANKINGS_NOW,
        tw_board="TWSE",
        allow_hardcoded_fallback=False,
    )

    assert response.items == []
    assert response.twRankingsScope is None
    assert response.source != rankings_service_module._TW_QUOTE_FALLBACK_SOURCE
