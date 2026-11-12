"""endpoint_timing helper (#346) — logs fields without altering the response."""

from __future__ import annotations

import logging

from app.core.endpoint_timing import PERF_LOG_MARKER, endpoint_timing


def test_endpoint_timing_logs_expected_fields_without_altering_response(caplog) -> None:
    payload = {"ok": True, "rows": [{"id": 1}, {"id": 2}]}
    with caplog.at_level(logging.INFO, logger="app.core.endpoint_timing"):
        with endpoint_timing(
            endpoint="GET /api/example",
            snapshot_id="fps_1",
            stock_id="TW:2330",
            record_count=10,
            calendar_preload_attempted=True,
        ) as timing:
            timing["rows_returned"] = 2
            timing["result"] = payload
            returned = payload

    assert returned is payload
    assert returned == {"ok": True, "rows": [{"id": 1}, {"id": 2}]}
    matching = [r for r in caplog.records if getattr(r, "event", None) == PERF_LOG_MARKER]
    assert len(matching) == 1
    record = matching[0]
    assert record.endpoint == "GET /api/example"
    assert record.snapshot_id == "fps_1"
    assert record.stock_id == "TW:2330"
    assert record.record_count == 10
    assert record.rows_returned == 2
    assert record.response_bytes > 0
    assert isinstance(record.duration_ms, float)
    assert record.calendar_preload_attempted is True
