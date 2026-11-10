"""History-budget resume windows and evaluation counters (#329)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.tw_research.constants import (
    DEFAULT_BUDGET_WINDOW_MONTH_FETCHES,
    HISTORY_BUDGET_EXHAUSTED_REASON,
)
from app.services.tw_research.scan import (
    TwResearchConflictError,
    TwResearchScanService,
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


def _rising_history(n: int = 120) -> list[CandleRecord]:
    end = datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
    dates: list[datetime] = []
    offset = 0
    while len(dates) < n:
        day = end - timedelta(days=offset)
        offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day.replace(hour=13, minute=30, second=0, microsecond=0))
    dates.reverse()
    out: list[CandleRecord] = []
    for index, stamp in enumerate(dates):
        close = 100.0 + index
        out.append(
            CandleRecord(
                time=stamp.isoformat(),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000 + index,
            )
        )
    return out


@pytest.fixture(autouse=True)
def _skip_calendar_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )


def test_budget_exhausted_batch_requires_explicit_resume() -> None:
    catalog = {
        "TW:2330": _tw("TW:2330"),
        "TW:2454": _tw("TW:2454"),
        "TW:2303": _tw("TW:2303"),
    }

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit
        if max_month_fetches <= 0:
            return [], 0
        return _rising_history(80), min(3, max_month_fetches)

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        max_upstream_month_fetches=3,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state = service.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.status == "budget_exhausted"
    assert state.snapshot()["budgetExhaustedCount"] >= 1

    with pytest.raises(TwResearchConflictError) as exc:
        service.run_batch(state.scan_id, batch_size=1)
    assert exc.value.code == "budget_resume_required"


def test_resume_budget_window_clears_retryable_symbols_and_allows_progress() -> None:
    catalog = {
        "TW:2330": _tw("TW:2330"),
        "TW:2454": _tw("TW:2454"),
        "TW:2303": _tw("TW:2303"),
    }
    observed: list[int] = []

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit
        observed.append(max_month_fetches)
        if max_month_fetches <= 0:
            return [], 0
        return _rising_history(80), min(3, max_month_fetches)

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        max_upstream_month_fetches=3,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state = service.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.records["TW:2454"].rejection_reason == HISTORY_BUDGET_EXHAUSTED_REASON
    snapshot = state.snapshot()
    assert snapshot["processedCount"] == 2
    assert snapshot["budgetExhaustedCount"] == 1
    assert (
        snapshot["definitiveEvaluatedCount"]
        + snapshot["unresolvedCount"]
        + snapshot["unprocessedCount"]
        == snapshot["universeSize"]
    )

    state = service.resume_budget_window(
        state.scan_id,
        additional_month_fetches=DEFAULT_BUDGET_WINDOW_MONTH_FETCHES,
    )
    assert state.status == "partial"
    assert state.max_upstream_month_fetches == 3 + DEFAULT_BUDGET_WINDOW_MONTH_FETCHES
    assert "TW:2454" not in state.records
    assert any(note.startswith("budget_window_opened:") for note in state.notes)

    state = service.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert observed[-1] > 0
    assert "TW:2454" in state.records
    assert state.records["TW:2454"].rejection_reason != HISTORY_BUDGET_EXHAUSTED_REASON


def test_warm_history_with_zero_month_fetches_does_not_mark_budget_exhausted() -> None:
    catalog = {"TW:2330": _tw("TW:2330")}
    warm = _rising_history(80)

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit
        assert max_month_fetches == 0
        return list(warm), 0

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        max_upstream_month_fetches=0,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state = service.run_batch(
        state.scan_id,
        batch_size=1,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.upstream_month_fetches == 0
    assert state.records["TW:2330"].rejection_reason != HISTORY_BUDGET_EXHAUSTED_REASON
    assert state.snapshot()["processedCount"] == 1
