"""HistoryFetchStats must not leak across symbols or failed loads (#361)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.daily_price_history_service import HistoryFetchStats, HistoryProvenance
from app.services.tw_research.constants import CALCULATION_VERSION_MONTHLY_FIRST
from app.services.tw_research.scan import (
    TwResearchHistoryCoordinationError,
    TwResearchScanService,
    _LAST_HISTORY_FETCH_STATS,
    _default_history_loader,
)
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


def _tw(stock_id: str):
    symbol = stock_id.split(":", 1)[1]
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=symbol,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_zh_tw=symbol,
    )


def _candles(n: int = 80, *, end: date | None = None) -> list[CandleRecord]:
    last = end or date(2024, 6, 28)
    out: list[CandleRecord] = []
    day = last
    while len(out) < n:
        if day.weekday() < 5:
            price = 100.0 + len(out) * 0.25
            out.append(
                CandleRecord(
                    time=day.isoformat(),
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=1000,
                )
            )
        day -= timedelta(days=1)
    return list(reversed(out))


def test_successful_load_then_failed_load_clears_contextvar(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod

    calls = {"n": 0}

    def _flaky(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del stock, range_name, limit, provider, max_month_fetches
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                _candles(),
                2,
                HistoryFetchStats(
                    months_attempted=2,
                    upstream_bars_obtained=40,
                    had_readable_postgres=False,
                ),
            )
        raise RuntimeError("provider_down")

    monkeypatch.setattr(
        hist_mod.daily_price_history_service,
        "get_daily_history_within_budget",
        _flaky,
    )
    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    _, months, stats = service._invoke_history_loader(  # noqa: SLF001
        _tw("TW:2330"), 80, max_month_fetches=2
    )
    assert months == 2
    assert stats is not None
    assert stats.upstream_bars_obtained == 40
    assert _LAST_HISTORY_FETCH_STATS.get() is None

    with pytest.raises(RuntimeError, match="provider_down"):
        service._invoke_history_loader(_tw("TW:2330"), 80, max_month_fetches=2)  # noqa: SLF001
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_failed_load_then_custom_loader_does_not_inherit_stale_stats() -> None:
    _LAST_HISTORY_FETCH_STATS.set(
        HistoryFetchStats(months_attempted=9, upstream_bars_obtained=99)
    )

    def _boom(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        _LAST_HISTORY_FETCH_STATS.set(
            HistoryFetchStats(upstream_bars_obtained=50, months_attempted=1)
        )
        raise RuntimeError("first_fail")

    service = TwResearchScanService(
        history_loader=_boom,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    with pytest.raises(RuntimeError, match="first_fail"):
        service._invoke_history_loader(_tw("TW:2330"), 80, max_month_fetches=1)  # noqa: SLF001
    assert _LAST_HISTORY_FETCH_STATS.get() is None

    def _custom(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        return _candles(), 0

    service._history_loader = _custom  # noqa: SLF001
    _, months, stats = service._invoke_history_loader(  # noqa: SLF001
        _tw("TW:2454"), 80, max_month_fetches=0
    )
    assert months == 0
    assert stats is None
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_custom_loader_snapshot_provenance_is_unknown() -> None:
    def _custom(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        return _candles(), 0

    service = TwResearchScanService(
        history_loader=_custom,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        max_upstream_month_fetches=0,
    )
    service.run_batch(state.scan_id, batch_size=1, now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ))
    monthly = (state.records["TW:2330"].monthly) or {}
    assert monthly.get("sourceProvider") is None
    assert monthly.get("historyOrigin") in {None, "unknown"}
    assert monthly.get("historyCacheLocation") is None


def test_sequential_stocks_do_not_reuse_fetch_stats(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod

    per_stock = {
        "TW:2330": HistoryFetchStats(
            months_attempted=1,
            upstream_bars_obtained=20,
            had_readable_postgres=False,
        ),
        "TW:2454": HistoryFetchStats(
            months_attempted=0,
            upstream_bars_obtained=0,
            had_readable_postgres=True,
        ),
    }

    def _get(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del range_name, limit, provider, max_month_fetches
        stats = per_stock[stock.id]
        return _candles(), stats.months_attempted, stats

    def _resolve(stock, **kwargs):
        del stock
        fetch_stats = kwargs.get("fetch_stats")
        if fetch_stats and fetch_stats.upstream_bars_obtained > 0:
            return HistoryProvenance("twse", None, "upstream_fetch")
        if fetch_stats and fetch_stats.had_readable_postgres:
            return HistoryProvenance(
                None, "postgresql_daily_price_bars", "postgresql_cache"
            )
        return HistoryProvenance(None, None, "unknown")

    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "get_daily_history_within_budget", _get
    )
    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "resolve_history_provenance", _resolve
    )

    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        max_upstream_month_fetches=5,
    )
    service.run_batch(state.scan_id, batch_size=2, now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ))

    a = state.records["TW:2330"].monthly or {}
    b = state.records["TW:2454"].monthly or {}
    assert a.get("historyOrigin") == "upstream_fetch"
    assert a.get("sourceProvider") == "twse"
    assert b.get("historyOrigin") == "postgresql_cache"
    assert b.get("sourceProvider") is None
    assert b.get("historyCacheLocation") == "postgresql_daily_price_bars"
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_concurrent_invokes_isolate_fetch_stats(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod

    barrier = Barrier(2)

    def _get(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del range_name, limit, provider, max_month_fetches
        barrier.wait(timeout=5)
        if stock.id == "TW:2330":
            stats = HistoryFetchStats(upstream_bars_obtained=11, months_attempted=1)
        else:
            stats = HistoryFetchStats(
                upstream_bars_obtained=0, had_readable_postgres=True
            )
        return _candles(), stats.months_attempted, stats

    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "get_daily_history_within_budget", _get
    )
    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )

    def _one(stock_id: str):
        return service._invoke_history_loader(  # noqa: SLF001
            _tw(stock_id), 80, max_month_fetches=1
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_one, "TW:2330")
        fut_b = pool.submit(_one, "TW:2454")
        _, _, stats_a = fut_a.result(timeout=5)
        _, _, stats_b = fut_b.result(timeout=5)

    assert stats_a is not None and stats_a.upstream_bars_obtained == 11
    assert stats_b is not None and stats_b.had_readable_postgres is True
    assert stats_b.upstream_bars_obtained == 0
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_warm_postgres_cache_path_returns_cache_stats(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod
    from app.services.tw_research import scan as scan_mod

    def _get(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del stock, range_name, limit, provider
        assert max_month_fetches == 0
        return (
            _candles(),
            0,
            HistoryFetchStats(had_readable_postgres=True, upstream_bars_obtained=0),
        )

    class _Repo:
        def is_available(self) -> bool:
            return True

    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "get_daily_history_within_budget", _get
    )
    monkeypatch.setattr(scan_mod, "stored_history_covers_requirement", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )

    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    service._persist = True  # noqa: SLF001
    candles, months, stats = service._load_history_single_flight(  # noqa: SLF001
        state, _tw("TW:2330"), remaining_budget=5
    )
    assert candles
    assert months == 0
    assert stats is not None
    assert stats.had_readable_postgres is True
    assert stats.upstream_bars_obtained == 0
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_upstream_persist_failure_stats_travel_explicitly(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod

    def _get(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del stock, range_name, limit, provider, max_month_fetches
        return (
            _candles(),
            1,
            HistoryFetchStats(
                months_attempted=1,
                upstream_bars_obtained=20,
                had_readable_postgres=False,
                persisted_upstream=False,
            ),
        )

    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "get_daily_history_within_budget", _get
    )
    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    _, months, stats = service._invoke_history_loader(  # noqa: SLF001
        _tw("TW:2330"), 80, max_month_fetches=1
    )
    assert months == 1
    assert stats is not None
    assert stats.upstream_bars_obtained == 20
    assert stats.persisted_upstream is False
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_single_flight_coordination_failure_clears_stale_stats(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod
    from app.services.tw_research import scan as scan_mod

    monkeypatch.setattr(scan_mod, "HISTORY_FLIGHT_RENEW_INTERVAL_SECONDS", 0.05)

    def _get(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del stock, range_name, limit, provider, max_month_fetches
        time.sleep(0.2)
        return (
            _candles(),
            1,
            HistoryFetchStats(upstream_bars_obtained=33, months_attempted=1),
        )

    class _Repo:
        def is_available(self) -> bool:
            return True

        def acquire_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            return {"status": "acquired", "generation": 1}

        def renew_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            return {"ok": False, "stale": True}

        def complete_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            return {"ok": True}

    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "get_daily_history_within_budget", _get
    )
    monkeypatch.setattr(scan_mod, "stored_history_covers_requirement", lambda *a, **k: False)
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )

    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    service._persist = True  # noqa: SLF001
    _LAST_HISTORY_FETCH_STATS.set(
        HistoryFetchStats(upstream_bars_obtained=99, months_attempted=9)
    )
    with pytest.raises(TwResearchHistoryCoordinationError):
        service._load_history_single_flight(  # noqa: SLF001
            state, _tw("TW:2330"), remaining_budget=3
        )
    assert _LAST_HISTORY_FETCH_STATS.get() is None


def test_exception_cleanup_and_retry_uses_fresh_stats(monkeypatch) -> None:
    from app.services import daily_price_history_service as hist_mod

    calls = {"n": 0}

    def _get(stock, range_name, *, limit=None, provider=None, max_month_fetches=None):
        del stock, range_name, limit, provider, max_month_fetches
        calls["n"] += 1
        if calls["n"] == 1:
            _LAST_HISTORY_FETCH_STATS.set(
                HistoryFetchStats(upstream_bars_obtained=77, months_attempted=2)
            )
            raise RuntimeError("transient")
        return (
            _candles(),
            0,
            HistoryFetchStats(had_readable_postgres=True, upstream_bars_obtained=0),
        )

    monkeypatch.setattr(
        hist_mod.daily_price_history_service, "get_daily_history_within_budget", _get
    )
    service = TwResearchScanService(
        history_loader=_default_history_loader,
        stock_loader=lambda sid: _tw(sid),
        persist=False,
    )
    with pytest.raises(RuntimeError, match="transient"):
        service._invoke_history_loader(_tw("TW:2330"), 80, max_month_fetches=1)  # noqa: SLF001
    assert _LAST_HISTORY_FETCH_STATS.get() is None

    _, months, stats = service._invoke_history_loader(  # noqa: SLF001
        _tw("TW:2330"), 80, max_month_fetches=0
    )
    assert months == 0
    assert stats is not None
    assert stats.had_readable_postgres is True
    assert stats.upstream_bars_obtained == 0
    assert _LAST_HISTORY_FETCH_STATS.get() is None
