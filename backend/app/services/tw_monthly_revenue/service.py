"""Fetch, validate and persist official latest-month revenue (#362 M1).

Operator entry point: :func:`collect_latest_monthly_revenue`. No scheduler is
started from this module; browser/API stock-detail reads never trigger fetches.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories import tw_monthly_revenue_repository as repo
from app.services.http_client import (
    HttpFetchError,
    HttpResponseMeta,
    fetch_response_meta,
)
from app.services.tw_monthly_revenue.parse import (
    PARSER_VERSION,
    parse_revenue_dataset_detailed,
)
from app.services.tw_monthly_revenue.validate import validate_revenue_dataset

logger = logging.getLogger(__name__)

TWSE_MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_MONTHLY_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

BOARD_ENDPOINTS: dict[str, tuple[str, str]] = {
    "TWSE": ("twse_openapi", TWSE_MONTHLY_REVENUE_URL),
    "TPEx": ("tpex_openapi", TPEX_MONTHLY_REVENUE_URL),
}

# Feasibility guidance: OpenAPI connect 10s / total 60s; concurrency 1; retries bounded.
_CONNECT_READ_TIMEOUT_SECONDS = 60.0
_RETRIES = 1
_PROVIDER_NAMES = {
    "TWSE": "twse_openapi",
    "TPEx": "tpex",
}
# Per-board transaction-scoped pg_try_advisory_xact_lock keys (#362 M1).
# Taken at the start of each board ingest and held until that board's commit.
_BOARD_ADVISORY_LOCK_KEYS = {
    "TWSE": 362_000_001,
    "TPEx": 362_000_002,
}
_TAIPEI = ZoneInfo("Asia/Taipei")
_STALE_MONTHS = 2

FetchFn = Callable[..., HttpResponseMeta]


@dataclass(frozen=True)
class BoardIngestResult:
    board: str
    outcome: str
    validation_status: str
    reporting_period_yyyymm: str | None
    row_count: int | None
    raw_fetch_id: int | None
    stats: dict[str, int]
    errors: list[str]
    error_message: str | None = None


def observation_period_is_stale(
    period_yyyymm: str,
    *,
    now: datetime | None = None,
) -> bool:
    """True when the observation period is more than 2 calendar months behind Asia/Taipei."""
    text_period = str(period_yyyymm or "").strip()
    if len(text_period) != 6 or not text_period.isdigit():
        return False
    year = int(text_period[:4])
    month = int(text_period[4:6])
    if month < 1 or month > 12:
        return False
    current = now.astimezone(_TAIPEI) if now is not None else datetime.now(_TAIPEI)
    current_index = current.year * 12 + (current.month - 1)
    period_index = year * 12 + (month - 1)
    return (current_index - period_index) > _STALE_MONTHS


def get_monthly_revenue_for_stock(session: Session, stock_id: str) -> dict[str, object] | None:
    """Return the latest stored official observation, or None if never collected."""
    row = repo.get_latest_observation_for_stock(session, stock_id=stock_id)
    if row is None:
        return None
    period = row.period_yyyymm
    month_label = f"{period[:4]}-{period[4:6]}" if len(period) == 6 else period
    return {
        "status": "ok",
        "source": f"official_monthly_revenue:{row.board}",
        "reason": None,
        "stockId": row.stock_id,
        "board": row.board,
        "reportingPeriod": month_label,
        "reportingPeriodYyyymm": row.period_yyyymm,
        "reportGeneratedDate": (
            row.report_generated_date.isoformat() if row.report_generated_date else None
        ),
        "retrievedAt": None,
        "revenueNtThousand": row.revenue_nt_thousand,
        "momPercent": row.mom_percent,
        "yoyPercent": row.yoy_percent,
        "ytdRevenueNtThousand": row.ytd_revenue_nt_thousand,
        "ytdYoyPercent": row.ytd_yoy_percent,
        "note": row.note,
        "missingFields": dict(row.missing_fields_json or {}),
        "lastSeenAt": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "rawFetchId": row.raw_fetch_id,
        "stale": observation_period_is_stale(row.period_yyyymm),
    }


def collect_latest_monthly_revenue(
    session: Session,
    *,
    boards: tuple[str, ...] = ("TWSE", "TPEx"),
    fetch_fn: FetchFn | None = None,
) -> list[BoardIngestResult]:
    """Bounded operator-driven collection for both boards (no scheduler).

    Advisory locking is per board inside :func:`ingest_board_monthly_revenue`
    so each board's transaction holds its lock until that board commits.
    """
    results: list[BoardIngestResult] = []
    for board in boards:
        results.append(
            ingest_board_monthly_revenue(session, board=board, fetch_fn=fetch_fn)
        )
    return results


def ingest_board_monthly_revenue(
    session: Session,
    *,
    board: str,
    fetch_fn: FetchFn | None = None,
    now: datetime | None = None,
) -> BoardIngestResult:
    if board not in BOARD_ENDPOINTS:
        raise ValueError(f"unsupported board: {board}")
    # Acquire first so the lock covers get_last_accepted…commit for this board.
    if not _try_collect_advisory_lock(session, board=board):
        session.rollback()
        return BoardIngestResult(
            board=board,
            outcome="skipped_locked",
            validation_status="skipped_locked",
            reporting_period_yyyymm=None,
            row_count=None,
            raw_fetch_id=None,
            stats={},
            errors=["advisory_lock_held"],
            error_message=(
                f"Another monthly-revenue collection holds the {board} advisory lock"
            ),
        )
    source, url = BOARD_ENDPOINTS[board]
    now = now or datetime.now(timezone.utc)
    last_accepted = repo.get_last_accepted_raw_fetch(session, board=board)
    last_accepted_body = repo.resolve_raw_body(session, last_accepted)
    last_accepted_etag = repo.resolve_etag(session, last_accepted)
    request_headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "real-time-stocks-official-monthly-revenue/1.0",
    }
    # Conditional GET only when an accepted cached body exists.
    if last_accepted is not None and last_accepted_body is not None and last_accepted_etag:
        request_headers["If-None-Match"] = last_accepted_etag

    fetcher = fetch_fn or _default_fetch
    try:
        response = fetcher(
            url,
            headers=dict(request_headers),
            timeout_seconds=_CONNECT_READ_TIMEOUT_SECONDS,
            retries=_RETRIES,
            provider_name=_PROVIDER_NAMES[board],
            operation_name="monthly_revenue_latest",
            resource_id=board,
            retry_rate_limits=False,
        )
    except (HttpFetchError, TimeoutError, OSError) as exc:
        raw = repo.create_raw_fetch(
            session,
            source=source,
            board=board,
            endpoint_url=url,
            request_method="GET",
            request_headers=request_headers,
            fetched_at=now,
            http_status=getattr(exc, "status_code", None),
            etag=None,
            last_modified=None,
            body_sha256=None,
            body_bytes=None,
            raw_body=None,
            reporting_period_yyyymm=None,
            report_generated_date=None,
            parser_version=PARSER_VERSION,
            validation_status="fetch_failed",
            validation_errors=[f"fetch_error:{type(exc).__name__}"],
            row_count=None,
            error_message=str(exc)[:500],
        )
        session.commit()
        return BoardIngestResult(
            board=board,
            outcome="fetch_failed",
            validation_status="fetch_failed",
            reporting_period_yyyymm=(
                last_accepted.reporting_period_yyyymm if last_accepted else None
            ),
            row_count=last_accepted.row_count if last_accepted else None,
            raw_fetch_id=raw.id,
            stats={},
            errors=[f"fetch_error:{type(exc).__name__}"],
            error_message=str(exc)[:500],
        )

    if response.status_code == 304:
        if last_accepted is None or last_accepted_body is None:
            raw = repo.create_raw_fetch(
                session,
                source=source,
                board=board,
                endpoint_url=url,
                request_method="GET",
                request_headers=request_headers,
                fetched_at=now,
                http_status=304,
                etag=_header(response, "etag"),
                last_modified=_header(response, "last-modified"),
                body_sha256=None,
                body_bytes=0,
                raw_body=None,
                reporting_period_yyyymm=None,
                report_generated_date=None,
                parser_version=PARSER_VERSION,
                # Keep within validation_status String(32).
                validation_status="rejected_304_no_cache",
                validation_errors=["conditional_304_without_usable_cache"],
                row_count=None,
                error_message="Received HTTP 304 without an accepted cached body",
            )
            session.commit()
            return BoardIngestResult(
                board=board,
                outcome="rejected",
                validation_status="rejected_304_no_cache",
                reporting_period_yyyymm=None,
                row_count=None,
                raw_fetch_id=raw.id,
                stats={},
                errors=["conditional_304_without_usable_cache"],
                error_message="Received HTTP 304 without an accepted cached body",
            )
        raw = repo.create_raw_fetch(
            session,
            source=source,
            board=board,
            endpoint_url=url,
            request_method="GET",
            request_headers=request_headers,
            fetched_at=now,
            http_status=304,
            etag=last_accepted_etag or last_accepted.etag,
            last_modified=last_accepted.last_modified,
            body_sha256=last_accepted.body_sha256,
            body_bytes=last_accepted.body_bytes,
            raw_body=None,
            body_ref_fetch_id=last_accepted.body_ref_fetch_id or last_accepted.id,
            reporting_period_yyyymm=last_accepted.reporting_period_yyyymm,
            report_generated_date=last_accepted.report_generated_date,
            parser_version=PARSER_VERSION,
            validation_status="not_modified",
            validation_errors=[],
            row_count=last_accepted.row_count,
            error_message=None,
        )
        session.commit()
        return BoardIngestResult(
            board=board,
            outcome="not_modified",
            validation_status="not_modified",
            reporting_period_yyyymm=last_accepted.reporting_period_yyyymm,
            row_count=last_accepted.row_count,
            raw_fetch_id=raw.id,
            stats={"unchanged": last_accepted.row_count or 0},
            errors=[],
        )

    body = response.content
    sha256 = hashlib.sha256(body).hexdigest()
    prior_body = repo.find_prior_body_fetch(session, board=board, body_sha256=sha256)
    store_body: bytes | None = body
    body_ref_fetch_id: int | None = None
    if prior_body is not None:
        store_body = None
        body_ref_fetch_id = prior_body.id

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raw = repo.create_raw_fetch(
            session,
            source=source,
            board=board,
            endpoint_url=url,
            request_method="GET",
            request_headers=request_headers,
            fetched_at=now,
            http_status=response.status_code,
            etag=_header(response, "etag"),
            last_modified=_header(response, "last-modified"),
            body_sha256=sha256,
            body_bytes=len(body),
            raw_body=store_body,
            body_ref_fetch_id=body_ref_fetch_id,
            reporting_period_yyyymm=None,
            report_generated_date=None,
            parser_version=PARSER_VERSION,
            validation_status="rejected_invalid_json",
            validation_errors=[f"json_decode:{type(exc).__name__}"],
            row_count=None,
            error_message=str(exc)[:500],
        )
        session.commit()
        return BoardIngestResult(
            board=board,
            outcome="rejected",
            validation_status="rejected_invalid_json",
            reporting_period_yyyymm=None,
            row_count=None,
            raw_fetch_id=raw.id,
            stats={},
            errors=[f"json_decode:{type(exc).__name__}"],
            error_message=str(exc)[:500],
        )

    parsed = parse_revenue_dataset_detailed(payload, board=board)
    validation = validate_revenue_dataset(
        parsed.rows,
        board=board,
        row_rejects=parsed.row_rejects,
        row_warnings=parsed.row_warnings,
        dataset_errors=parsed.dataset_errors,
        prior_accepted_row_count=last_accepted.row_count if last_accepted else None,
        payload_total_rows=parsed.payload_total_rows,
    )
    report_generated = (
        date.fromisoformat(validation.report_generated_date_iso)
        if validation.report_generated_date_iso
        else None
    )

    if not validation.ok:
        # Persist rejected raw body for lineage; leave prior observations untouched.
        raw = repo.create_raw_fetch(
            session,
            source=source,
            board=board,
            endpoint_url=url,
            request_method="GET",
            request_headers=request_headers,
            fetched_at=now,
            http_status=response.status_code,
            etag=_header(response, "etag"),
            last_modified=_header(response, "last-modified"),
            body_sha256=sha256,
            body_bytes=len(body),
            raw_body=store_body,
            body_ref_fetch_id=body_ref_fetch_id,
            reporting_period_yyyymm=validation.reporting_period_yyyymm,
            report_generated_date=report_generated,
            parser_version=PARSER_VERSION,
            validation_status="rejected",
            validation_errors=validation.errors,
            row_count=validation.row_count,
            error_message=";".join(validation.errors)[:500],
        )
        session.commit()
        return BoardIngestResult(
            board=board,
            outcome="rejected",
            validation_status="rejected",
            reporting_period_yyyymm=validation.reporting_period_yyyymm,
            row_count=validation.row_count,
            raw_fetch_id=raw.id,
            stats={},
            errors=validation.errors,
            error_message=";".join(validation.errors)[:500],
        )

    # Atomic accept: raw fetch + observation upsert/revisions in one transaction.
    raw = repo.create_raw_fetch(
        session,
        source=source,
        board=board,
        endpoint_url=url,
        request_method="GET",
        request_headers=request_headers,
        fetched_at=now,
        http_status=response.status_code,
        etag=_header(response, "etag"),
        last_modified=_header(response, "last-modified"),
        body_sha256=sha256,
        body_bytes=len(body),
        raw_body=store_body,
        body_ref_fetch_id=body_ref_fetch_id,
        reporting_period_yyyymm=validation.reporting_period_yyyymm,
        report_generated_date=report_generated,
        parser_version=PARSER_VERSION,
        validation_status="accepted",
        validation_errors=validation.errors,
        row_count=validation.row_count,
        error_message=None,
    )
    stats = repo.apply_accepted_rows(
        session,
        board=board,
        raw_fetch=raw,
        rows=validation.accepted_rows,
        now=now,
    )
    session.commit()
    logger.info(
        "Accepted official monthly revenue dataset",
        extra={
            "event": "tw_monthly_revenue.accepted",
            "board": board,
            "period": validation.reporting_period_yyyymm,
            "row_count": validation.row_count,
            "stats": stats,
            "raw_fetch_id": raw.id,
            "body_ref_fetch_id": body_ref_fetch_id,
            "row_rejects_total": validation.row_rejects_total,
        },
    )
    return BoardIngestResult(
        board=board,
        outcome="accepted",
        validation_status="accepted",
        reporting_period_yyyymm=validation.reporting_period_yyyymm,
        row_count=validation.row_count,
        raw_fetch_id=raw.id,
        stats=stats,
        errors=validation.errors,
    )


def _try_collect_advisory_lock(session: Session, *, board: str) -> bool:
    """PostgreSQL transaction-scoped per-board advisory lock; no-op on SQLite."""
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect != "postgresql":
        return True
    key = _BOARD_ADVISORY_LOCK_KEYS.get(board)
    if key is None:
        raise ValueError(f"unsupported board for advisory lock: {board}")
    result = session.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": key},
    )
    return bool(result.scalar())


def _default_fetch(url: str, **kwargs: object) -> HttpResponseMeta:
    return fetch_response_meta(url, **kwargs)  # type: ignore[arg-type]


def _header(response: HttpResponseMeta, name: str) -> str | None:
    lower = name.lower()
    for key, value in response.headers.items():
        if key.lower() == lower:
            return value
    return None
