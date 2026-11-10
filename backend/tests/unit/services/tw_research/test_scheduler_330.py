"""Disabled-by-default post-market research schedule (#330)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.tw_research.scheduler import (
    resolve_schedule_trading_day,
    run_scheduled_research,
)
from app.services.tw_research.scan import TwResearchScanService
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


def test_schedule_disabled_skips_without_creating_scan() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=False,
        trading_day="2024-06-28",
        service=service,
    )
    assert result.outcome == "skipped_disabled"
    assert result.scan_id is None


def test_schedule_dry_run_does_not_create_scan() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        dry_run=True,
        trading_day="2024-06-28",
        service=service,
    )
    assert result.outcome == "dry_run"
    assert result.dry_run is True
    assert result.scan_id is None


def test_schedule_reuses_same_day_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    catalog = {f"TW:{n}": _tw(f"TW:{n}") for n in ("2330", "2454", "2303")}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    created = service.create_scan(
        UniverseSelector(mode="starter_preset"),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )

    def _list(user_id: str, limit: int = 20):  # noqa: ANN001
        del limit
        assert user_id == "schedule-owner"
        return [
            {
                "scanId": created.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": created.status,
            }
        ]

    monkeypatch.setattr(service, "list_scans", _list)
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=1,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert result.scan_id == created.scan_id
    assert result.outcome in {"reused_scan", "partial", "budget_exhausted", "completed_window"}


def test_resolve_schedule_trading_day_skips_weekend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.is_trading_day",
        lambda market, value, allow_holiday_fetch=False: False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=False: date(2024, 6, 28),
    )
    day, skip = resolve_schedule_trading_day(
        now=datetime(2024, 6, 30, 10, 0, tzinfo=TZ),
        allow_holiday_fetch=False,
    )
    assert day == "2024-06-28"
    assert skip == "skipped_nontrading_day"
