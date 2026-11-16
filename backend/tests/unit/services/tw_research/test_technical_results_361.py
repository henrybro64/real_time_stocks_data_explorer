"""Unit tests for Stage-4 technical results mapping (#361)."""

from __future__ import annotations

from app.services.tw_research.constants import (
    DAILY_NOT_RUN_BY_DESIGN,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
    TECHNICAL_STAGE_DAILY_NOT_RUN,
    TECHNICAL_STAGE_DAILY_PASS,
)
from app.services.tw_research.scan import SymbolScanRecord
from app.services.tw_research.technical_results import map_symbol_technical_result


def test_map_ok_dual_pass_row() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        stock_name="台積電",
        name_status="ok",
        monthly={
            "asOf": "2024-06",
            "dif": 1.2345,
            "signal": 0.9876,
            "osc": 0.2469,
            "k": 72.15,
            "d": 65.4,
            "barCount": 48,
            "quality": "ok",
            "passStatus": "pass",
            "priceBasis": "unadjusted_close",
            "calculationVersion": "macd12-26-9_kd9-3-3_monthly_first_v1",
            "timeframe": "monthly",
        },
        daily={
            "asOf": "2024-06-28",
            "dif": 2.5,
            "signal": 1.1,
            "osc": 1.4,
            "k": 80.0,
            "d": 70.0,
            "barCount": 120,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "daily",
        },
    )
    row = map_symbol_technical_result(record)
    assert row["stockId"] == "TW:2330"
    assert row["name"] == "台積電"
    assert row["overallStageResult"] == "pass"
    assert row["monthly"]["dif"] == 1.2345
    assert row["daily"]["asOf"] == "2024-06-28"
    assert row["insufficientHistory"] is None
    assert row["dailyNotRunReason"] is None


def test_map_insufficient_history() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2454",
        stage="rejected",
        readiness="rejected",
        technical_stage="monthly_unknown",
        stock_name="聯發科",
        monthly={
            "asOf": None,
            "dif": None,
            "signal": None,
            "osc": None,
            "k": None,
            "d": None,
            "barCount": 12,
            "quality": "insufficient_history",
            "passStatus": "unknown",
            "timeframe": "monthly",
        },
        daily=None,
        coverage={
            "monthly": {"reason": "insufficient_history", "barCount": 12},
        },
    )
    row = map_symbol_technical_result(record)
    assert row["overallStageResult"] == "insufficient_history"
    assert row["insufficientHistory"] == {
        "reason": "insufficient_history",
        "requiredBars": MIN_MONTHLY_BARS_FOR_MACD_KD,
        "availableBars": 12,
        "timeframe": "monthly",
    }
    assert row["daily"] is None


def test_map_daily_not_run_by_design() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2303",
        stage="A",
        readiness="processed",
        technical_stage=TECHNICAL_STAGE_DAILY_NOT_RUN,
        stock_name="聯電",
        monthly={
            "asOf": "2024-05",
            "dif": -0.5,
            "signal": -0.2,
            "osc": -0.3,
            "k": 20.0,
            "d": 25.0,
            "barCount": 40,
            "quality": "ok",
            "passStatus": "fail",
            "timeframe": "monthly",
        },
        daily={
            "asOf": None,
            "dif": None,
            "signal": None,
            "osc": None,
            "k": None,
            "d": None,
            "passStatus": None,
            "executionStatus": DAILY_NOT_RUN_BY_DESIGN,
            "quality": "unavailable",
            "barCount": 0,
            "reason": "monthly_gate_failed",
            "timeframe": "daily",
        },
    )
    row = map_symbol_technical_result(record)
    assert row["overallStageResult"] == "daily_not_run"
    assert row["daily"] is None
    assert row["dailyNotRunReason"] == "monthly_gate_failed"
    assert row["monthly"]["passStatus"] == "fail"
