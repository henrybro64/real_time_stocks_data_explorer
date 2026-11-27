from __future__ import annotations

import json
from pathlib import Path

from app.services.tw_monthly_revenue.parse import (
    normalize_company_code,
    parse_optional_number,
    parse_revenue_dataset,
    parse_revenue_row,
    roc_yyyymm_to_gregorian,
    roc_yyyymmdd_to_date,
)
from app.services.tw_monthly_revenue.validate import validate_revenue_dataset

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
