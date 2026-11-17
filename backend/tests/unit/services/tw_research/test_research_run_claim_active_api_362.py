"""API as_dict must not expose claim tokens (#362 polish)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.tw_research.research_run import ResearchRun


def test_as_dict_exposes_claim_active_not_token() -> None:
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    run = ResearchRun(
        research_run_id="rr_claim",
        user_id="u1",
        phase="phase1_first_pass",
        status="queued",
        job_claim_token="secret_claim_token_value",
        job_claim_expires_at=future,
        pending_first_pass_job_id="fpj_pending",
        phase2_claim_token="secret_phase2_token",
        phase2_claim_expires_at=future,
    )
    payload = run.as_dict()
    assert "jobClaimToken" not in payload
    assert "phase2ClaimToken" not in payload
    assert "secret_claim_token_value" not in str(payload)
    assert "secret_phase2_token" not in str(payload)
    assert payload["jobClaimActive"] is True
    assert payload["phase2ClaimActive"] is True
    assert payload["jobClaimExpiresAt"] == future
    assert payload["pendingFirstPassJobId"] == "fpj_pending"


def test_as_dict_claim_active_false_when_expired() -> None:
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    run = ResearchRun(
        research_run_id="rr_expired",
        user_id="u1",
        job_claim_token="tok",
        job_claim_expires_at=past,
    )
    payload = run.as_dict()
    assert payload["jobClaimActive"] is False
    assert "jobClaimToken" not in payload
