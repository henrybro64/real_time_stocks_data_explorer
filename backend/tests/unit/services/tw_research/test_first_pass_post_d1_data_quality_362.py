"""Post-D1 First-Pass data quality (#362): transient PE ≠ sticky partial."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.repositories import tw_research_repository as repo_mod
from app.services.tw_research.first_pass import (
    classify_field_reason,
    field_reason_is_retryable,
)
from app.services.tw_research.first_pass_job import (
    FirstPassSymbolRecord,
    _enrich_symbol_payload,
    _symbol_status,
)
from app.services.tw_research.enrichment import (
    EnrichmentResult,
    ProvenanceField,
    _enrich_valuation,
)


def _pe_field(reason: str) -> ProvenanceField:
    return ProvenanceField(
        field="official_pe",
        status="unavailable",
        source="key_stats_service",
        reason=reason,
    )


def test_transient_pe_warning_is_retryable_and_failed_status() -> None:
    reason = "transient_timeout:TimeoutError"
    assert classify_field_reason(reason) == "timeout"
    assert field_reason_is_retryable(reason) is True

    record = FirstPassSymbolRecord(
        stock_id="TW:6202",
        stock_name="x",
        name_status="ok",
        name_source="catalog",
        exchange="TWSE",
        market="TW",
        sector_theme_keys=[],
        fields={"official_pe": _pe_field(reason)},
        completeness={"fieldsOk": 3, "fieldsUnavailable": 1},
    )
    assert _symbol_status(record) == "failed"
    payload = _enrich_symbol_payload(record, status="failed", previous_payload=None)
    assert payload["nextRetryAt"] is not None
    assert payload["failureClass"] == "timeout"
    assert payload["retryExhausted"] is False
    assert "providerDiagnostics" in payload
    assert "providers" in payload["providerDiagnostics"]


def test_blank_official_pe_stays_non_retryable_partial() -> None:
    reason = "TWSE official daily P/E did not report a usable P/E value (peStatus=missing)."
    assert field_reason_is_retryable(reason) is False
    record = FirstPassSymbolRecord(
        stock_id="TW:3363",
        stock_name="x",
        name_status="ok",
        name_source="catalog",
        exchange="TWSE",
        market="TW",
        sector_theme_keys=[],
        fields={"official_pe": _pe_field(reason)},
        completeness={"fieldsOk": 4, "fieldsUnavailable": 1},
    )
    assert _symbol_status(record) == "partial"
    payload = _enrich_symbol_payload(record, status="partial", previous_payload=None)
    assert payload["nextRetryAt"] is None


def test_enrich_valuation_uses_transient_warning_not_generic_unavailable() -> None:
    result = EnrichmentResult(stock_id="TW:2330", qualified=True)

    def loader(_sid: str):
        return {
            "peRatio": None,
            "valuationWarning": "rate_limited:HTTP429:HttpFetchError:retry_after=30",
            "peRatioSource": "TWSE official daily P/E",
        }

    _enrich_valuation(result, "TW:2330", loader=loader, captured_at="2024-06-28T00:00:00Z")
    pe = result.fields["official_pe"]
    assert pe.status == "unavailable"
    assert pe.reason.startswith("rate_limited:")
    assert field_reason_is_retryable(pe.reason) is True


def test_partial_with_retryable_reason_is_reclaimable() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        status="partial",
        payload_json={
            "fields": {
                "official_pe": {
                    "status": "unavailable",
                    "reason": "transient_http:503:HttpFetchError",
                },
                "institutional_5d": {"status": "ok", "value": {"x": 1}},
            },
            "nextRetryAt": (now - timedelta(seconds=1)).isoformat(),
            "retryExhausted": False,
        },
    )
    assert repo_mod._first_pass_symbol_needs_work(row, now=now) is True


def test_partial_authoritative_gap_not_reclaimed_without_flag() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        status="partial",
        payload_json={
            "fields": {
                "official_pe": {
                    "status": "unavailable",
                    "reason": "official_pe_unavailable",
                }
            },
            "retryExhausted": False,
        },
    )
    assert repo_mod._first_pass_symbol_needs_work(row, now=now) is False


def test_partial_revalidate_flag_schedules_work_without_dropping_ok_fields() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        status="partial",
        payload_json={
            "revalidatePartial": True,
            "fields": {
                "official_pe": {
                    "status": "unavailable",
                    "reason": "official_pe_unavailable",
                },
                "institutional_5d": {"status": "ok", "value": {"lots": 1}},
            },
            "nextRetryAt": None,
            "retryExhausted": False,
        },
    )
    assert repo_mod._first_pass_symbol_needs_work(row, now=now) is True

    # Re-collect path preserves prior OK fields when new PE is still transient.
    prior = dict(row.payload_json)
    record = FirstPassSymbolRecord(
        stock_id="TW:6451",
        stock_name="x",
        name_status="ok",
        name_source="catalog",
        exchange="TPEx",
        market="TW",
        sector_theme_keys=[],
        fields={
            "official_pe": _pe_field("transient_timeout:TimeoutError"),
            "institutional_5d": ProvenanceField(
                field="institutional_5d",
                status="unavailable",
                source="fixture",
                reason="transient_timeout:TimeoutError",
            ),
        },
        completeness={"fieldsOk": 0, "fieldsUnavailable": 2},
    )
    payload = _enrich_symbol_payload(record, status="failed", previous_payload=prior)
    assert payload["fields"]["institutional_5d"]["status"] == "ok"
    assert payload["fields"]["institutional_5d"]["value"] == {"lots": 1}
    assert payload.get("revalidatePartial") is None
    assert payload["revalidationAttempts"] == 1
    assert payload["revalidationOutcome"] == "failed"
    assert payload.get("revalidatedAt")
    assert payload["nextRetryAt"] is not None


def test_revalidate_still_partial_is_one_shot_not_reclaimed() -> None:
    """Flagged partial → re-collect still blank PE → needs_work False next tick."""
    now = datetime.now(UTC)
    prior = {
        "revalidatePartial": True,
        "fields": {
            "official_pe": {
                "status": "unavailable",
                "reason": "official_pe_unavailable",
            },
            "institutional_5d": {"status": "ok", "value": {"lots": 1}},
        },
        "nextRetryAt": None,
        "retryExhausted": False,
    }
    blank = (
        "TWSE official daily P/E did not report a usable P/E value (peStatus=missing)."
    )
    record = FirstPassSymbolRecord(
        stock_id="TW:6202",
        stock_name="x",
        name_status="ok",
        name_source="catalog",
        exchange="TWSE",
        market="TW",
        sector_theme_keys=[],
        fields={"official_pe": _pe_field(blank)},
        completeness={"fieldsOk": 4, "fieldsUnavailable": 1},
    )
    payload = _enrich_symbol_payload(record, status="partial", previous_payload=prior)
    assert payload.get("revalidatePartial") is None
    assert payload["revalidationAttempts"] == 1
    assert payload["revalidationOutcome"] == "partial"
    assert payload["nextRetryAt"] is None

    row = SimpleNamespace(status="partial", payload_json=payload)
    assert repo_mod._first_pass_symbol_needs_work(row, now=now) is False

    # Even if an operator re-sets the flag, attempts >= 1 blocks reclaim via flag.
    payload["revalidatePartial"] = True
    row2 = SimpleNamespace(status="partial", payload_json=payload)
    assert repo_mod._first_pass_symbol_needs_work(row2, now=now) is False


def test_revalidate_to_ok_records_outcome() -> None:
    prior = {
        "revalidatePartial": True,
        "fields": {
            "official_pe": {
                "status": "unavailable",
                "reason": "official_pe_unavailable",
            }
        },
        "nextRetryAt": None,
        "retryExhausted": False,
    }
    record = FirstPassSymbolRecord(
        stock_id="TW:2330",
        stock_name="x",
        name_status="ok",
        name_source="catalog",
        exchange="TWSE",
        market="TW",
        sector_theme_keys=[],
        fields={
            "official_pe": ProvenanceField(
                field="official_pe",
                status="ok",
                source="key_stats_service",
                value={"peRatio": 20.0},
            )
        },
        completeness={"fieldsOk": 5, "fieldsUnavailable": 0},
    )
    payload = _enrich_symbol_payload(record, status="ok", previous_payload=prior)
    assert payload.get("revalidatePartial") is None
    assert payload["revalidationAttempts"] == 1
    assert payload["revalidationOutcome"] == "ok"
    assert payload["nextRetryAt"] is None
    assert repo_mod._first_pass_symbol_needs_work(
        SimpleNamespace(status="ok", payload_json=payload),
        now=datetime.now(UTC),
    ) is False


def test_enrich_valuation_transient_pe_marks_pb_yield_not_retrieved() -> None:
    result = EnrichmentResult(stock_id="TW:2330", qualified=True)

    def loader(_sid: str):
        return {
            "peRatio": None,
            "valuationWarning": "rate_limited:HTTP429:HttpFetchError:retry_after=30",
            "peRatioSource": "TWSE official daily P/E",
        }

    _enrich_valuation(result, "TW:2330", loader=loader, captured_at="2024-06-28T00:00:00Z")
    assert result.fields["official_pb"].reason == "official_pb_not_retrieved"
    assert result.fields["official_dividend_yield"].reason == "official_yield_not_retrieved"


def test_provider_budget_delta_is_per_symbol_not_cumulative() -> None:
    from app.services.tw_research.first_pass import (
        bounded_provider_diagnostics,
        provider_budget_delta,
    )

    before = {"twse_pe": {"requests": 0, "rate_limited": 0, "timeouts": 0}}
    after_sym1 = {"twse_pe": {"requests": 1, "rate_limited": 1, "timeouts": 0}}
    after_sym2 = {"twse_pe": {"requests": 1, "rate_limited": 1, "timeouts": 0}}

    d1 = provider_budget_delta(before, after_sym1)
    d2 = provider_budget_delta(after_sym1, after_sym2)
    assert d1["twse_pe"]["rate_limited"] == 1
    assert d2 == {}  # sticky totals must not appear as a second-symbol delta

    sticky = SimpleNamespace(
        classification="rate_limited", status_code=429, retry_after_seconds=30
    )
    diag1 = bounded_provider_diagnostics(
        before=before, after=after_sym1, last_http=sticky
    )
    diag2 = bounded_provider_diagnostics(
        before=after_sym1, after=after_sym2, last_http=None
    )
    assert diag1["lastHttp"]["statusCode"] == 429
    assert "lastHttp" not in diag2
    assert len(str(diag1)) < 4000


def test_recovery_sql_casts_json_column_not_bare_jsonb_concat() -> None:
    """payload_json is json (migration 0024); bare || jsonb fails — cast pattern required."""
    from tests.repo_root import repo_root

    doc = (
        repo_root() / "docs" / "tw-research-first-pass-post-d1-data-quality-362.md"
    ).read_text(encoding="utf-8")
    assert "payload_json::jsonb || jsonb_build_object(" in doc
    assert ")::json" in doc
    assert "official_pe_unavailable" in doc
    assert "BEGIN;" in doc
    # Must not recommend bare json || jsonb without cast.
    assert "SET payload_json = payload_json\n     || jsonb_build_object" not in doc


def test_reconcile_persists_when_only_first_pass_progress_counts_change() -> None:
    from app.services.tw_research.research_run import ResearchRunStore

    service = ResearchRunStore.__new__(ResearchRunStore)
    run = SimpleNamespace(
        phase="phase1_first_pass",
        status="running",
        first_pass_snapshot_id=None,
        technical_scan_id=None,
        first_pass_job_id="fpj_test",
        notes=[],
        dimensions={
            "firstPassProgress": {
                "processed": 3,
                "okCount": 2,
                "partialCount": 1,
                "failedCount": 0,
            }
        },
        updated_at=None,
    )

    def fake_from_job(_run):
        _run.dimensions["firstPassProgress"] = {
            "processed": 13,
            "okCount": 9,
            "partialCount": 4,
            "failedCount": 0,
        }

    service._reconcile_from_scan_locked = lambda _r: None  # type: ignore[method-assign]
    service._reconcile_from_job_locked = fake_from_job  # type: ignore[method-assign]
    assert service._reconcile_locked(run) is True
    assert run.updated_at is not None
    assert run.dimensions["firstPassProgress"]["processed"] == 13

