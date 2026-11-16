"""History provenance attribution without inventing tw_default (#361)."""

from __future__ import annotations

from app.services.daily_price_history_service import (
    DailyPriceHistoryService,
    exchange_upstream_source,
)
from tests.support import stock_record


class _FakeRepo:
    def __init__(self, *, available: bool = True, sources: set[str] | None = None) -> None:
        self._available = available
        self._sources = sources or set()

    def is_available(self) -> bool:
        return self._available

    def distinct_sources(self, *, market: str, symbol: str, limit: int) -> set[str]:
        del market, symbol, limit
        return set(self._sources)


def test_exchange_upstream_source_matches_persist_labels() -> None:
    twse = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="2330",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    tpex = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="6488",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    assert exchange_upstream_source(twse) == "twse"
    assert exchange_upstream_source(tpex) == "tpex"


def test_cached_known_provider_from_postgres() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="2330",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    service = DailyPriceHistoryService(repository=_FakeRepo(sources={"twse"}))
    provenance = service.resolve_history_provenance(stock, months_fetched=0)
    assert provenance.source_provider == "twse"
    assert provenance.history_cache_location == "postgresql_daily_price_bars"
    assert provenance.history_origin == "postgresql_cache"


def test_upstream_fetch_without_store_uses_exchange_mapping() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="2330",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(stock, months_fetched=2)
    assert provenance.source_provider == "twse"
    assert provenance.history_cache_location is None
    assert provenance.history_origin == "upstream_fetch"


def test_mixed_known_sources_provider_unknown_cache_present() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="2330",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    service = DailyPriceHistoryService(repository=_FakeRepo(sources={"twse", "yahoo"}))
    provenance = service.resolve_history_provenance(stock, months_fetched=0)
    assert provenance.source_provider is None
    assert provenance.history_cache_location == "postgresql_daily_price_bars"
    assert provenance.history_origin == "postgresql_cache"


def test_missing_store_and_no_fetch_is_unknown() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="2330",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(stock, months_fetched=0)
    assert provenance.source_provider is None
    assert provenance.history_cache_location is None
    assert provenance.history_origin == "unknown"
