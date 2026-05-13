from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services import rankings_snapshot_cache
from app.services.rankings_snapshot_cache import (
    RankingsSnapshot,
    SnapshotEntry,
)
from app.services.rankings_snapshot_service import RankingsSnapshotService
from tests.support import quote_record, stock_record


class FakeQuoteSource:
    def __init__(
        self,
        stocks: dict[str, object],
        quotes: dict[str, object],
    ) -> None:
        self._stocks = stocks
        self._quotes = quotes

    def get_stock(self, stock_id: str):
        return self._stocks.get(stock_id)

    def get_quote(self, stock_id: str):
        return self._quotes.get(stock_id)


class FakeRankingsHelper:
    """Stands in for RankingsService.fetch_tw_institutional_snapshot."""

    def __init__(
        self,
        result: tuple[date, dict[str, int]] | None,
    ) -> None:
        self._result = result
        self.calls: list[datetime] = []

    def fetch_tw_institutional_snapshot(self, now: datetime):
        self.calls.append(now)
        return self._result


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


def _quote(stock_id: str, volume: int, *, timestamp: str = "2026-05-12T10:00:00+08:00"):
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


class _InMemorySnapshotStore:
    """Captures set_snapshot calls; substitutes the Redis-backed module-level helpers."""

    def __init__(self) -> None:
        self.snapshots: dict[str, RankingsSnapshot] = {}

    def set(self, snapshot: RankingsSnapshot) -> None:
        self.snapshots[snapshot.market] = snapshot


@pytest.fixture(autouse=True)
def _patch_set_snapshot(monkeypatch):
    captured = _InMemorySnapshotStore()
    monkeypatch.setattr(
        "app.services.rankings_snapshot_service.set_snapshot",
        captured.set,
    )
    yield captured


def test_refresh_tw_fetches_quotes_for_every_universe_stock(_patch_set_snapshot) -> None:
    helper = FakeRankingsHelper(
        result=(
            date(2026, 5, 12),
            {
                "TW:2330": 500_000,
                "TW:1101": 200_000,
                "TW:2317": 100_000,
            },
        )
    )
    quote_source = FakeQuoteSource(
        stocks={
            "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
            "TW:1101": _tw_stock("TW:1101", "1101", "台泥"),
            "TW:2317": _tw_stock("TW:2317", "2317", "鴻海"),
        },
        quotes={
            "TW:2330": _quote("TW:2330", 5000),
            "TW:1101": _quote("TW:1101", 800),
            "TW:2317": _quote("TW:2317", 2500),
        },
    )
    service = RankingsSnapshotService(
        quote_source=quote_source,
        rankings_helper=helper,
        max_workers=2,
    )

    now = datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc)
    result = service.refresh("TW", now=now)

    assert result.universe_size == 3
    assert result.quote_count == 3
    assert result.skipped_count == 0
    assert result.market_open is True

    snapshot = _patch_set_snapshot.snapshots["TW"]
    assert snapshot.market == "TW"
    assert snapshot.fetched_at == now
    assert sorted(entry.stock.id for entry in snapshot.entries) == [
        "TW:1101",
        "TW:2317",
        "TW:2330",
    ]


def test_refresh_tw_skips_stocks_missing_a_quote(_patch_set_snapshot) -> None:
    helper = FakeRankingsHelper(
        result=(
            date(2026, 5, 12),
            {"TW:2330": 500_000, "TW:9999": 100_000, "TW:1101": 50_000},
        )
    )
    quote_source = FakeQuoteSource(
        stocks={
            "TW:2330": _tw_stock("TW:2330", "2330", "台積電"),
            "TW:9999": _tw_stock("TW:9999", "9999", "Delisted"),
            "TW:1101": _tw_stock("TW:1101", "1101", "台泥"),
        },
        quotes={
            "TW:2330": _quote("TW:2330", 1000),
            "TW:1101": _quote("TW:1101", 500),
        },
    )
    service = RankingsSnapshotService(
        quote_source=quote_source,
        rankings_helper=helper,
        max_workers=2,
    )

    result = service.refresh("TW", now=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc))

    assert result.universe_size == 3
    assert result.quote_count == 2
    assert result.skipped_count == 1
    snapshot = _patch_set_snapshot.snapshots["TW"]
    assert sorted(entry.stock.id for entry in snapshot.entries) == ["TW:1101", "TW:2330"]


def test_refresh_tw_writes_empty_snapshot_when_universe_unavailable(
    _patch_set_snapshot,
) -> None:
    helper = FakeRankingsHelper(result=None)
    quote_source = FakeQuoteSource(stocks={}, quotes={})
    service = RankingsSnapshotService(
        quote_source=quote_source,
        rankings_helper=helper,
    )

    result = service.refresh("TW", now=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc))

    assert result.universe_size == 0
    assert result.quote_count == 0
    snapshot = _patch_set_snapshot.snapshots["TW"]
    assert snapshot.entries == []


def test_refresh_rejects_unsupported_market(_patch_set_snapshot) -> None:
    service = RankingsSnapshotService(
        quote_source=FakeQuoteSource(stocks={}, quotes={}),
        rankings_helper=FakeRankingsHelper(result=None),
    )

    with pytest.raises(ValueError):
        service.refresh("FOO")  # type: ignore[arg-type]


def test_snapshot_age_minutes_is_zero_when_just_fetched() -> None:
    snapshot = RankingsSnapshot(
        market="TW",
        fetched_at=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
        market_open=True,
        entries=[],
    )

    assert snapshot.age_minutes(
        now=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
    ) == 0.0


def test_snapshot_age_minutes_reports_elapsed_time() -> None:
    snapshot = RankingsSnapshot(
        market="TW",
        fetched_at=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
        market_open=True,
        entries=[],
    )

    assert snapshot.age_minutes(
        now=datetime(2026, 5, 12, 3, 27, tzinfo=timezone.utc),
    ) == 27.0


def test_snapshot_round_trip_through_serialization() -> None:
    stock = _tw_stock("TW:2330", "2330", "台積電")
    quote = _quote("TW:2330", 1000)
    snapshot = RankingsSnapshot(
        market="TW",
        fetched_at=datetime(2026, 5, 12, 3, 0, tzinfo=timezone.utc),
        market_open=True,
        entries=[SnapshotEntry(stock=stock, quote=quote)],
    )
    serialized = rankings_snapshot_cache._serialize(snapshot)
    restored = rankings_snapshot_cache._deserialize(serialized)
    assert restored is not None
    assert restored.market == "TW"
    assert restored.fetched_at == snapshot.fetched_at
    assert restored.market_open is True
    assert len(restored.entries) == 1
    assert restored.entries[0].stock.id == "TW:2330"
    assert restored.entries[0].quote.volume == 1000
