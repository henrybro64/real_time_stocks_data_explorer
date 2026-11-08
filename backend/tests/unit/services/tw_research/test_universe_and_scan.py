"""Universe selector and bounded scan coverage for #310 / #312."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.stock_theme_service import StockThemeService
from app.services.tw_research.constants import SCAN_BATCH_SIZE, UNIVERSE_PRESET_VERSION
from app.services.tw_research.contract import build_research_contract
from app.services.tw_research.preset import (
    TW_RESEARCH_THEME_ASSIGNMENTS,
    TW_RESEARCH_THEME_DEFINITIONS,
    starter_preset_stock_ids,
)
from app.services.tw_research.scan import TwResearchScanService
from app.services.tw_research.universe import UniverseSelector, resolve_universe
from tests.support import stock_record


TZ = ZoneInfo("Asia/Taipei")


def _tw(stock_id: str, *, exchange: str = "TWSE"):
    symbol = stock_id.split(":", 1)[1]
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=symbol,
        exchange=exchange,
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


def test_starter_preset_is_deduplicated_and_not_a_hard_cap() -> None:
    ids = starter_preset_stock_ids()
    assert len(ids) == len(set(ids))
    assert len(ids) >= 40
    # Not a product maximum — explicit larger lists are allowed by the selector.
    assert UNIVERSE_PRESET_VERSION.startswith("tw-starter")


def test_resolve_explicit_ids_accepts_restaurant_airline_and_unclassified() -> None:
    catalog = {
        "TW:2727": _tw("TW:2727"),  # 餐飲
        "TW:2618": _tw("TW:2618"),  # 航空
        "TW:2330": _tw("TW:2330"),  # unclassified vs starter sectors
        "TW:9999": _tw("TW:9999"),
    }
    # Ensure these are outside the starter theme assignments for the test intent.
    starter = set(starter_preset_stock_ids())
    assert "TW:2727" not in starter
    assert "TW:2618" not in starter

    resolved = resolve_universe(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2727", "TW:2618", "TW:2330", "TW:2727", "US:AAPL", "TW:0000"),
        ),
        stock_resolver=lambda stock_id: catalog.get(stock_id),
    )
    assert resolved.stock_ids == ("TW:2727", "TW:2618", "TW:2330")
    reasons = {item.stock_id: item.reason for item in resolved.rejected}
    assert reasons["US:AAPL"] == "not_taiwan_market"
    assert reasons["TW:0000"] == "not_in_catalog"
    assert "Theme membership is optional" in " ".join(resolved.notes)


def test_theme_mode_uses_existing_theme_service_without_replacing_us() -> None:
    catalog = {
        "TW:3037": _tw("TW:3037"),
        "TW:2383": _tw("TW:2383"),
        "US:NVDA": stock_record(
            "US:NVDA",
            market="US",
            symbol="NVDA",
            name="NVIDIA",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        ),
    }
    themes = StockThemeService(
        themes=TW_RESEARCH_THEME_DEFINITIONS,
        assignments=TW_RESEARCH_THEME_ASSIGNMENTS,
        stock_loader=lambda stock_id: catalog.get(stock_id),
    )
    resolved = resolve_universe(
        UniverseSelector(mode="theme_keys", theme_keys=("tw-pcb", "tw-ccl")),
        stock_resolver=lambda stock_id: catalog.get(stock_id),
        theme_service=themes,
    )
    assert "TW:3037" in resolved.stock_ids
    assert "TW:2383" in resolved.stock_ids
    assert "US:NVDA" not in resolved.stock_ids


def test_contract_marks_enrichment_fields_and_reuses_closed_issues() -> None:
    contract = build_research_contract()
    assert contract.industryAgnostic is True
    assert contract.starterPresetIsOptional is True
    assert contract.noHardUniverseCap is True
    names = {item.field for item in contract.fieldMap}
    assert "macd_kd_daily" in names
    assert "institutional_net_buys_5d" in names
    joined = " ".join(contract.notes)
    assert "#269" in joined and "#272" in joined


def test_scan_batches_paginate_and_never_reject_for_missing_theme() -> None:
    catalog = {
        "TW:2727": _tw("TW:2727"),
        "TW:2618": _tw("TW:2618"),
        "TW:2330": _tw("TW:2330"),
        "TW:2454": _tw("TW:2454"),
    }
    history = _rising_history(80)

    def loader(stock, limit):  # noqa: ANN001
        del limit
        return list(history)

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
    )
    state = service.create_scan(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2727", "TW:2618", "TW:2330", "TW:2454"),
        ),
        target_count=10,
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.universe_version.startswith("explicit:")

    first = service.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert first.cursor == 2
    assert first.status == "running"
    assert len(first.records) == 2
    for record in first.records.values():
        assert record.rejection_reason != "out_of_sector"
        assert record.daily is not None

    second = service.run_batch(
        state.scan_id,
        batch_size=SCAN_BATCH_SIZE,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert second.cursor == 4
    assert second.status == "completed"
    snap = second.snapshot()
    assert snap["checkpointsEphemeral"] is True
    assert snap["checkpointContract"]["durable"] is False
    assert "target_count is an output target only" in " ".join(second.notes)


def test_scan_batch_size_cap_and_target_count_validation() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit: [],
        stock_loader=lambda stock_id: _tw(stock_id),
    )
    with pytest.raises(ValueError, match="target_count"):
        service.create_scan(
            UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
            target_count=15,  # type: ignore[arg-type]
        )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        target_count=20,
    )
    with pytest.raises(ValueError, match="batch_size"):
        service.run_batch(state.scan_id, batch_size=11)


def test_scan_multi_batch_over_eighty_symbols_without_loading_all_at_once() -> None:
    """#312: large universes paginate across batches; stub providers only."""
    stock_ids = tuple(f"TW:{1000 + index:04d}" for index in range(81))
    catalog = {stock_id: _tw(stock_id) for stock_id in stock_ids}
    # Short history → daily unknown (no Stage B); focuses the assertion on pagination.
    history = _rising_history(5)
    fetch_counts: list[int] = []

    def loader(stock, limit):  # noqa: ANN001
        del stock, limit
        fetch_counts.append(1)
        # Each batch POST must not pull every symbol's history in one go.
        assert len(fetch_counts) <= SCAN_BATCH_SIZE
        return list(history)

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=stock_ids),
        target_count=10,
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert len(state.stock_ids) == 81
    assert state.status == "running"

    batches = 0
    while state.cursor < len(state.stock_ids):
        fetch_counts.clear()
        state = service.run_batch(
            state.scan_id,
            batch_size=SCAN_BATCH_SIZE,
            now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        )
        batches += 1
        assert len(fetch_counts) <= SCAN_BATCH_SIZE
        assert batches <= 9  # 81 / 10 → 9 batches max

    assert batches == 9
    assert state.cursor == 81
    assert state.status == "completed"
    assert len(state.records) == 81
    assert state.day_pass_ids == []
