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


def test_deploy_script_generates_auth_secret_and_hostname_override() -> None:
    script = (_ROOT / "scripts" / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert "ensure_backend_auth_secret" in script
    assert "STOCK_TICKER_HOSTNAME" in script
    assert "apply_frontend_ingress_host" in script
