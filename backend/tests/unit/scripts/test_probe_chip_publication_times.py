"""Unit tests for chip publication-time probe (no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.probe_chip_publication_times import run_probe

_TAIPEI = ZoneInfo("Asia/Taipei")


def _clock(start: datetime):
    state = {"t": start}

    def now_fn() -> datetime:
        return state["t"]

    def sleep_fn(seconds: float) -> None:
        state["t"] = state["t"] + timedelta(seconds=seconds)

    return now_fn, sleep_fn, state


def test_probe_stops_after_third_poll_records_first_available(tmp_path: Path) -> None:
    trading_day = date(2024, 6, 27)  # Thursday
    start = datetime(2024, 6, 27, 14, 30, tzinfo=_TAIPEI)
    now_fn, sleep_fn, _state = _clock(start)
    counts = {"institutional": 0, "margin": 0}

    def make_fetcher(name: str):
        def _fetch(day: date, symbol: str, market: str):  # noqa: ARG001
            counts[name] += 1
            if counts[name] >= 3:
                return True, None
            return False, "not_published"

        return _fetch

    out = tmp_path / "chip_publication_times.jsonl"
    result = run_probe(
        trading_day=trading_day,
        market="TWSE",
        stock_id="TW:2330",
        start=dt_time(14, 30),
        end=dt_time(23, 0),
        interval_minutes=10,
        fetchers={
            "institutional": make_fetcher("institutional"),
            "margin": make_fetcher("margin"),
        },
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        is_trading_day_fn=lambda _m, _d: True,
        out_path=out,
        write_jsonl=True,
    )
    assert result.first_available["institutional"] == "14:50"
    assert result.first_available["margin"] == "14:50"
    assert result.request_counts["institutional"] == 3
    assert result.request_counts["margin"] == 3
    # No further polls after both datasets available.
    assert counts["institutional"] == 3
    assert counts["margin"] == 3

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["trading_day"] == "2024-06-27"
    assert payload["market"] == "TWSE"
    assert payload["stock_id"] == "TW:2330"
    assert payload["institutional_first_available"] == "14:50"
    assert payload["margin_first_available"] == "14:50"
    assert payload["request_counts"]["institutional"] == 3
    assert "polled_at" in payload


def test_probe_enforces_request_cap(tmp_path: Path) -> None:
    trading_day = date(2024, 6, 27)
    now_fn, sleep_fn, _ = _clock(datetime(2024, 6, 27, 14, 30, tzinfo=_TAIPEI))
    calls = {"institutional": 0, "margin": 0}

    def always_empty(day, symbol, market):  # noqa: ARG001
        # Identify dataset via closure mutation from wrapper below.
        return False, "not_published"

    def tracked(name: str):
        def _fetch(day, symbol, market):  # noqa: ARG001
            calls[name] += 1
            return always_empty(day, symbol, market)

        return _fetch

    result = run_probe(
        trading_day=trading_day,
        market="TWSE",
        stock_id="TW:2330",
        start=dt_time(14, 30),
        end=dt_time(23, 0),
        interval_minutes=10,
        fetchers={
            "institutional": tracked("institutional"),
            "margin": tracked("margin"),
        },
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        is_trading_day_fn=lambda _m, _d: True,
        max_requests_per_dataset=2,
        out_path=tmp_path / "out.jsonl",
        write_jsonl=True,
    )
    assert result.request_counts["institutional"] == 2
    assert result.request_counts["margin"] == 2
    assert calls["institutional"] == 2
    assert calls["margin"] == 2
    assert result.first_available["institutional"] is None
    assert result.first_available["margin"] is None


def test_probe_non_trading_day_exits_nonzero() -> None:
    with pytest.raises(SystemExit, match="non_trading_day"):
        run_probe(
            trading_day=date(2024, 6, 29),  # Saturday
            market="TWSE",
            stock_id="TW:2330",
            start=dt_time(14, 30),
            end=dt_time(23, 0),
            interval_minutes=10,
            fetchers={
                "institutional": lambda *_a: (True, None),
                "margin": lambda *_a: (True, None),
            },
            now_fn=lambda: datetime(2024, 6, 29, 14, 30, tzinfo=_TAIPEI),
            sleep_fn=lambda _s: None,
            is_trading_day_fn=lambda _m, _d: False,
            write_jsonl=False,
        )
