from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import URLError

import pytest

from app.services.market_index_service import (
    MarketIndexRecord,
    MarketIndexService,
    _fetch_twse_index,
    _parse_float,
)


def _ok_record(
    *,
    record_id: str,
    market: str,
    label: str,
    value: float = 100.0,
) -> MarketIndexRecord:
    return MarketIndexRecord(
        id=record_id,
        market=market,
        label=label,
        value=value,
        change=1.0,
        change_percent=1.0,
        timestamp=datetime(2026, 7, 13, 12, 0, tzinfo=UTC).isoformat(),
        is_market_open=False,
        status="ok",
    )


def test_get_latest_always_returns_four_catalog_slots() -> None:
    service = MarketIndexService(
        twse_fetcher=lambda: _ok_record(
            record_id="TWSE:TAIEX",
            market="TW",
            label="Taiwan Stock Exchange",
            value=22000.0,
        ),
        stooq_fetcher=lambda symbol, label: _ok_record(
            record_id=symbol,
            market="US",
            label=label,
        ),
    )

    records = service.get_latest()

    assert [item.id for item in records] == ["TWSE:TAIEX", "^DJI", "^NDQ", "^SPX"]
    assert all(item.status == "ok" for item in records)


def test_taiwan_success_keeps_us_indices() -> None:
    service = MarketIndexService(
        twse_fetcher=lambda: _ok_record(
            record_id="TWSE:TAIEX",
            market="TW",
            label="Taiwan Stock Exchange",
            value=21950.32,
        ),
        stooq_fetcher=lambda symbol, label: _ok_record(
            record_id=symbol,
            market="US",
            label=label,
            value=500.0,
        ),
    )

    records = service.get_latest()

    assert records[0].value == pytest.approx(21950.32)
    assert records[0].status == "ok"
    assert [item.id for item in records[1:]] == ["^DJI", "^NDQ", "^SPX"]


def test_taiwan_timeout_falls_back_to_yahoo_then_keeps_slot() -> None:
    yahoo_calls = {"count": 0}

    def failing_twse():
        raise TimeoutError("TWSE timed out")

    def yahoo_tw():
        yahoo_calls["count"] += 1
        return _ok_record(
            record_id="TWSE:TAIEX",
            market="TW",
            label="Taiwan Stock Exchange",
            value=21800.0,
        )

    service = MarketIndexService(
        twse_fetcher=failing_twse,
        yahoo_taiwan_fetcher=yahoo_tw,
        stooq_fetcher=lambda symbol, label: _ok_record(
            record_id=symbol,
            market="US",
            label=label,
        ),
    )

    records = service.get_latest()

    assert yahoo_calls["count"] == 1
    assert records[0].id == "TWSE:TAIEX"
    assert records[0].status == "ok"
    assert records[0].value == pytest.approx(21800.0)
    assert len(records) == 4


def test_taiwan_malformed_and_empty_payload_use_unavailable_placeholder(
    monkeypatch,
) -> None:
    def empty_payload(*_args, **_kwargs):
        return {"msgArray": []}

    monkeypatch.setattr(
        "app.services.market_index_service._load_json",
        empty_payload,
    )
    assert _fetch_twse_index() is None

    def malformed_payload(*_args, **_kwargs):
        return {"msgArray": [{"z": "N/A", "y": "--"}]}

    monkeypatch.setattr(
        "app.services.market_index_service._load_json",
        malformed_payload,
    )
    assert _fetch_twse_index() is None

    service = MarketIndexService(
        twse_fetcher=lambda: None,
        yahoo_taiwan_fetcher=lambda: None,
        stooq_fetcher=lambda symbol, label: _ok_record(
            record_id=symbol,
            market="US",
            label=label,
        ),
    )
    records = service.get_latest()

    assert records[0].id == "TWSE:TAIEX"
    assert records[0].status == "unavailable"
    assert records[0].value is None
    assert records[0].unavailable_reason == "taiwan_provider_unavailable"
    assert all(item.status == "ok" for item in records[1:])


def test_taiwan_provider_failure_does_not_drop_us_indices() -> None:
    def failing_twse():
        raise URLError("twse down")

    service = MarketIndexService(
        twse_fetcher=failing_twse,
        yahoo_taiwan_fetcher=lambda: None,
        stooq_fetcher=lambda symbol, label: _ok_record(
            record_id=symbol,
            market="US",
            label=label,
            value=1000.0,
        ),
    )

    records = service.get_latest()

    assert len(records) == 4
    assert records[0].status == "unavailable"
    assert [item.id for item in records[1:]] == ["^DJI", "^NDQ", "^SPX"]
    assert all(item.status == "ok" and item.value == pytest.approx(1000.0) for item in records[1:])


def test_us_stooq_failure_falls_back_to_yahoo() -> None:
    service = MarketIndexService(
        twse_fetcher=lambda: _ok_record(
            record_id="TWSE:TAIEX",
            market="TW",
            label="Taiwan Stock Exchange",
        ),
        stooq_fetcher=lambda _symbol, _label: None,
        yahoo_us_fetcher=lambda yahoo_symbol, record_id, label: _ok_record(
            record_id=record_id,
            market="US",
            label=label,
            value=42.0,
        ),
    )

    records = service.get_latest()

    assert all(item.status == "ok" for item in records)
    assert records[1].value == pytest.approx(42.0)


def test_parse_float_handles_placeholders() -> None:
    assert _parse_float("1,234.5") == pytest.approx(1234.5)
    assert _parse_float("N/D") is None
    assert _parse_float("--") is None
    assert _parse_float("") is None
