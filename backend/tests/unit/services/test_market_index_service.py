from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import URLError

import pytest

from app.services.market_index_service import (
    MarketIndexRecord,
    MarketIndexService,
    _INDEX_CATALOG,
    _US_STOOQ_SYMBOLS,
    _US_YAHOO_SYMBOLS,
    _fetch_twse_index,
    _fetch_yahoo_index,
    _parse_float,
    _resolve_catalog_path,
)

CATALOG_IDS = [slot.id for slot in _INDEX_CATALOG]
US_INDEX_IDS = [slot.id for slot in _INDEX_CATALOG if slot.market == "US"]
TW_ID = next(slot.id for slot in _INDEX_CATALOG if slot.market == "TW")
TW_LABEL = next(slot.label for slot in _INDEX_CATALOG if slot.market == "TW")
SOX_ID = "^SOX"
SOX_LABEL = next(slot.label for slot in _INDEX_CATALOG if slot.id == SOX_ID)


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


def _ok_tw(*, value: float = 22000.0) -> MarketIndexRecord:
    return _ok_record(record_id=TW_ID, market="TW", label=TW_LABEL, value=value)


def _ok_yahoo_us(
    _yahoo_symbol: str,
    record_id: str,
    label: str,
    *,
    value: float = 1000.0,
) -> MarketIndexRecord:
    """Stub Yahoo US fetcher for slots that skip Stooq (e.g. ^SOX)."""
    return _ok_record(record_id=record_id, market="US", label=label, value=value)


def _ok_stooq(symbol: str, label: str, *, value: float = 100.0) -> MarketIndexRecord:
    return _ok_record(record_id=symbol, market="US", label=label, value=value)


def test_get_latest_always_returns_catalog_slots() -> None:
    service = MarketIndexService(
        twse_fetcher=_ok_tw,
        stooq_fetcher=_ok_stooq,
        yahoo_us_fetcher=_ok_yahoo_us,
    )

    records = service.get_latest()

    assert [item.id for item in records] == CATALOG_IDS
    assert records[-1].label == SOX_LABEL
    assert all(item.status == "ok" for item in records)


def test_taiwan_success_keeps_us_indices() -> None:
    service = MarketIndexService(
        twse_fetcher=lambda: _ok_tw(value=21950.32),
        stooq_fetcher=lambda symbol, label: _ok_stooq(symbol, label, value=500.0),
        yahoo_us_fetcher=lambda yahoo, rid, label: _ok_yahoo_us(yahoo, rid, label, value=500.0),
    )

    records = service.get_latest()

    assert records[0].value == pytest.approx(21950.32)
    assert records[0].status == "ok"
    assert [item.id for item in records[1:]] == US_INDEX_IDS


def test_taiwan_timeout_falls_back_to_yahoo_then_keeps_slot() -> None:
    yahoo_calls = {"count": 0}

    def failing_twse():
        raise TimeoutError("TWSE timed out")

    def yahoo_tw():
        yahoo_calls["count"] += 1
        return _ok_tw(value=21800.0)

    service = MarketIndexService(
        twse_fetcher=failing_twse,
        yahoo_taiwan_fetcher=yahoo_tw,
        stooq_fetcher=_ok_stooq,
        yahoo_us_fetcher=_ok_yahoo_us,
    )

    records = service.get_latest()

    assert yahoo_calls["count"] == 1
    assert records[0].id == TW_ID
    assert records[0].status == "ok"
    assert records[0].value == pytest.approx(21800.0)
    assert len(records) == len(CATALOG_IDS)


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
        stooq_fetcher=_ok_stooq,
        yahoo_us_fetcher=_ok_yahoo_us,
    )
    records = service.get_latest()

    assert records[0].id == TW_ID
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
        stooq_fetcher=lambda symbol, label: _ok_stooq(symbol, label, value=1000.0),
        yahoo_us_fetcher=_ok_yahoo_us,
    )

    records = service.get_latest()

    assert len(records) == len(CATALOG_IDS)
    assert records[0].status == "unavailable"
    assert [item.id for item in records[1:]] == US_INDEX_IDS
    assert all(item.status == "ok" and item.value == pytest.approx(1000.0) for item in records[1:])


def test_us_stooq_failure_falls_back_to_yahoo() -> None:
    yahoo_calls: list[tuple[str, str, str]] = []

    def yahoo_us_fetcher(yahoo_symbol: str, record_id: str, label: str):
        yahoo_calls.append((yahoo_symbol, record_id, label))
        return _ok_record(
            record_id=record_id,
            market="US",
            label=label,
            value=42.0,
        )

    service = MarketIndexService(
        twse_fetcher=_ok_tw,
        stooq_fetcher=lambda _symbol, _label: None,
        yahoo_us_fetcher=yahoo_us_fetcher,
    )

    records = service.get_latest()

    assert all(item.status == "ok" for item in records)
    assert records[1].value == pytest.approx(42.0)
    assert (SOX_ID, SOX_ID, SOX_LABEL) in yahoo_calls
    assert all(symbol != "SOXX" for symbol, _record_id, _label in yahoo_calls)


def test_sox_provider_failure_keeps_other_index_slots() -> None:
    stooq_calls: list[str] = []

    def stooq_fetcher(symbol: str, label: str):
        stooq_calls.append(symbol)
        return _ok_stooq(symbol, label, value=1000.0)

    def yahoo_us_fetcher(yahoo_symbol: str, record_id: str, label: str):
        if record_id == SOX_ID:
            return None
        return _ok_yahoo_us(yahoo_symbol, record_id, label, value=1000.0)

    service = MarketIndexService(
        twse_fetcher=_ok_tw,
        stooq_fetcher=stooq_fetcher,
        yahoo_us_fetcher=yahoo_us_fetcher,
    )

    records = service.get_latest()
    by_id = {item.id: item for item in records}

    assert [item.id for item in records] == CATALOG_IDS
    assert SOX_ID not in stooq_calls
    assert by_id[SOX_ID].status == "unavailable"
    assert by_id[SOX_ID].value is None
    assert all(by_id[index_id].status == "ok" for index_id in CATALOG_IDS if index_id != SOX_ID)


def test_sox_uses_yahoo_directly_without_stooq() -> None:
    stooq_calls: list[str] = []
    yahoo_calls: list[tuple[str, str, str]] = []

    def stooq_fetcher(symbol: str, label: str):
        stooq_calls.append(symbol)
        return _ok_stooq(symbol, label, value=1000.0)

    def yahoo_us_fetcher(yahoo_symbol: str, record_id: str, label: str):
        yahoo_calls.append((yahoo_symbol, record_id, label))
        return _ok_record(record_id=record_id, market="US", label=label, value=5488.12)

    service = MarketIndexService(
        twse_fetcher=_ok_tw,
        stooq_fetcher=stooq_fetcher,
        yahoo_us_fetcher=yahoo_us_fetcher,
    )

    by_id = {item.id: item for item in service.get_latest()}
    assert SOX_ID not in stooq_calls
    assert (SOX_ID, SOX_ID, SOX_LABEL) in yahoo_calls
    assert by_id[SOX_ID].status == "ok"
    assert by_id[SOX_ID].value == pytest.approx(5488.12)


def test_yahoo_sox_quote_normalizes_value_change_and_timestamp(monkeypatch) -> None:
    captured_url = {"value": ""}

    def fake_load_json_get(url: str, _headers: dict[str, str]) -> dict[str, object]:
        captured_url["value"] = url
        return {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 5488.123,
                            "chartPreviousClose": 5466.72,
                            "regularMarketTime": 1778270400,
                        }
                    }
                ]
            }
        }

    monkeypatch.setattr(
        "app.services.market_index_service._load_json_get",
        fake_load_json_get,
    )
    record = _fetch_yahoo_index(
        SOX_ID,
        SOX_ID,
        SOX_LABEL,
        market="US",
        timezone_name="America/New_York",
    )

    assert record is not None
    assert record.id == SOX_ID
    assert record.label == SOX_LABEL
    assert record.value == pytest.approx(5488.12)
    assert record.change == pytest.approx(21.4)
    assert record.change_percent == pytest.approx(0.39)
    assert record.timestamp == "2026-05-08T16:00:00-04:00"
    assert SOX_ID in captured_url["value"]
    assert "SOXX" not in captured_url["value"]


def test_parse_float_handles_placeholders() -> None:
    assert _parse_float("1,234.5") == pytest.approx(1234.5)
    assert _parse_float("N/D") is None
    assert _parse_float("--") is None
    assert _parse_float("") is None


def test_us_catalog_provider_maps_cover_every_us_slot() -> None:
    us_ids = [slot.id for slot in _INDEX_CATALOG if slot.market == "US"]
    assert set(us_ids) == set(_US_YAHOO_SYMBOLS)
    assert set(us_ids) == set(_US_STOOQ_SYMBOLS)
    assert _US_YAHOO_SYMBOLS[SOX_ID] == SOX_ID
    assert _US_STOOQ_SYMBOLS[SOX_ID] is None
    assert _resolve_catalog_path().name in {
        "marketIndexCatalog.json",
        "market_index_catalog.json",
    }
