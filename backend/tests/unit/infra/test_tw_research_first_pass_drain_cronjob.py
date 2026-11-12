"""Validate suspended first-pass drain CronJob packaging (#349)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
_CRON = _ROOT / "k8s" / "backend-tw-research-first-pass-drain-cronjob.yaml"
_SCHEDULE = _ROOT / "k8s" / "backend-tw-research-schedule-cronjob.yaml"


def test_first_pass_drain_cronjob_is_suspended() -> None:
    text = _CRON.read_text(encoding="utf-8")
    assert "kind: CronJob" in text
    assert "suspend: true" in text
    assert "timeZone: Asia/Taipei" in text
    assert "concurrencyPolicy: Forbid" in text
    assert "X-Tw-Research-First-Pass-Drain-Token" in text
    assert "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN" in text
    assert "/api/tw-research/first-pass/drain" in text
    assert "real-drain-token" not in text
    assert "replace-with" not in text.lower()


def test_first_pass_drain_cronjob_is_in_kustomization() -> None:
    kustomization = (_ROOT / "k8s" / "kustomization.yaml").read_text(encoding="utf-8")
    assert "backend-tw-research-first-pass-drain-cronjob.yaml" in kustomization


def test_technical_schedule_cronjob_still_suspended() -> None:
    text = _SCHEDULE.read_text(encoding="utf-8")
    assert "suspend: true" in text


def test_first_pass_drain_cronjob_yaml_parses() -> None:
    import yaml

    docs = list(yaml.safe_load_all(_CRON.read_text(encoding="utf-8")))
    assert len(docs) == 1
    cron = docs[0]
    assert cron["kind"] == "CronJob"
    assert cron["spec"]["suspend"] is True
    assert cron["spec"]["timeZone"] == "Asia/Taipei"
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"
    assert cron["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 180
    assert cron["spec"]["schedule"] == "*/5 * * * *"
    text = _CRON.read_text(encoding="utf-8")
    assert '"maxBatches":1' in text or '"maxBatches": 1' in text
    assert '"batchSize":5' in text or '"batchSize": 5' in text
    assert '"timeBudgetSeconds":90' in text or '"timeBudgetSeconds": 90' in text
    assert "--max-time 150" in text
    assert "budget 90" in text.lower() or "90 s" in text or "90s" in text


def test_auth_secret_example_lists_drain_keys() -> None:
    text = (_ROOT / "k8s" / "backend-auth-secret.yaml.example").read_text(encoding="utf-8")
    assert "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN" in text
    assert "TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID" in text
    assert "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED" in text
