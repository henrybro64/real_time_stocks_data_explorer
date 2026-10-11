"""k8s Ingress + auth secret packaging (#241, #267)."""

from __future__ import annotations

from pathlib import Path

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
