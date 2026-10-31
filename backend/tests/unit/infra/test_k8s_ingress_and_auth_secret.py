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


def test_entra_secret_is_example_only_and_gitignored() -> None:
    assert (_K8S / "backend-entra-secret.yaml.example").is_file()
    assert not (_K8S / "backend-entra-secret.yaml").exists()
    example = (_K8S / "backend-entra-secret.yaml.example").read_text(encoding="utf-8")
    assert "name: stock-ticker-entra" in example
    assert "stock-ticker-backend-entra" not in example
    assert "ENTRA_CLIENT_SECRET:" in example
    assert "ENTRA_CLIENT_ID:" not in example
    assert "sk-" not in example
    kustomization = (_K8S / "kustomization.yaml").read_text(encoding="utf-8")
    assert "backend-entra-secret.yaml" not in [
        line.strip().lstrip("- ").strip()
        for line in kustomization.splitlines()
        if line.strip().startswith("- ")
    ]
    gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "k8s/backend-entra-secret.yaml" in gitignore


def test_backend_configmap_persists_nonsecret_entra() -> None:
    backend = (_K8S / "backend.yaml").read_text(encoding="utf-8")
    worker = (_K8S / "backend-worker.yaml").read_text(encoding="utf-8")
    assert 'LOCAL_AUTH_ENABLED: "true"' in backend
    assert 'ENTRA_EXTERNAL_ID_ENABLED: "true"' in backend
    assert "ENTRA_TENANT_ID:" in backend
    assert "ENTRA_AUTHORITY:" in backend
    assert "ciamlogin.com" in backend
    assert "ENTRA_REDIRECT_URI:" in backend
    assert "callback/entra" in backend
    assert 'ENTRA_CLIENT_ID: "feeaf879-3149-48da-b4c6-485dfa8e5ba0"' in backend
    assert "ENTRA_CLIENT_SECRET:" not in backend
    assert "stock-ticker-entra" in backend
    assert "stock-ticker-backend-entra" not in backend
    assert "optional: true" in backend
    assert "name: stock-ticker-entra" in worker
    assert "stock-ticker-backend-entra" not in worker
    # Secret is last envFrom. It must not contain ENTRA_CLIENT_ID or it would
    # override the public client id in the ConfigMap.
    backend_envfrom_tail = backend.split("envFrom:")[-1]
    assert backend_envfrom_tail.index("stock-ticker-entra") > backend_envfrom_tail.index(
        "stock-ticker-backend-auth"
    )


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
[[ "$args" == *"patch secret stock-ticker-backend-auth"* ]] && { echo PATCHED_COOKIE; exit 0; }
if [[ "$args" == *"get secret stock-ticker-backend-auth"* ]]; then
  if [[ "$args" == *go-template* ]]; then printf '%s\\n' SESSION_SECRET LOCAL_AUTH_USERNAME LOCAL_AUTH_PASSWORD SESSION_COOKIE_SECURE; exit 0; fi
  [[ "$args" == *"{.data.SESSION_SECRET}"* ]] && printf '%s' "$LIVE_SESSION" | base64 && exit 0
  [[ "$args" == *SESSION_COOKIE_SECURE* ]] && printf '%s' "false" | base64 && exit 0
  exit 0
fi
exit 1
"""


def _run_apply_backend_auth_secret(
    tmp_path, *, force: bool, hostname: str = "stock-ticker.ai"
) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = (_ROOT / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    names = (
        "public_scheme",
        "public_origin",
        "_ensure_https_session_cookie",
        "_live_secret_data_value",
        "_local_auth_secret_value",
        "_live_secret_keys",
        "apply_backend_auth_secret",
    )
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
        "STOCK_TICKER_HOSTNAME": hostname,
    }
    env.pop("STOCK_TICKER_PUBLIC_SCHEME", None)
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
    assert "PATCHED_COOKIE" not in out


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("base64") is None, reason="needs bash")
def test_https_cookie_is_patched_when_auth_secret_apply_is_skipped(tmp_path) -> None:
    out = _run_apply_backend_auth_secret(
        tmp_path, force=False, hostname="stock.cloudforge64.com"
    )
    assert "Skipping apply" in out
    assert "PATCHED_COOKIE" in out
    assert "APPLIED" not in out


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
    assert "apply_backend_entra_secret" in script
    assert "render_public_origin_manifests" in script
    deploy = script.split("deploy_manifests() {", 1)[1].split("\nneeds_docker()", 1)[0]
    assert deploy.index("apply_backend_entra_secret") < deploy.index("apply -k")
    manual = (_ROOT / "docs" / "kubernetes.md").read_text(encoding="utf-8")
    assert "kubectl apply -n stock-ticker -f k8s/backend-auth-secret.yaml" in manual
    assert 'k3d cluster create stock-ticker -p "80:80@loadbalancer"' in manual


def _extract_deploy_funcs(*names: str) -> str:
    script = (_ROOT / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    funcs = []
    for name in names:
        match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", script, re.S | re.M)
        assert match, name
        funcs.append(match.group(0))
    return "\n".join(funcs)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_public_origin_matches_ingress_host(tmp_path) -> None:
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _extract_deploy_funcs("public_scheme", "public_origin", "render_public_origin_manifests")
        + "\nrender_public_origin_manifests \"$1\"\nprintf '%s\\n' \"$(public_origin)\"\n",
        encoding="utf-8",
    )
    rendered = tmp_path / "rendered"
    env = {**os.environ, "ROOT_DIR": str(_ROOT), "STOCK_TICKER_HOSTNAME": "stock.cloudforge64.com"}
    env.pop("STOCK_TICKER_PUBLIC_SCHEME", None)
    result = subprocess.run(
        ["bash", str(harness), str(rendered)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "https://stock.cloudforge64.com"
    backend = (rendered / "backend.yaml").read_text(encoding="utf-8")
    assert 'ENTRA_REDIRECT_URI: "https://stock.cloudforge64.com/api/auth/callback/entra"' in backend
    assert 'ENTRA_POST_LOGOUT_REDIRECT_URI: "https://stock.cloudforge64.com/"' in backend
    assert "host: stock.cloudforge64.com" in (rendered / "frontend-ingress.yaml").read_text(encoding="utf-8")

    lan = tmp_path / "lan"
    lan_env = {**os.environ, "ROOT_DIR": str(_ROOT), "STOCK_TICKER_HOSTNAME": "stock-ticker.ai"}
    lan_env.pop("STOCK_TICKER_PUBLIC_SCHEME", None)
    lan_result = subprocess.run(
        ["bash", str(harness), str(lan)],
        env=lan_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert lan_result.stdout.strip() == "http://stock-ticker.ai"
    lan_backend = (lan / "backend.yaml").read_text(encoding="utf-8")
    assert 'ENTRA_REDIRECT_URI: "https://stock.cloudforge64.com/api/auth/callback/entra"' in lan_backend
    assert 'ENTRA_CLIENT_ID: "feeaf879-3149-48da-b4c6-485dfa8e5ba0"' in lan_backend
    assert "http://stock-ticker.ai/api/auth/callback/entra" not in lan_backend


_ENTRA_KUBECTL = """#!/usr/bin/env bash
args="$*"
if [[ "$args" == *"get secret stock-ticker-entra"* ]]; then
  if [[ "${ENTRA_SECRET_PRESENT:-}" != "1" ]]; then
    exit 1
  fi
  if [[ "$args" == *ENTRA_CLIENT_SECRET* ]]; then
    printf '%s' "${LIVE_ENTRA_SECRET:-}" | base64
    exit 0
  fi
  exit 0
fi
if [[ "$args" == *"apply "* ]]; then
  echo APPLIED
  exit 0
fi
exit 1
"""


def _entra_apply_harness(tmp_path) -> Path:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(_ENTRA_KUBECTL, encoding="utf-8")
    kubectl.chmod(0o755)
    (tmp_path / "k8s").mkdir()
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _extract_deploy_funcs(
            "_live_secret_data_value",
            "_local_auth_secret_value",
            "apply_backend_entra_secret",
        )
        + "\napply_backend_entra_secret\n",
        encoding="utf-8",
    )
    return harness


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("base64") is None, reason="needs bash")
def test_deploy_keeps_existing_entra_secret_without_printing_it(tmp_path) -> None:
    harness = _entra_apply_harness(tmp_path)
    client_secret = "entra-secret-value-do-not-print"
    env = {
        **os.environ,
        "ROOT_DIR": str(tmp_path),
        "NAMESPACE": "stock-ticker",
        "KUBECTL_CMD": str(tmp_path / "kubectl"),
        "ENTRA_SECRET_PRESENT": "1",
        "LIVE_ENTRA_SECRET": client_secret,
    }
    result = subprocess.run(
        ["bash", str(harness)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Keeping existing Secret stock-ticker-entra" in result.stdout
    assert "APPLIED" not in result.stdout
    assert client_secret not in result.stdout
    assert client_secret not in result.stderr
    assert not (tmp_path / "k8s" / "backend-entra-secret.yaml").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_deploy_does_not_overwrite_an_empty_entra_secret_or_harvest_configmap(tmp_path) -> None:
    harness = _entra_apply_harness(tmp_path)
    env = {
        **os.environ,
        "ROOT_DIR": str(tmp_path),
        "NAMESPACE": "stock-ticker",
        "KUBECTL_CMD": str(tmp_path / "kubectl"),
        "ENTRA_SECRET_PRESENT": "1",
        "LIVE_ENTRA_SECRET": "",
        "LIVE_CLIENT_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "LIVE_CLIENT_SECRET": "harvested-secret-do-not-write",
    }
    result = subprocess.run(
        ["bash", str(harness)], env=env, capture_output=True, text=True, check=True
    )
    assert "Leaving it unchanged" in result.stdout
    assert "APPLIED" not in result.stdout
    assert "harvested-secret-do-not-write" not in result.stdout
    assert not (tmp_path / "k8s" / "backend-entra-secret.yaml").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_deploy_applies_local_entra_file_only_when_the_secret_is_absent(tmp_path) -> None:
    harness = _entra_apply_harness(tmp_path)
    secret_value = "local-file-secret-do-not-print"
    (tmp_path / "k8s" / "backend-entra-secret.yaml").write_text(
        "apiVersion: v1\nkind: Secret\nmetadata:\n  name: stock-ticker-entra\n"
        "stringData:\n  ENTRA_CLIENT_SECRET: \"" + secret_value + "\"\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ROOT_DIR": str(tmp_path),
        "NAMESPACE": "stock-ticker",
        "KUBECTL_CMD": str(tmp_path / "kubectl"),
    }
    result = subprocess.run(
        ["bash", str(harness)], env=env, capture_output=True, text=True, check=True
    )
    assert "APPLIED" in result.stdout
    assert secret_value not in result.stdout
    assert secret_value not in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_deploy_warns_when_ingress_host_differs_from_entra_redirect(tmp_path) -> None:
    harness = tmp_path / "warn.sh"
    harness.write_text(
        "set -euo pipefail\n"
        + _extract_deploy_funcs("warn_if_entra_redirect_host_differs")
        + "\nwarn_if_entra_redirect_host_differs\n",
        encoding="utf-8",
    )
    mismatch = subprocess.run(
        ["bash", str(harness)],
        env={**os.environ, "ROOT_DIR": str(_ROOT), "STOCK_TICKER_HOSTNAME": "stock-ticker.ai"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "stock.cloudforge64.com" in mismatch.stderr
    assert "stock-ticker.ai" in mismatch.stderr

    match = subprocess.run(
        ["bash", str(harness)],
        env={
            **os.environ,
            "ROOT_DIR": str(_ROOT),
            "STOCK_TICKER_HOSTNAME": "stock.cloudforge64.com",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert match.stderr == ""
