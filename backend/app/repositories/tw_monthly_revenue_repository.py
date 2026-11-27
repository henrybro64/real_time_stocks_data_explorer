"""Persistence helpers for official monthly revenue (#362 M1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    TwFundamentalRawFetch,
    TwMonthlyRevenueObservation,
    TwMonthlyRevenueRevision,
)
from app.services.tw_monthly_revenue.parse import (
    BASIS_AS_PUBLISHED,
    ParsedRevenueRow,
    observation_values_dict,
)
from app.services.tw_monthly_revenue.validate import VALIDATION_ERROR_CAP, cap_validation_errors

DATASET_MONTHLY_REVENUE = "monthly_revenue"


def get_last_accepted_raw_fetch(
    session: Session,
    *,
    board: str,
    dataset: str = DATASET_MONTHLY_REVENUE,
) -> TwFundamentalRawFetch | None:
    """Latest accepted fetch that has a body or a resolvable body reference."""
    stmt = (
        select(TwFundamentalRawFetch)
        .where(
            TwFundamentalRawFetch.board == board,
            TwFundamentalRawFetch.dataset == dataset,
            TwFundamentalRawFetch.validation_status == "accepted",
            or_(
                TwFundamentalRawFetch.raw_body.is_not(None),
                TwFundamentalRawFetch.body_ref_fetch_id.is_not(None),
            ),
        )
        .order_by(TwFundamentalRawFetch.fetched_at.desc(), TwFundamentalRawFetch.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def resolve_raw_body(
    session: Session,
    fetch: TwFundamentalRawFetch | None,
) -> bytes | None:
    """Return bytes for a fetch, following at most one body_ref_fetch_id hop."""
    if fetch is None:
        return None
    if fetch.raw_body is not None:
        return bytes(fetch.raw_body)
    if fetch.body_ref_fetch_id is None:
        return None
    ref = session.get(TwFundamentalRawFetch, fetch.body_ref_fetch_id)
    if ref is None:
        return None
    if ref.raw_body is not None:
        return bytes(ref.raw_body)
    return None


def resolve_etag(session: Session, fetch: TwFundamentalRawFetch | None) -> str | None:
    if fetch is None:
        return None
    if fetch.etag:
        return fetch.etag
    if fetch.body_ref_fetch_id is None:
        return None
    ref = session.get(TwFundamentalRawFetch, fetch.body_ref_fetch_id)
    return ref.etag if ref is not None else None


def find_prior_body_fetch(
    session: Session,
    *,
    board: str,
    body_sha256: str,
    dataset: str = DATASET_MONTHLY_REVENUE,
) -> TwFundamentalRawFetch | None:
    """Most recent stored body (accepted or rejected) with the same SHA-256."""
    stmt = (
        select(TwFundamentalRawFetch)
        .where(
            TwFundamentalRawFetch.board == board,
            TwFundamentalRawFetch.dataset == dataset,
            TwFundamentalRawFetch.body_sha256 == body_sha256,
            TwFundamentalRawFetch.raw_body.is_not(None),
        )
        .order_by(TwFundamentalRawFetch.fetched_at.desc(), TwFundamentalRawFetch.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def get_latest_observation_for_stock(
    session: Session,
    *,
    stock_id: str,
) -> TwMonthlyRevenueObservation | None:
    stmt = (
        select(TwMonthlyRevenueObservation)
        .where(
            TwMonthlyRevenueObservation.stock_id == stock_id,
            TwMonthlyRevenueObservation.basis == BASIS_AS_PUBLISHED,
        )
        .order_by(
            TwMonthlyRevenueObservation.period_yyyymm.desc(),
            TwMonthlyRevenueObservation.id.desc(),
        )
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def get_board_freshness(
    session: Session,
    *,
    board: str,
) -> dict[str, Any]:
    last_accepted = get_last_accepted_raw_fetch(session, board=board)
    last_any_stmt = (
        select(TwFundamentalRawFetch)
        .where(
            TwFundamentalRawFetch.board == board,
            TwFundamentalRawFetch.dataset == DATASET_MONTHLY_REVENUE,
        )
        .order_by(TwFundamentalRawFetch.fetched_at.desc(), TwFundamentalRawFetch.id.desc())
        .limit(1)
    )
    last_any = session.execute(last_any_stmt).scalars().first()
    return {
        "board": board,
        "lastAcceptedAt": last_accepted.fetched_at.isoformat() if last_accepted else None,
        "lastAcceptedPeriod": last_accepted.reporting_period_yyyymm if last_accepted else None,
        "lastAcceptedRowCount": last_accepted.row_count if last_accepted else None,
        "lastAttemptAt": last_any.fetched_at.isoformat() if last_any else None,
        "lastAttemptStatus": last_any.validation_status if last_any else None,
        "lastAttemptError": last_any.error_message if last_any else None,
    }


def create_raw_fetch(
    session: Session,
    *,
    source: str,
    board: str,
    endpoint_url: str,
    request_method: str,
    request_headers: dict[str, str],
    fetched_at: datetime,
    http_status: int | None,
    etag: str | None,
    last_modified: str | None,
    body_sha256: str | None,
    body_bytes: int | None,
    raw_body: bytes | None,
    reporting_period_yyyymm: str | None,
    report_generated_date: date | None,
    parser_version: str,
    validation_status: str,
    validation_errors: list[str],
    row_count: int | None,
    error_message: str | None,
    body_ref_fetch_id: int | None = None,
) -> TwFundamentalRawFetch:
    capped_errors = cap_validation_errors(
        list(validation_errors),
        limit=VALIDATION_ERROR_CAP,
    )

    row = TwFundamentalRawFetch(
        source=source,
        dataset=DATASET_MONTHLY_REVENUE,
        board=board,
        endpoint_url=endpoint_url,
        request_method=request_method,
        request_headers_json=dict(request_headers),
        fetched_at=fetched_at,
        http_status=http_status,
        etag=etag,
        last_modified=last_modified,
        body_sha256=body_sha256,
        body_bytes=body_bytes,
        raw_body=raw_body,
        body_ref_fetch_id=body_ref_fetch_id,
        reporting_period_yyyymm=reporting_period_yyyymm,
        report_generated_date=report_generated_date,
        parser_version=parser_version,
        validation_status=validation_status,
        validation_errors_json=capped_errors,
        row_count=row_count,
        error_message=error_message,
    )
    session.add(row)
    session.flush()
    return row


def apply_accepted_rows(
    session: Session,
    *,
    board: str,
    raw_fetch: TwFundamentalRawFetch,
    rows: list[ParsedRevenueRow],
    now: datetime | None = None,
) -> dict[str, int]:
    """Upsert observations; write revisions when value hashes change."""
    now = now or datetime.now(timezone.utc)
    inserted = 0
    unchanged = 0
    revised = 0

    for parsed in rows:
        if parsed.revenue_nt_thousand is None:
            # Defense in depth: never persist rows lacking current-month revenue.
            continue
        existing = session.execute(
            select(TwMonthlyRevenueObservation).where(
                TwMonthlyRevenueObservation.stock_id == parsed.stock_id,
                TwMonthlyRevenueObservation.period_yyyymm == parsed.period_yyyymm,
                TwMonthlyRevenueObservation.basis == BASIS_AS_PUBLISHED,
            )
        ).scalars().first()

        if existing is None:
            session.add(
                TwMonthlyRevenueObservation(
                    stock_id=parsed.stock_id,
                    board=board,
                    company_code=parsed.company_code,
                    company_name=parsed.company_name,
                    industry=parsed.industry,
                    period_yyyymm=parsed.period_yyyymm,
                    report_generated_date=parsed.report_generated_date,
                    basis=BASIS_AS_PUBLISHED,
                    revenue_nt_thousand=parsed.revenue_nt_thousand,
                    prev_month_revenue_nt_thousand=parsed.prev_month_revenue_nt_thousand,
                    year_ago_revenue_nt_thousand=parsed.year_ago_revenue_nt_thousand,
                    mom_percent=parsed.mom_percent,
                    yoy_percent=parsed.yoy_percent,
                    ytd_revenue_nt_thousand=parsed.ytd_revenue_nt_thousand,
                    ytd_year_ago_revenue_nt_thousand=parsed.ytd_year_ago_revenue_nt_thousand,
                    ytd_yoy_percent=parsed.ytd_yoy_percent,
                    note=parsed.note,
                    missing_fields_json=dict(parsed.missing_fields),
                    value_hash=parsed.value_hash,
                    raw_fetch_id=raw_fetch.id,
                    first_seen_at=now,
                    last_seen_at=now,
                    revision_count=0,
                )
            )
            inserted += 1
            continue

        if existing.value_hash == parsed.value_hash:
            existing.last_seen_at = now
            existing.raw_fetch_id = raw_fetch.id
            unchanged += 1
            continue

        old_values = _observation_snapshot(existing)
        session.add(
            TwMonthlyRevenueRevision(
                observation_id=existing.id,
                changed_at=now,
                old_value_hash=existing.value_hash,
                new_value_hash=parsed.value_hash,
                old_values_json=old_values,
                new_values_json=observation_values_dict(parsed),
                previous_raw_fetch_id=existing.raw_fetch_id,
                new_raw_fetch_id=raw_fetch.id,
            )
        )
        existing.company_name = parsed.company_name
        existing.industry = parsed.industry
        existing.report_generated_date = parsed.report_generated_date
        existing.revenue_nt_thousand = parsed.revenue_nt_thousand
        existing.prev_month_revenue_nt_thousand = parsed.prev_month_revenue_nt_thousand
        existing.year_ago_revenue_nt_thousand = parsed.year_ago_revenue_nt_thousand
        existing.mom_percent = parsed.mom_percent
        existing.yoy_percent = parsed.yoy_percent
        existing.ytd_revenue_nt_thousand = parsed.ytd_revenue_nt_thousand
        existing.ytd_year_ago_revenue_nt_thousand = parsed.ytd_year_ago_revenue_nt_thousand
        existing.ytd_yoy_percent = parsed.ytd_yoy_percent
        existing.note = parsed.note
        existing.missing_fields_json = dict(parsed.missing_fields)
        existing.value_hash = parsed.value_hash
        existing.raw_fetch_id = raw_fetch.id
        existing.last_seen_at = now
        existing.revision_count = int(existing.revision_count or 0) + 1
        revised += 1

    session.flush()
    return {"inserted": inserted, "unchanged": unchanged, "revised": revised}


def _observation_snapshot(row: TwMonthlyRevenueObservation) -> dict[str, Any]:
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
        "missing_fields": dict(row.missing_fields_json or {}),
    }
