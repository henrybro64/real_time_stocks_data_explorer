"""Dataset-level validation before publishing an accepted revenue snapshot."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.tw_monthly_revenue.parse import REQUIRED_KEYS, ParsedRevenueRow


# First-accept floors (below observed market-wide sizes from 2026-11-27 feasibility).
_MIN_ROWS_BY_BOARD = {
    "TWSE": 800,
    "TPEx": 600,
}
_PRIOR_COMPLETENESS_RATIO = 0.90


@dataclass(frozen=True)
class DatasetValidation:
    ok: bool
    errors: list[str]
    reporting_period_yyyymm: str | None
    report_generated_date_iso: str | None
    row_count: int


def validate_revenue_dataset(
    rows: list[ParsedRevenueRow],
    *,
    board: str,
    parse_errors: list[str],
    prior_accepted_row_count: int | None = None,
) -> DatasetValidation:
    errors = list(parse_errors)
    if not rows:
        errors.append("no_valid_rows")
        return DatasetValidation(
            ok=False,
            errors=errors,
            reporting_period_yyyymm=None,
            report_generated_date_iso=None,
            row_count=0,
        )

    periods = {row.period_yyyymm for row in rows}
    if len(periods) != 1:
        errors.append(f"mixed_reporting_periods:{sorted(periods)}")
    period = next(iter(periods)) if len(periods) == 1 else None

    generated = {
        row.report_generated_date.isoformat()
        for row in rows
        if row.report_generated_date is not None
    }
    if not generated:
        errors.append("missing_report_generated_date")
    generated_iso = next(iter(generated)) if len(generated) == 1 else (
        sorted(generated)[-1] if generated else None
    )
    if len(generated) > 1:
        errors.append(f"mixed_report_generated_dates:{sorted(generated)}")

    codes = [row.company_code for row in rows]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        errors.append(f"duplicate_company_codes:{duplicates[:20]}")

    min_rows = _MIN_ROWS_BY_BOARD.get(board, 1)
    if prior_accepted_row_count is None:
        if len(rows) < min_rows:
            errors.append(f"incomplete_dataset:row_count={len(rows)}<min={min_rows}")
    else:
        floor = max(min_rows, int(prior_accepted_row_count * _PRIOR_COMPLETENESS_RATIO))
        if len(rows) < floor:
            errors.append(
                "incomplete_vs_prior:"
                f"row_count={len(rows)}<floor={floor}"
                f"(prior={prior_accepted_row_count})"
            )

    # Schema stability: required keys already enforced per-row; surface unknown-key
    # risk only when a row somehow skipped required checks (defensive).
    for row in rows[:5]:
        if not row.company_code or not row.period_yyyymm:
            errors.append("row_missing_identity")
            break

    _ = REQUIRED_KEYS
    return DatasetValidation(
        ok=not errors,
        errors=errors,
        reporting_period_yyyymm=period,
        report_generated_date_iso=generated_iso,
        row_count=len(rows),
    )
