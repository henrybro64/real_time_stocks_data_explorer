"""History provenance attribution without inventing tw_default (#361)."""

from __future__ import annotations

from datetime import date

from app.services.daily_price_history_service import (
    DailyPriceHistoryService,
    HistoryFetchStats,
    exchange_upstream_source,
)
from tests.support import stock_record


class _FakeRepo:
    def __init__(
        self,
        *,
        available: bool = True,
        sources: set[str] | None = None,
        range_sources: set[str] | None = None,
    ) -> None:
        self._available = available
        self._sources = sources or set()
        self._range_sources = range_sources
        self.range_calls: list[tuple[date, date]] = []

    def is_available(self) -> bool:
        return self._available

    def distinct_sources(self, *, market: str, symbol: str, limit: int) -> set[str]:
        del market, symbol, limit
        return set(self._sources)

    def distinct_sources_in_range(
        self,
        *,
        market: str,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> set[str]:
        del market, symbol
        self.range_calls.append((start_date, end_date))
        if self._range_sources is not None:
            return set(self._range_sources)
        return set(self._sources)


def _tw_stock():
    return stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="2330",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def test_exchange_upstream_source_matches_persist_labels() -> None:
    twse = _tw_stock()
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
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(sources={"twse"}))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(had_readable_postgres=True),
    )
    assert provenance.source_provider == "twse"
    assert provenance.history_cache_location == "postgresql_daily_price_bars"
    assert provenance.history_origin == "postgresql_cache"


def test_upstream_fetch_without_store_uses_exchange_mapping() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(
            months_attempted=2,
            upstream_bars_obtained=40,
            had_readable_postgres=False,
        ),
    )
    assert provenance.source_provider == "twse"
    assert provenance.history_cache_location is None
    assert provenance.history_origin == "upstream_fetch"


def test_failed_upstream_attempts_do_not_look_like_collection() -> None:
    """Attempted month fetches alone must not report upstream_fetch / mixed."""
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(sources={"twse"}))
    provenance = service.resolve_history_provenance(
        stock,
        months_fetched=3,  # attempts only — ignored
        fetch_stats=HistoryFetchStats(
            months_attempted=3,
            upstream_bars_obtained=0,
            had_readable_postgres=True,
        ),
    )
    assert provenance.source_provider == "twse"
    assert provenance.history_origin == "postgresql_cache"
    assert provenance.history_cache_location == "postgresql_daily_price_bars"


def test_all_attempts_fail_with_no_usable_bars_is_unknown() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(
            months_attempted=2,
            upstream_bars_obtained=0,
            had_readable_postgres=False,
        ),
    )
    assert provenance.source_provider is None
    assert provenance.history_cache_location is None
    assert provenance.history_origin == "unknown"


def test_successful_upstream_with_persist_failure_reports_upstream() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(
            months_attempted=1,
            upstream_bars_obtained=20,
            had_readable_postgres=False,
            persisted_upstream=False,
        ),
    )
    assert provenance.source_provider == "twse"
    assert provenance.history_origin == "upstream_fetch"
    assert provenance.history_cache_location is None


def test_cache_plus_successful_upstream_is_mixed() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(sources={"twse"}))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(
            months_attempted=1,
            upstream_bars_obtained=15,
            had_readable_postgres=True,
            persisted_upstream=True,
        ),
    )
    assert provenance.source_provider == "twse"
    assert provenance.history_origin == "mixed"
    assert provenance.history_cache_location == "postgresql_daily_price_bars"


def test_mixed_known_sources_provider_unknown_cache_present() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(
        repository=_FakeRepo(sources={"twse", "yahoo"})
    )
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(had_readable_postgres=True),
    )
    assert provenance.source_provider is None
    assert provenance.history_cache_location == "postgresql_daily_price_bars"
    assert provenance.history_origin == "postgresql_cache"


def test_mixed_sources_inside_calculated_date_range() -> None:
    stock = _tw_stock()
    repo = _FakeRepo(
        sources={"twse"},  # newest-N would be single
        range_sources={"twse", "yahoo"},
    )
    service = DailyPriceHistoryService(repository=repo)
    provenance = service.resolve_history_provenance(
        stock,
        first_bar_as_of="2024-01-02",
        last_bar_as_of="2024-06-28",
        fetch_stats=HistoryFetchStats(had_readable_postgres=True),
    )
    assert repo.range_calls == [(date(2024, 1, 2), date(2024, 6, 28))]
    assert provenance.source_provider is None
    assert provenance.history_origin == "postgresql_cache"


def test_provider_outside_calculated_range_is_ignored() -> None:
    stock = _tw_stock()
    repo = _FakeRepo(
        sources={"yahoo"},  # outside window (newest bars)
        range_sources={"twse"},
    )
    service = DailyPriceHistoryService(repository=repo)
    provenance = service.resolve_history_provenance(
        stock,
        first_bar_as_of="2023-01-01",
        last_bar_as_of="2023-12-31",
        fetch_stats=HistoryFetchStats(had_readable_postgres=True),
    )
    assert provenance.source_provider == "twse"
    assert provenance.history_origin == "postgresql_cache"


def test_legacy_unknown_source_rows_provider_unavailable() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(sources={"unknown"}))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(had_readable_postgres=True),
    )
    assert provenance.source_provider is None
    assert provenance.history_origin == "postgresql_cache"
    assert provenance.history_cache_location == "postgresql_daily_price_bars"


def test_missing_store_and_no_fetch_is_unknown() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(
        stock,
        fetch_stats=HistoryFetchStats(had_readable_postgres=False),
    )
    assert provenance.source_provider is None
    assert provenance.history_cache_location is None
    assert provenance.history_origin == "unknown"


def test_months_fetched_alone_does_not_invent_upstream_provider() -> None:
    stock = _tw_stock()
    service = DailyPriceHistoryService(repository=_FakeRepo(available=False))
    provenance = service.resolve_history_provenance(stock, months_fetched=2)
    assert provenance.source_provider is None
    assert provenance.history_origin == "unknown"
