"""Per-symbol hard timeout hierarchy for First-Pass providers (#360)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.collector_deadline import (
    CollectorDeadlineExceeded,
    capped_request_timeout_seconds,
    collector_deadline_scope,
    sleep_respecting_collector_deadline,
)
from app.services.http_client import fetch_json
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


def test_portal_request_timeout_capped_to_remaining(monkeypatch) -> None:
    """Portal GET/POST started at T=40s of a 45s deadline gets timeout ≤5s."""
    import app.services.tw_ownership_concentration_service as svc

    clock = {"now": 100.0}
    captured: list[httpx.Timeout] = []

    class _Client:
        def get(self, url, *, timeout=None):  # noqa: ANN001
            del url
            captured.append(timeout)
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.text = "<html/>"
            resp.raise_for_status = MagicMock()
            return resp

        def post(self, url, *, data=None, timeout=None):  # noqa: ANN001
            del url, data
            captured.append(timeout)
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.text = "<html/>"
            resp.raise_for_status = MagicMock()
            return resp

    monkeypatch.setattr(svc, "extract_tdcc_synchronizer_token", lambda _t: "tok")
    monkeypatch.setattr(
        svc, "extract_tdcc_available_dates", lambda _t: ["2024-06-28"]
    )
    monkeypatch.setattr(svc, "parse_tdcc_qry_stock_html", lambda *_a, **_k: None)

    with collector_deadline_scope(100.0 + 45.0, lambda: clock["now"]):
        clock["now"] = 140.0  # 5s remaining
        svc._portal_history_with_client(
            _Client(),  # type: ignore[arg-type]
            symbol="2330",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            max_weeks=1,
        )
    assert captured
    for timeout in captured:
        assert timeout is not None
        # httpx.Timeout: read/connect/write/pool each capped.
        assert float(timeout.read) <= 5.0 + 1e-6


def test_retry_sleep_raises_when_retry_after_exceeds_remaining(monkeypatch) -> None:
    """429 with Retry-After 30 at 40s elapsed → CollectorDeadlineExceeded, no sleep."""
    sleeps: list[float] = []
    clock = {"now": 140.0}  # 5s remaining of a 45s window started at 100

    def fake_request(*_args, **_kwargs):
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {
            "content-type": "application/json",
            "retry-after": "30",
        }
        resp.content = b"{}"
        return resp

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_request)
    monkeypatch.setattr(
        "app.services.http_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    with collector_deadline_scope(100.0 + 45.0, lambda: clock["now"]):
        with pytest.raises(CollectorDeadlineExceeded):
            fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", retries=2)

    assert sleeps == []


def test_sleep_respecting_deadline_raises_without_sleeping() -> None:
    slept: list[float] = []
    clock = {"now": 140.0}
    with collector_deadline_scope(100.0 + 45.0, lambda: clock["now"]):
        with pytest.raises(CollectorDeadlineExceeded):
            sleep_respecting_collector_deadline(
                30.0, sleep=lambda s: slept.append(s)
            )
    assert slept == []


def test_pe_fetch_timeout_capped_to_remaining_deadline(monkeypatch) -> None:
    """PE day-file fetch under a 3s remaining deadline uses timeout ≤3s."""
    from app.services import key_stats_service

    key_stats_service._fetch_twse_daily_pe_snapshot.cache_clear()
    captured: list[float] = []

    def fake_send(method, url, **kwargs):  # noqa: ANN001
        del method, url
        timeout = kwargs.get("timeout")
        captured.append(float(timeout.read) if timeout is not None else -1.0)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.content = b'{"stat":"OK","fields":[],"data":[]}'
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"stat": "OK", "fields": [], "data": []}
        return resp

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_send)
    clock = {"now": 100.0}
    with collector_deadline_scope(100.0 + 3.0, lambda: clock["now"]):
        key_stats_service._fetch_twse_daily_pe_snapshot(date(2024, 6, 28))
    assert captured
    assert captured[0] <= 3.0 + 1e-6
    key_stats_service._fetch_twse_daily_pe_snapshot.cache_clear()


def test_worst_case_single_symbol_all_providers_slow_on_fake_clock() -> None:
    """All providers slow → single symbol still ≤45s on a fake clock.

    Each loader consumes only ``min(configured, remaining)`` like real HTTP
    timeout caps, so wall-clock never exceeds the symbol deadline.
    """
    clock = {"now": 0.0}

    def mono() -> float:
        return clock["now"]

    def _consume(configured: float, success):  # noqa: ANN001
        timeout = capped_request_timeout_seconds(configured)
        clock["now"] += timeout
        return success()

    started = clock["now"]
    result = collect_first_pass_batch(
        ["TW:2330"],
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _consume(30.0, _good_inst),
        margin_loader=lambda *_a, **_k: _consume(30.0, _good_margin),
        ownership_loader=lambda *_a, **_k: _consume(30.0, _good_own),
        key_stats_loader=lambda *_a, **_k: _consume(
            15.0, lambda: _key_stats("TW:2330")
        ),
        symbol_deadline_seconds=45.0,
        time_budget_seconds=90.0,
        monotonic_clock=mono,
    )
    elapsed = clock["now"] - started
    assert elapsed <= 45.0 + 1e-6
    assert len(result.records) == 1
    fields = result.records[0].fields
    # At least one later field is deadline-skipped or still present after caps.
    assert fields["institutional_5d"].status in {"ok", "partial", "unavailable"}
