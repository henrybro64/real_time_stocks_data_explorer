from __future__ import annotations

import json
from pathlib import Path

from app.services.tw_monthly_revenue.parse import (
    normalize_company_code,
    parse_optional_number,
    parse_revenue_dataset,
    parse_revenue_dataset_detailed,
    parse_revenue_row,
    roc_yyyymm_to_gregorian,
    roc_yyyymmdd_to_date,
)
from app.services.tw_monthly_revenue.validate import (
    max_tolerated_row_rejects,
    validate_revenue_dataset,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tw_monthly_revenue"


def _load(name: str) -> list[dict]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_roc_dates_and_company_codes() -> None:
    assert roc_yyyymm_to_gregorian("11510") == "202610"
    assert roc_yyyymmdd_to_date("1151117").isoformat() == "2026-11-17"
    assert normalize_company_code("2330") == "2330"
    assert normalize_company_code("620") == "0620"
    assert normalize_company_code("") is None
    assert roc_yyyymm_to_gregorian("1199") is None


def test_missing_optional_values_are_null_not_zero() -> None:
    missing: dict[str, str] = {}
    assert parse_optional_number("-", field_name="mom_percent", missing=missing) is None
    assert parse_optional_number("", field_name="yoy_percent", missing=missing) is None
    assert parse_optional_number(None, field_name="ytd", missing=missing) is None
    assert missing["mom_percent"] == "blank_or_sentinel"
    assert "0" not in missing.values()


def test_parse_twse_and_tpex_fixture_schemas() -> None:
    for board, name in (("TWSE", "twse_latest_month.json"), ("TPEx", "tpex_latest_month.json")):
        rows, errors = parse_revenue_dataset(_load(name), board=board)
        assert not any(err.startswith("payload_") for err in errors)
        assert rows
        assert all(row.period_yyyymm == "202610" for row in rows)
        assert rows[0].revenue_nt_thousand is not None
        # Units remain NT$ thousand; UI maps the same number as K TWD.
        assert rows[0].value_hash


def test_malformed_required_revenue_rejected() -> None:
    raw = dict(_load("twse_incomplete.json")[0])
    raw["營業收入-當月營收"] = "N/A"
    parsed, errors = parse_revenue_row(raw, board="TWSE")
    assert parsed is None
    assert any(err.startswith("required_revenue:") for err in errors)


def test_duplicate_and_incomplete_dataset_rejected() -> None:
    payload = _load("twse_incomplete.json")
    # Force duplicate identifier.
    payload = [dict(payload[0]), dict(payload[0])]
    rows, parse_errors = parse_revenue_dataset(payload, board="TWSE")
    validation = validate_revenue_dataset(
        rows,
        board="TWSE",
        parse_errors=parse_errors,
        prior_accepted_row_count=None,
    )
    assert validation.ok is False
    assert any("duplicate_company_codes" in err for err in validation.errors)
    assert any("incomplete_dataset" in err for err in validation.errors)


def test_wrong_period_mix_rejected() -> None:
    payload = _load("twse_incomplete.json")
    payload = [dict(payload[0]), dict(payload[1])]
    payload[1]["資料年月"] = "11509"
    rows, parse_errors = parse_revenue_dataset(payload, board="TWSE")
    validation = validate_revenue_dataset(
        rows,
        board="TWSE",
        parse_errors=parse_errors,
        prior_accepted_row_count=None,
    )
    assert validation.ok is False
    assert any("mixed_reporting_periods" in err for err in validation.errors)


def test_one_bad_row_out_of_820_accepted_with_bounded_errors() -> None:
    payload = _load("twse_latest_month.json")
    assert len(payload) == 820
    payload = [dict(row) for row in payload]
    payload[0]["營業收入-當月營收"] = "N/A"
    detailed = parse_revenue_dataset_detailed(payload, board="TWSE")
    assert len(detailed.row_rejects) == 1
    validation = validate_revenue_dataset(
        detailed.rows,
        board="TWSE",
        row_rejects=detailed.row_rejects,
        row_warnings=detailed.row_warnings,
        dataset_errors=detailed.dataset_errors,
        payload_total_rows=detailed.payload_total_rows,
    )
    assert validation.ok is True
    assert validation.row_count == 819
    assert validation.row_rejects_total == 1
    assert any(err.startswith("row_rejects_total:") for err in validation.errors)
    assert len([err for err in validation.errors if err.startswith("row[")]) <= 50


def test_ten_bad_rows_out_of_820_rejected() -> None:
    payload = [dict(row) for row in _load("twse_latest_month.json")]
    for index in range(10):
        payload[index]["營業收入-當月營收"] = ""
    detailed = parse_revenue_dataset_detailed(payload, board="TWSE")
    assert len(detailed.row_rejects) == 10
    assert max_tolerated_row_rejects(820) == 5
    validation = validate_revenue_dataset(
        detailed.rows,
        board="TWSE",
        row_rejects=detailed.row_rejects,
        row_warnings=detailed.row_warnings,
        dataset_errors=detailed.dataset_errors,
        payload_total_rows=detailed.payload_total_rows,
    )
    assert validation.ok is False
    assert any("excessive_row_rejects" in err for err in validation.errors)


def test_invalid_report_generated_date_is_non_fatal() -> None:
    payload = [dict(row) for row in _load("twse_latest_month.json")]
    payload[3]["出表日期"] = "not-a-date"
    detailed = parse_revenue_dataset_detailed(payload, board="TWSE")
    assert detailed.row_rejects == []
    assert any("invalid_report_generated_date" in err for err in detailed.row_warnings)
    validation = validate_revenue_dataset(
        detailed.rows,
        board="TWSE",
        row_rejects=detailed.row_rejects,
        row_warnings=detailed.row_warnings,
        dataset_errors=detailed.dataset_errors,
        payload_total_rows=detailed.payload_total_rows,
    )
    assert validation.ok is True
    assert validation.row_count == 820
