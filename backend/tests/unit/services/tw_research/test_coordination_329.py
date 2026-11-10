"""Cross-pod history flight identity and durable resume conflict codes (#329)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.tw_research.constants import (
    HISTORY_BUDGET_EXHAUSTED_REASON,
    PREFERRED_DAILY_HISTORY_LIMIT,
    PRICE_BASIS,
)
from app.services.tw_research.scan import (
    TwResearchConflictError,
    TwResearchScanService,
    history_flight_identity,
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


def test_history_flight_identity_is_stable_and_compatibility_scoped() -> None:
    key_a, compat_a = history_flight_identity(
        "TW:2330",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
        expect_through="2024-06-28",
    )
    key_b, compat_b = history_flight_identity(
        "TW:2330",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
        expect_through="2024-06-28",
    )
    assert key_a == key_b
    assert compat_a == compat_b
    assert PRICE_BASIS in compat_a
    assert "through=2024-06-28" in compat_a
    assert "adj=none" in compat_a
    key_other, _ = history_flight_identity(
        "TW:2454",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
        expect_through="2024-06-28",
    )
    assert key_other != key_a
    key_limit, _ = history_flight_identity(
        "TW:2330", limit=100, expect_through="2024-06-28"
    )
    assert key_limit != key_a
    key_day, _ = history_flight_identity(
        "TW:2330",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
        expect_through="2024-06-27",
    )
    assert key_day != key_a


def test_memory_resume_rejects_non_applicable_status() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state.status = "verified_ready"
    with pytest.raises(TwResearchConflictError) as exc:
        service.resume_budget_window(state.scan_id)
    assert exc.value.code == "resume_not_applicable"


def test_memory_resume_preserves_completed_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from app.models.quote import CandleRecord

    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )

    def _rising(n: int = 80):
        end = datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
        dates = []
        offset = 0
        while len(dates) < n:
            day = end - timedelta(days=offset)
            offset += 1
            if day.weekday() < 5:
                dates.append(day)
        dates.reverse()
        out = []
        for index, stamp in enumerate(dates):
            close = 100.0 + index
            out.append(
                CandleRecord(
                    time=stamp.isoformat(),
                    open=close - 0.5,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=1000,
                )
            )
        return out

    catalog = {
        "TW:2330": _tw("TW:2330"),
        "TW:2454": _tw("TW:2454"),
        "TW:2303": _tw("TW:2303"),
    }

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit
        if max_month_fetches <= 0:
            return [], 0
        return _rising(), min(2, max_month_fetches)

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        max_upstream_month_fetches=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state = service.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.status == "budget_exhausted"
    first = state.records["TW:2330"]
    assert first.rejection_reason != HISTORY_BUDGET_EXHAUSTED_REASON
    exhausted = [
        stock_id
        for stock_id, record in state.records.items()
        if record.rejection_reason == HISTORY_BUDGET_EXHAUSTED_REASON
    ]
    assert exhausted
    state = service.resume_budget_window(state.scan_id)
    assert "TW:2330" in state.records
    assert state.records["TW:2330"].daily == first.daily
    for stock_id in exhausted:
        assert stock_id not in state.records
    assert state.budget_windows_opened == 1


def test_history_flight_renew_fail_closed_after_consecutive_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 3: prolonged renew DB failures must not publish ready markers."""
    import time

    from app.models.quote import CandleRecord
    from app.services.tw_research import scan as scan_mod
    from app.services.tw_research.constants import (
        HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES,
    )

    monkeypatch.setattr(scan_mod, "HISTORY_FLIGHT_RENEW_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(scan_mod, "HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES", 3)

    complete_calls: list[dict] = []
    renew_failures = {"n": 0}

    class _Repo:
        def is_available(self) -> bool:
            return True

        def acquire_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            return {"status": "acquired", "generation": 7}

        def renew_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            renew_failures["n"] += 1
            raise RuntimeError("connection refused")

        def complete_history_flight(self, **kwargs):  # noqa: ANN003
            complete_calls.append(dict(kwargs))
            return {"ok": True, "stale": False}

    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )
    monkeypatch.setattr(
        scan_mod,
        "stored_history_covers_requirement",
        lambda *a, **k: False,
    )

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        # Stay longer than renew interval * consecutive failure threshold.
        time.sleep(0.05 * (HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES + 2) + 0.15)
        return [
            CandleRecord(
                time="2024-06-28T13:30:00+08:00",
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            )
        ], 1

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    # Enable durable flight coordination path without requiring Postgres for create.
    service._persist = True

    candles, months = service._load_history_single_flight(  # noqa: SLF001
        state,
        _tw("TW:2330"),
        remaining_budget=5,
    )
    assert candles
    assert months == 1
    assert renew_failures["n"] >= HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES
    assert complete_calls
    assert complete_calls[-1]["success"] is False
    assert complete_calls[-1]["coverage_complete"] is False
    assert complete_calls[-1]["generation"] == 7


def test_history_flight_renew_recovers_after_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from app.models.quote import CandleRecord
    from app.services.tw_research import scan as scan_mod

    monkeypatch.setattr(scan_mod, "HISTORY_FLIGHT_RENEW_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(scan_mod, "HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES", 3)

    complete_calls: list[dict] = []
    renew_state = {"n": 0}

    class _Repo:
        def is_available(self) -> bool:
            return True

        def acquire_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            return {"status": "acquired", "generation": 3}

        def renew_history_flight(self, **kwargs):  # noqa: ANN003
            del kwargs
            renew_state["n"] += 1
            if renew_state["n"] == 1:
                raise RuntimeError("brief blip")
            return {"ok": True, "stale": False}

        def complete_history_flight(self, **kwargs):  # noqa: ANN003
            complete_calls.append(dict(kwargs))
            return {"ok": True, "stale": False}

    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )
    cover_calls = {"n": 0}

    def _covers(*_a, **_k):  # noqa: ANN001
        # First warm-path check is False so we enter the flight; later checks
        # (post-load) report complete.
        cover_calls["n"] += 1
        return cover_calls["n"] >= 2

    monkeypatch.setattr(
        scan_mod,
        "stored_history_covers_requirement",
        _covers,
    )

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        time.sleep(0.2)
        return [
            CandleRecord(
                time="2024-06-28T13:30:00+08:00",
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            )
        ], 1

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    service._persist = True

    service._load_history_single_flight(  # noqa: SLF001
        state,
        _tw("TW:2330"),
        remaining_budget=5,
    )
    assert complete_calls
    assert complete_calls[-1]["success"] is True
    assert complete_calls[-1]["coverage_complete"] is True
    assert renew_state["n"] >= 1
