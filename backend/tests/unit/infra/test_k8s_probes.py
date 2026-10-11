"""Probe wiring for the API and worker Deployments (#289)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_K8S = Path(__file__).resolve().parents[4] / "k8s"


def _container(manifest: str, deployment: str, container: str) -> dict:
    docs = yaml.safe_load_all((_K8S / manifest).read_text(encoding="utf-8"))
    for doc in docs:
        if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == deployment:
            for item in doc["spec"]["template"]["spec"]["containers"]:
                if item["name"] == container:
                    return item
    raise AssertionError(f"{deployment}/{container} not found in {manifest}")


def test_worker_liveness_restarts_only_on_dead_threads() -> None:
    worker = _container("backend-worker.yaml", "stock-ticker-backend-worker", "worker")
    assert worker["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert worker["readinessProbe"]["httpGet"]["path"] == "/health"


@pytest.mark.parametrize(
    ("manifest", "deployment", "container"),
    [
        ("backend.yaml", "stock-ticker-backend", "backend"),
        ("backend-worker.yaml", "stock-ticker-backend-worker", "worker"),
    ],
)
def test_probes_allow_a_slow_node(manifest: str, deployment: str, container: str) -> None:
    item = _container(manifest, deployment, container)
    for probe in ("readinessProbe", "livenessProbe"):
        assert item[probe].get("timeoutSeconds", 1) > 1, probe
    assert item["livenessProbe"].get("failureThreshold", 3) >= 6
