"""Worker /health (readiness) versus /livez (liveness) (#289)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.worker as worker
from app.jobs import health as health_module
from app.jobs.health import register_background_job, reset_job_health_for_tests


@pytest.fixture
def client():
    reset_job_health_for_tests()
    yield TestClient(worker.health_app)
    reset_job_health_for_tests()


def _finished_thread() -> threading.Thread:
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return thread


def test_no_jobs_is_healthy_and_live(client) -> None:
    assert client.get("/health").status_code == 200
    live = client.get("/livez")
    assert live.status_code == 200
    assert live.json() == {"status": "ok", "role": "worker", "dead_jobs": []}


def test_stale_job_fails_readiness_but_not_liveness(client) -> None:
    register_background_job("alert-rule-evaluator", enabled=True, max_stale_seconds=120)
    with health_module._lock:
        health_module._jobs["alert-rule-evaluator"].registered_at = datetime.now(
            UTC
        ) - timedelta(seconds=600)

    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["unhealthy_jobs"] == ["alert-rule-evaluator"]
    # A restart cannot fix a job waiting on Postgres or a provider, so stay live.
    assert client.get("/livez").status_code == 200


def test_dead_thread_fails_liveness(client) -> None:
    register_background_job("quote-refresher", enabled=True, thread=_finished_thread())

    live = client.get("/livez")
    assert live.status_code == 503
    assert live.json() == {"status": "dead", "role": "worker", "dead_jobs": ["quote-refresher"]}
    assert client.get("/health").status_code == 503
