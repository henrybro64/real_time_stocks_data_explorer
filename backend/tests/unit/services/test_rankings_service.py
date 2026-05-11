from datetime import date, datetime, timezone

from app.schemas.rankings import RankingSourceType, RankingType
from app.services import rankings_service as rankings_service_module
from app.services.rankings_service import RankingsService
from app.services.rankings_volume_baseline_cache import InMemoryVolumeBaselineStore
from tests.support import quote_record, stock_record


class FakeQuoteSource:
    def __init__(self, stocks: dict[str, object], quotes: dict[str, object]) -> None:
        self._stocks = stocks
        self._quotes = quotes

    def get_quotes(self, stock_ids: list[str]):
        envelopes = []
        for stock_id in stock_ids:
            stock = self._stocks.get(stock_id)
            quote = self._quotes.get(stock_id)
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


def _quote(stock_id: str, volume: int, timestamp: str):
    return quote_record(
        stock_id,
        last_price=100.0,
        change=1.0,
        change_percent=1.0,
        previous_close=99.0,
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
        )
    )

    response = service.get_rankings("US", RankingType.volume, limit=3)

    assert [item.stock.symbol for item in response.items] == ["MSFT", "NVDA", "AAPL"]
    assert response.sourceType == RankingSourceType.free_unofficial


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
    )

    response = service.get_rankings("US", RankingType.volume_up, limit=3)

    assert [item.stock.symbol for item in response.items] == ["NVDA", "AAPL", "MSFT"]
    assert [item.volumeDeltaPercent for item in response.items] == [100.0, 50.0, 20.0]


def test_volume_up_returns_null_delta_when_baseline_is_missing() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 150, "2026-05-12T10:00:00-04:00")},
        ),
        volume_baseline_store=InMemoryVolumeBaselineStore(),
    )

    response = service.get_rankings("US", RankingType.volume_up, limit=1)

    assert response.items[0].volumeDeltaPercent is None
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
    )

    response = service.get_rankings("US", RankingType.volume_down, limit=3)

    assert [item.stock.symbol for item in response.items] == ["NVDA", "AAPL", "MSFT"]
    assert [item.volumeDeltaPercent for item in response.items] == [-60.0, -50.0, -10.0]


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
        )
    )

    response = service.get_rankings("US", RankingType.volume, limit=1)

    assert len(response.items) == 1
    assert response.items[0].stock.symbol == "MSFT"


def test_market_closed_label_is_exposed_on_quote_rankings() -> None:
    service = RankingsService(
        FakeQuoteSource(
            stocks={"US:AAPL": _us_stock("US:AAPL", "AAPL", "Apple Inc.")},
            quotes={"US:AAPL": _quote("US:AAPL", 100, "2026-05-12T10:00:00-04:00")},
        )
    )

    response = service.get_rankings(
        "US",
        RankingType.volume,
        limit=1,
        now=datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert response.isMarketOpen is False
    assert "Market closed" in response.dataFreshnessLabel


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
