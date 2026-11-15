"""Per-symbol hard timeout hierarchy for First-Pass providers (#360)."""

from __future__ import annotations

import pytest

from app.services.collector_deadline import (
    CollectorDeadlineExceeded,
    capped_request_timeout_seconds,
    collector_deadline_scope,
)
from app.services.tw_research.first_pass import (
    COLLECTOR_DEADLINE_EXCEEDED_REASON,
    DEFAULT_SYMBOL_DEADLINE_SECONDS,
    collect_first_pass_batch,
)
from tests.support import stock_record
from tests.unit.services.tw_research.test_first_pass_budget_circuit_348_349 import (
    _candles,
    _good_inst,
    _good_margin,
    _good_own,
    _key_stats,
)


def _stock_loader(stock_id: str):
    return stock_record(
        stock_id,
        market="TW",
        symbol=stock_id.split(":")[-1],
        name=stock_id,
        name_zh_tw=stock_id,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def test_capped_timeout_respects_remaining_deadline() -> None:
    clock = {"now": 100.0}

    def mono() -> float:
        return clock["now"]

    with collector_deadline_scope(100.0 + 5.0, mono):
        assert capped_request_timeout_seconds(20.0) == pytest.approx(5.0)
        clock["now"] = 104.5
        with pytest.raises(CollectorDeadlineExceeded):
            capped_request_timeout_seconds(20.0)


def test_later_steps_skip_when_early_steps_consume_deadline() -> None:
    clock = {"now": 0.0}

    def mono() -> float:
        return clock["now"]

    def slow_institutional(*_a, **_k):
        clock["now"] += 23.0
        return _good_inst()

    def slow_margin(*_a, **_k):
        clock["now"] += 23.0
        return _good_margin()

    def ownership_loader(*_a, **_k):
        raise AssertionError("ownership must be skipped after deadline")

    result = collect_first_pass_batch(
        ["TW:2330"],
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=slow_institutional,
        margin_loader=slow_margin,
        ownership_loader=ownership_loader,
        key_stats_loader=_key_stats,
        symbol_deadline_seconds=45.0,
        monotonic_clock=mono,
        time_budget_seconds=90.0,
    )
    assert len(result.records) == 1
    fields = result.records[0].fields
    ownership = fields.get("tdcc_holders_ge_400_lots")
    assert ownership is not None
    assert ownership.status == "unavailable"
    assert ownership.reason == COLLECTOR_DEADLINE_EXCEEDED_REASON
    # Peers collected before the deadline remain.
    assert fields["institutional_5d"].status in {"ok", "partial", "unavailable"}


def test_worst_case_batch_duration_on_fake_clock() -> None:
    """batch 60s + one symbol ≤45s → ≤105s under curl 150s / Job 180s."""
    assert DEFAULT_SYMBOL_DEADLINE_SECONDS == 45.0
    clock = {"now": 0.0}

    def mono() -> float:
        return clock["now"]

    def consume(stock_id: str):
        del stock_id
        clock["now"] += 20.0
        return _good_inst()

    started = clock["now"]
    result = collect_first_pass_batch(
        ["TW:2330", "TW:2317", "TW:2454"],
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=consume,
        margin_loader=lambda _s: _good_margin(),
        ownership_loader=lambda _s: _good_own(),
        key_stats_loader=_key_stats,
        symbol_deadline_seconds=45.0,
        time_budget_seconds=60.0,
        monotonic_clock=mono,
    )
    elapsed = clock["now"] - started
    assert elapsed <= 105.0
    assert result.collected_count >= 1
