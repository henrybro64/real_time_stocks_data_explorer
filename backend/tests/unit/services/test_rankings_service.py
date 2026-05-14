from datetime import date, datetime, timezone

import pytest

from app.schemas.rankings import RankingSourceType, RankingType
from app.services import rankings_service as rankings_service_module
from app.services.rankings_service import RankingsService
from app.services.rankings_volume_baseline_cache import InMemoryVolumeBaselineStore
from tests.support import quote_record, stock_record


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

    def fetch(self, market, ranking_type, limit, now=None):  # noqa: ARG002
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


def _tw_stock(stock_id: str, symbol: str, name: str):
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=name,
        exchange="TWSE",
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
        self.calls: list[tuple[str, RankingType, int]] = []

    def fetch(self, market, ranking_type, limit, now=None):  # noqa: ARG002
        self.calls.append((market, ranking_type, limit))
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
    assert external.calls == [("TW", RankingType.volume, 10)]
    assert response.source == "Yahoo Finance pre-ranked quote rankings."
    assert response.usRankingsScope is None
    assert response.dataFreshnessLabel == (
        "Market open; volume rankings come from Yahoo Finance and refresh every few minutes."
    )


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
    assert external.calls == [("US", RankingType.change_up, 5)]
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
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
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
    service._fetch_tw_institutional_snapshot = lambda now: (  # type: ignore[method-assign]
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
    service._fetch_tw_institutional_snapshot = lambda now: (  # type: ignore[method-assign]
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
    service._fetch_tw_institutional_snapshot = lambda now: (  # type: ignore[method-assign]
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
    service._fetch_tw_institutional_snapshot = lambda now: (  # type: ignore[method-assign]
        date(2026, 5, 11),
        {"TW:2330": -500000, "TW:2317": -100000},
    )

    response = service.get_rankings("TW", RankingType.net_sell, limit=2)

    assert [item.stock.symbol for item in response.items] == ["2330", "2317"]
    assert response.warningMessage == "台股三大法人買賣超為日資料，非盤中即時資料。"


def test_fetch_tw_institutional_rows_parses_net_buy_sell_values(monkeypatch) -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    monkeypatch.setattr(
        rankings_service_module,
        "fetch_json",
        lambda *args, **kwargs: {
            "fields": ["證券代號", "三大法人買賣超股數"],
            "data": [["2330", "500,000"], ["2317", "-125000"]],
        },
    )

    rows = service._fetch_tw_institutional_rows(date(2026, 5, 11))

    assert rows == {"TW:2330": 500000, "TW:2317": -125000}


def test_tw_net_buy_degrades_when_public_source_is_unavailable() -> None:
    service = RankingsService(FakeQuoteSource(stocks={}, quotes={}))
    service._fetch_tw_institutional_snapshot = lambda now: None  # type: ignore[method-assign]

    response = service.get_rankings("TW", RankingType.net_buy, limit=50)

    assert response.items == []
    assert response.sourceType == RankingSourceType.unsupported
    assert response.warningMessage == "台股三大法人買賣超為日資料，非盤中即時資料。"
    assert (
        response.unsupportedReason
        == "TW institutional daily data is temporarily unavailable from the free public source."
    )
