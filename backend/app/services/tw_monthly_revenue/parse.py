"""Parse and normalize official monthly-revenue OpenAPI rows (#362 M1)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


PARSER_VERSION = "monthly_revenue_v1"
BASIS_AS_PUBLISHED = "as_published"

# Both boards currently publish Chinese keys for t187ap05 (verified 2026-11-27).
REQUIRED_KEYS = (
    "出表日期",
    "資料年月",
    "公司代號",
    "營業收入-當月營收",
)

OPTIONAL_NUMERIC_KEYS = (
    "營業收入-上月營收",
    "營業收入-去年當月營收",
    "營業收入-上月比較增減(%)",
    "營業收入-去年同月增減(%)",
    "累計營業收入-當月累計營收",
    "累計營業收入-去年累計營收",
    "累計營業收入-前期比較增減(%)",
)

_NULL_MARKERS = frozenset({"", "-", "--", "n/a", "na", "null", "none", "無"})
_COMPANY_CODE_RE = re.compile(r"^\d{3,6}[A-Za-z]?$")


@dataclass(frozen=True)
class ParsedRevenueRow:
    company_code: str
    stock_id: str
    company_name: str | None
    industry: str | None
    period_yyyymm: str
    report_generated_date: date | None
    revenue_nt_thousand: float | None
    prev_month_revenue_nt_thousand: float | None
    year_ago_revenue_nt_thousand: float | None
    mom_percent: float | None
    yoy_percent: float | None
    ytd_revenue_nt_thousand: float | None
    ytd_year_ago_revenue_nt_thousand: float | None
    ytd_yoy_percent: float | None
    note: str | None
    missing_fields: dict[str, str] = field(default_factory=dict)
    value_hash: str = ""

    def with_hash(self) -> ParsedRevenueRow:
        payload = {
            "period_yyyymm": self.period_yyyymm,
            "revenue_nt_thousand": self.revenue_nt_thousand,
            "prev_month_revenue_nt_thousand": self.prev_month_revenue_nt_thousand,
            "year_ago_revenue_nt_thousand": self.year_ago_revenue_nt_thousand,
            "mom_percent": self.mom_percent,
            "yoy_percent": self.yoy_percent,
            "ytd_revenue_nt_thousand": self.ytd_revenue_nt_thousand,
            "ytd_year_ago_revenue_nt_thousand": self.ytd_year_ago_revenue_nt_thousand,
            "ytd_yoy_percent": self.ytd_yoy_percent,
            "note": self.note,
            "missing_fields": self.missing_fields,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return ParsedRevenueRow(**{**asdict(self), "value_hash": digest})


def roc_yyyymm_to_gregorian(value: object) -> str | None:
    """Convert ROC ``YYYMM`` (e.g. 11510) to Gregorian ``YYYYMM`` (202610)."""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4,5}", text):
        return None
    if len(text) == 4:
        roc_year = int(text[:2])
        month = int(text[2:])
    else:
        roc_year = int(text[:-2])
        month = int(text[-2:])
    if month < 1 or month > 12:
        return None
    year = roc_year + 1911
    if year < 1912 or year > 2200:
        return None
    return f"{year:04d}{month:02d}"


def roc_yyyymmdd_to_date(value: object) -> date | None:
    """Convert ROC ``YYYMMDD`` (e.g. 1151117) to a Gregorian date."""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{6,7}", text):
        return None
    if len(text) == 6:
        roc_year = int(text[:2])
        month = int(text[2:4])
        day = int(text[4:6])
    else:
        roc_year = int(text[:-4])
        month = int(text[-4:-2])
        day = int(text[-2:])
    year = roc_year + 1911
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_company_code(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if not text or not _COMPANY_CODE_RE.fullmatch(text):
        return None
    if text.isdigit() and len(text) < 4:
        text = text.zfill(4)
    return text


def stock_id_for_code(company_code: str) -> str:
    return f"TW:{company_code}"


def parse_optional_number(value: object, *, field_name: str, missing: dict[str, str]) -> float | None:
    if value is None:
        missing[field_name] = "missing"
        return None
    if isinstance(value, bool):
        missing[field_name] = "malformed_bool"
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            missing[field_name] = "non_finite"
            return None
        return number
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in _NULL_MARKERS:
        missing[field_name] = "blank_or_sentinel"
        return None
    try:
        number = float(text)
    except ValueError:
        missing[field_name] = "malformed"
        return None
    if not math.isfinite(number):
        missing[field_name] = "non_finite"
        return None
    return number


def parse_required_number(value: object, *, field_name: str) -> tuple[float | None, str | None]:
    missing: dict[str, str] = {}
    number = parse_optional_number(value, field_name=field_name, missing=missing)
    if number is None:
        return None, missing.get(field_name, "missing")
    return number, None


def normalize_note(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None
    return text


def parse_revenue_row(raw: object, *, board: str) -> tuple[ParsedRevenueRow | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["row_not_object"]
    for key in REQUIRED_KEYS:
        if key not in raw:
            errors.append(f"missing_key:{key}")
    if errors:
        return None, errors

    company_code = normalize_company_code(raw.get("公司代號"))
    if company_code is None:
        return None, ["invalid_company_code"]

    period = roc_yyyymm_to_gregorian(raw.get("資料年月"))
    if period is None:
        return None, ["invalid_reporting_period"]

    report_generated = roc_yyyymmdd_to_date(raw.get("出表日期"))
    if report_generated is None:
        errors.append("invalid_report_generated_date")

    missing: dict[str, str] = {}
    revenue, revenue_err = parse_required_number(
        raw.get("營業收入-當月營收"),
        field_name="revenue_nt_thousand",
    )
    if revenue_err is not None:
        # Current-month revenue is required for an accepted row.
        return None, [f"required_revenue:{revenue_err}"]

    row = ParsedRevenueRow(
        company_code=company_code,
        stock_id=stock_id_for_code(company_code),
        company_name=_optional_str(raw.get("公司名稱")),
        industry=_optional_str(raw.get("產業別")),
        period_yyyymm=period,
        report_generated_date=report_generated,
        revenue_nt_thousand=revenue,
        prev_month_revenue_nt_thousand=parse_optional_number(
            raw.get("營業收入-上月營收"),
            field_name="prev_month_revenue_nt_thousand",
            missing=missing,
        ),
        year_ago_revenue_nt_thousand=parse_optional_number(
            raw.get("營業收入-去年當月營收"),
            field_name="year_ago_revenue_nt_thousand",
            missing=missing,
        ),
        mom_percent=parse_optional_number(
            raw.get("營業收入-上月比較增減(%)"),
            field_name="mom_percent",
            missing=missing,
        ),
        yoy_percent=parse_optional_number(
            raw.get("營業收入-去年同月增減(%)"),
            field_name="yoy_percent",
            missing=missing,
        ),
        ytd_revenue_nt_thousand=parse_optional_number(
            raw.get("累計營業收入-當月累計營收"),
            field_name="ytd_revenue_nt_thousand",
            missing=missing,
        ),
        ytd_year_ago_revenue_nt_thousand=parse_optional_number(
            raw.get("累計營業收入-去年累計營收"),
            field_name="ytd_year_ago_revenue_nt_thousand",
            missing=missing,
        ),
        ytd_yoy_percent=parse_optional_number(
            raw.get("累計營業收入-前期比較增減(%)"),
            field_name="ytd_yoy_percent",
            missing=missing,
        ),
        note=normalize_note(raw.get("備註")),
        missing_fields=missing,
    )
    _ = board  # board reserved for future English-key variants
    return row.with_hash(), errors


def parse_revenue_dataset(payload: object, *, board: str) -> tuple[list[ParsedRevenueRow], list[str]]:
    if not isinstance(payload, list):
        return [], ["payload_not_array"]
    if not payload:
        return [], ["empty_dataset"]

    rows: list[ParsedRevenueRow] = []
    errors: list[str] = []
    for index, item in enumerate(payload):
        parsed, row_errors = parse_revenue_row(item, board=board)
        if parsed is None:
            errors.append(f"row[{index}]:{','.join(row_errors)}")
            continue
        if row_errors:
            errors.extend(f"row[{index}]:{err}" for err in row_errors)
        rows.append(parsed)
    return rows, errors


def observation_values_dict(row: ParsedRevenueRow) -> dict[str, Any]:
    return {
        "company_name": row.company_name,
        "industry": row.industry,
        "report_generated_date": (
            row.report_generated_date.isoformat() if row.report_generated_date else None
        ),
        "revenue_nt_thousand": row.revenue_nt_thousand,
        "prev_month_revenue_nt_thousand": row.prev_month_revenue_nt_thousand,
        "year_ago_revenue_nt_thousand": row.year_ago_revenue_nt_thousand,
        "mom_percent": row.mom_percent,
        "yoy_percent": row.yoy_percent,
        "ytd_revenue_nt_thousand": row.ytd_revenue_nt_thousand,
        "ytd_year_ago_revenue_nt_thousand": row.ytd_year_ago_revenue_nt_thousand,
        "ytd_yoy_percent": row.ytd_yoy_percent,
        "note": row.note,
        "missing_fields": row.missing_fields,
    }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
