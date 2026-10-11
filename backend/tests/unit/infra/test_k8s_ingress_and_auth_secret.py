"""k8s Ingress + auth secret packaging (#241, #267)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_K8S = _ROOT / "k8s"


def test_auth_secret_is_example_only_and_gitignored() -> None:
    assert (_K8S / "backend-auth-secret.yaml.example").is_file()
    assert not (_K8S / "backend-auth-secret.yaml").exists()
    kustomization = (_K8S / "kustomization.yaml").read_text(encoding="utf-8")
    assert "backend-auth-secret.yaml" not in [
        line.strip().lstrip("- ").strip()
        for line in kustomization.splitlines()
        if line.strip().startswith("- ")
    ]
    gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "k8s/backend-auth-secret.yaml" in gitignore


def test_frontend_ingress_targets_clusterip_and_keeps_nodeport() -> None:
    ingress = (_K8S / "frontend-ingress.yaml").read_text(encoding="utf-8")
    assert "kind: Ingress" in ingress
    assert "stock-ticker-frontend" in ingress
    assert "ingressClassName: traefik" in ingress
    assert "stock-ticker.ai" in ingress
    assert (_K8S / "frontend-nodeport.yaml").is_file()
    kustomization = (_K8S / "kustomization.yaml").read_text(encoding="utf-8")
    assert "frontend-ingress.yaml" in kustomization
    assert "frontend-nodeport.yaml" in kustomization


def test_rankings_archive_example_token_is_empty() -> None:
    text = (_K8S / "backend-rankings-archive-secret.yaml.example").read_text(encoding="utf-8")
    assert 'RANKINGS_ARCHIVE_CAPTURE_TOKEN: ""' in text
    assert "replace-with-long-random-token" not in text


def test_deploy_script_preflights_live_secrets() -> None:
    script = (_ROOT / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert "preflight_live_auth_secrets" in script
    assert "FORCE_AUTH_SECRET_APPLY" in script
    assert "SESSION_SECRET is empty" in script
    assert "must both be set or both be empty" in script
    assert "RANKINGS_CAPTURE_TOKEN" in script
    assert "TAIWAN_MARKET_DATA_CAPTURE_TOKEN" in script
    smoke = (_ROOT / "scripts" / "ci-k3s-smoke.sh").read_text(encoding="utf-8")
    assert "smoke_placeholder_capture_token" in smoke


_FAKE_KUBECTL = """#!/usr/bin/env bash
args="$*"
[[ "$args" == *"apply "* ]] && { echo APPLIED; exit 0; }
if [[ "$args" == *"get secret stock-ticker-backend-auth"* ]]; then
  if [[ "$args" == *go-template* ]]; then printf '%s\\n' SESSION_SECRET LOCAL_AUTH_USERNAME LOCAL_AUTH_PASSWORD; exit 0; fi
  [[ "$args" == *"{.data.SESSION_SECRET}"* ]] && printf '%s' "$LIVE_SESSION" | base64
  exit 0
fi
exit 1
"""


def _run_apply_backend_auth_secret(tmp_path, *, force: bool) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = (_ROOT / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    names = ("_live_secret_data_value", "_local_auth_secret_value", "_live_secret_keys", "apply_backend_auth_secret")
    funcs = []
    for name in names:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", script, re.S | re.M)
        assert match, name
        funcs.append(match.group(0))
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(_FAKE_KUBECTL, encoding="utf-8")
    kubectl.chmod(0o755)
    session = "a" * 64
    (tmp_path / "k8s").mkdir()
    # Identical to live except a stale LOCAL_AUTH_RESET_PASSWORD=true (#286).
    (tmp_path / "k8s" / "backend-auth-secret.yaml").write_text(
        "stringData:\n"
        f'  SESSION_SECRET: "{session}"\n'
        '  LOCAL_AUTH_USERNAME: ""\n'
        '  LOCAL_AUTH_PASSWORD: ""\n'
        '  LOCAL_AUTH_RESET_PASSWORD: "true"\n',
        encoding="utf-8",
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\nensure_backend_auth_secret() { :; }\n"
        + "\n".join(funcs)
        + "\napply_backend_auth_secret\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ROOT_DIR": str(tmp_path),
        "NAMESPACE": "stock-ticker",
        "KUBECTL_CMD": str(kubectl),
        "LIVE_SESSION": session,
        "FORCE_AUTH_SECRET_APPLY": "1" if force else "0",
    }
    result = subprocess.run(
        ["bash", str(harness)], env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("base64") is None, reason="needs bash")
def test_deploy_script_will_not_silently_apply_a_reset_password_flag(tmp_path) -> None:
    out = _run_apply_backend_auth_secret(tmp_path, force=False)
    assert "differs on: LOCAL_AUTH_RESET_PASSWORD" in out
    assert "APPLIED" not in out
    assert "APPLIED" in _run_apply_backend_auth_secret(tmp_path / "forced", force=True)


def test_backend_worker_deployment_is_wired() -> None:
    worker = (_K8S / "backend-worker.yaml").read_text(encoding="utf-8")
    assert "name: stock-ticker-backend-worker" in worker
    assert "python" in worker
    assert "app.worker" in worker
    assert 'value: "true"' in worker
    assert "WORKER_HEALTH_HOST" in worker
    assert "alembic upgrade head" not in worker
    backend = (_K8S / "backend.yaml").read_text(encoding="utf-8")
    assert "alembic upgrade head" in backend
    assert 'RUN_BACKGROUND_JOBS: "false"' in backend
    kustomization = (_K8S / "kustomization.yaml").read_text(encoding="utf-8")
    assert "backend-worker.yaml" in kustomization


def test_deploy_script_generates_auth_secret_and_hostname_override() -> None:
    script = (_ROOT / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert "ensure_backend_auth_secret" in script
    assert "umask 077" in script
    assert "chmod 600" in script
    assert 'k3d cluster create stock-ticker -p "80:80@loadbalancer"' in script
    assert "STOCK_TICKER_HOSTNAME" in script
    assert "apply_frontend_ingress_host" in script
    manual = (_ROOT / "docs" / "kubernetes.md").read_text(encoding="utf-8")
    assert "kubectl apply -n stock-ticker -f k8s/backend-auth-secret.yaml" in manual
    assert 'k3d cluster create stock-ticker -p "80:80@loadbalancer"' in manual
