"""Validate suspended TW research CronJob packaging (#330)."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_CRON = _ROOT / "k8s" / "backend-tw-research-schedule-cronjob.yaml"


def test_tw_research_schedule_cronjob_is_suspended_with_taipei_timezone() -> None:
    text = _CRON.read_text(encoding="utf-8")
    assert "kind: CronJob" in text
    assert "suspend: true" in text
    assert "timeZone: Asia/Taipei" in text
    assert 'schedule: "30 15-18 * * 1-5"' in text
    assert "concurrencyPolicy: Forbid" in text
    assert "activeDeadlineSeconds: 900" in text
    assert "X-Tw-Research-Schedule-Token" in text
    assert "TW_RESEARCH_SCHEDULE_TOKEN" in text
    # Token must come from the auth secret, never a literal in the manifest.
    assert "real-schedule-token" not in text
    assert "replace-with" not in text.lower()


def test_tw_research_schedule_cronjob_is_in_kustomization() -> None:
    kustomization = (_ROOT / "k8s" / "kustomization.yaml").read_text(encoding="utf-8")
    assert "backend-tw-research-schedule-cronjob.yaml" in kustomization


def test_tw_research_schedule_cronjob_yaml_parses() -> None:
    import yaml

    docs = list(yaml.safe_load_all(_CRON.read_text(encoding="utf-8")))
    assert len(docs) == 1
    cron = docs[0]
    assert cron["kind"] == "CronJob"
    assert cron["spec"]["suspend"] is True
    assert cron["spec"]["timeZone"] == "Asia/Taipei"
    assert cron["spec"]["schedule"] == "30 15-18 * * 1-5"
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"