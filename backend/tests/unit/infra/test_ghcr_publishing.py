"""Offline checks for GHCR publishing and optional k3s image overrides (#214)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "docs").is_dir() and (candidate / "k8s").is_dir():
            return candidate
    raise AssertionError(f"could not locate repo root from {here}")


_REPO = _repo_root()
_WORKFLOW = _REPO / ".github" / "workflows" / "publish-ghcr.yml"
_TEST_AND_SMOKE = _REPO / ".github" / "workflows" / "test-and-smoke.yml"
_LIB = _REPO / "scripts" / "lib" / "k8s_images.sh"
_DEPLOY = _REPO / "scripts" / "deploy-k3s.sh"
_DOCS = _REPO / "docs" / "kubernetes.md"
_BACKEND_DEPLOY = _REPO / "k8s" / "backend.yaml"
_FRONTEND_DEPLOY = _REPO / "k8s" / "frontend.yaml"


def _run_lib(*args: str, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        ["bash", str(_LIB), *args],
        check=False,
        capture_output=True,
        text=True,
        env=merged,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _eval_lib(expression: str) -> str:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{_LIB}"; {expression}',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_ghcr_image_names_are_lowercase_and_separate_packages() -> None:
    backend = _run_lib("backend-name").strip()
    frontend = _run_lib("frontend-name").strip()
    assert backend == "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend"
    assert frontend == "ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend"
    assert backend == backend.lower()
    assert frontend == frontend.lower()
    assert backend != frontend


def test_ghcr_image_names_honor_owner_override() -> None:
    backend = _run_lib("backend-name", env={"GHCR_OWNER": "OtherUser"}).strip()
    assert backend == "ghcr.io/otheruser/real_time_stocks_data_explorer-backend"


def test_registry_vs_local_image_detection() -> None:
    assert _eval_lib('is_registry_image "ghcr.io/acme/app:sha" && echo yes') == "yes"
    assert _eval_lib(
        'is_registry_image "stock-ticker-backend:local" && echo yes || echo no'
    ) == "no"


def test_image_pull_policy_for_local_latest_and_sha() -> None:
    assert _eval_lib('image_pull_policy_for "stock-ticker-backend:local"') == "Never"
    assert (
        _eval_lib(
            'image_pull_policy_for "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:latest"'
        )
        == "Always"
    )
    assert (
        _eval_lib(
            'image_pull_policy_for "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:abc123"'
        )
        == "IfNotPresent"
    )


_DIGEST = "sha256:" + ("ab" * 32)


def test_image_ref_parsers_handle_digest_and_tag() -> None:
    digest_only = (
        "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend@" + _DIGEST
    )
    tagged_digest = (
        "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:deadbeef@" + _DIGEST
    )
    assert (
        _eval_lib(f'image_name_part "{digest_only}"')
        == "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend"
    )
    assert _eval_lib(f'image_digest_part "{digest_only}"') == _DIGEST
    assert _eval_lib(f'image_pull_policy_for "{digest_only}"') == "IfNotPresent"
    assert (
        _eval_lib(f'image_name_part "{tagged_digest}"')
        == "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend"
    )
    assert _eval_lib(f'image_tag_part "{tagged_digest}"') == "deadbeef"
    assert _eval_lib(f'image_digest_part "{tagged_digest}"') == _DIGEST
    assert _eval_lib(f'image_pull_policy_for "{tagged_digest}"') == "IfNotPresent"


def test_default_k8s_manifests_still_use_local_never_pull() -> None:
    backend = _BACKEND_DEPLOY.read_text(encoding="utf-8")
    frontend = _FRONTEND_DEPLOY.read_text(encoding="utf-8")
    assert "image: stock-ticker-backend:local" in backend
    assert "image: stock-ticker-frontend:local" in frontend
    assert "imagePullPolicy: Never" in backend
    assert "imagePullPolicy: Never" in frontend


def test_image_overlay_rewrites_ghcr_images_and_pull_policy(tmp_path: Path) -> None:
    backend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:deadbeef"
    frontend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend:deadbeef"
    _run_lib("write-overlay", str(tmp_path), backend, frontend, "ghcr-pull")
    overlay = yaml.safe_load((tmp_path / "kustomization.yaml").read_text(encoding="utf-8"))
    assert overlay["resources"] == ["k8s"]
    assert (tmp_path / "k8s" / "backend.yaml").is_file()
    images = {item["name"]: item for item in overlay["images"]}
    assert images["stock-ticker-backend"]["newName"].endswith(
        "real_time_stocks_data_explorer-backend"
    )
    assert images["stock-ticker-backend"]["newTag"] == "deadbeef"
    assert images["stock-ticker-frontend"]["newTag"] == "deadbeef"
    patches = "\n".join(patch["patch"] for patch in overlay["patches"])
    assert "IfNotPresent" in patches
    assert "imagePullSecrets" in patches
    assert "ghcr-pull" in patches
    assert "Never" not in patches


def test_image_overlay_rewrites_digest_refs(tmp_path: Path) -> None:
    backend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend@" + _DIGEST
    frontend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend@" + _DIGEST
    _run_lib("write-overlay", str(tmp_path), backend, frontend)
    overlay = yaml.safe_load((tmp_path / "kustomization.yaml").read_text(encoding="utf-8"))
    images = {item["name"]: item for item in overlay["images"]}
    assert images["stock-ticker-backend"]["digest"] == _DIGEST
    assert "newTag" not in images["stock-ticker-backend"]
    assert images["stock-ticker-frontend"]["digest"] == _DIGEST
    patches = "\n".join(patch["patch"] for patch in overlay["patches"])
    assert "IfNotPresent" in patches
    assert "Always" not in patches


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_image_overlay_kustomize_build_rewrites_deployments(tmp_path: Path) -> None:
    backend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:deadbeef"
    frontend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend:deadbeef"
    _run_lib("write-overlay", str(tmp_path), backend, frontend, "ghcr-pull")
    result = subprocess.run(
        ["kubectl", "kustomize", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    assert "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:deadbeef" in rendered
    assert "ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend:deadbeef" in rendered
    assert "stock-ticker-backend:local" not in rendered
    assert "imagePullPolicy: IfNotPresent" in rendered
    assert "name: ghcr-pull" in rendered


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
def test_image_overlay_kustomize_build_rewrites_digest_refs(tmp_path: Path) -> None:
    backend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend@" + _DIGEST
    frontend = "ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend@" + _DIGEST
    _run_lib("write-overlay", str(tmp_path), backend, frontend)
    result = subprocess.run(
        ["kubectl", "kustomize", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "ghcr.io/henrybro64/real_time_stocks_data_explorer-backend@" + _DIGEST
        in result.stdout
    )
    assert "imagePullPolicy: IfNotPresent" in result.stdout


def test_publish_workflow_is_ci_gated_and_uses_github_token() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML 1.1 treats the key `on` as boolean True.
    trigger = workflow.get("on", workflow.get(True))
    assert trigger is not None
    assert "pull_request" not in trigger
    assert trigger["workflow_run"]["workflows"] == ["Test And Smoke"]
    assert trigger["workflow_run"]["types"] == ["completed"]
    assert trigger["workflow_run"]["branches"] == ["main", "master", "v*.*.*"]
    assert "push" not in trigger
    assert "workflow_dispatch" in trigger
    assert workflow["permissions"] == {
        "contents": "read",
        "packages": "write",
        "actions": "read",
    }
    decide_if = workflow["jobs"]["decide"]["if"]
    assert "workflow_run.event == 'push'" in decide_if
    assert "workflow_run.conclusion" not in decide_if
    assert "workflow_dispatch" in decide_if
    assert "default_branch" in decide_if
    assert workflow["jobs"]["publish"]["needs"] in ("decide", ["decide"])
    assert "needs.decide.outputs.publish" in workflow["jobs"]["publish"]["if"]
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.GITHUB_TOKEN" in text
    assert "CR_PAT" not in text
    assert "GHCR_TOKEN" not in text
    assert "workflow_run.head_sha" in text
    assert "github.ref_name" in text
    assert "backend-pytest" in text
    assert "frontend-playwright" in text
    assert "k3s-smoke" in text
    assert "jq " in text or "jq -r" in text
    assert "platforms: linux/amd64" in text
    assert "backend/Dockerfile" in text
    assert "frontend/Dockerfile" in text
    assert "org.opencontainers.image.source" in text
    assert "org.opencontainers.image.revision" in text
    assert "cache-to: type=registry" in text
    assert ":buildcache" in text
    assert "secrets.GHCR" not in text


def test_existing_ci_workflow_does_not_publish_ghcr() -> None:
    smoke = yaml.safe_load(_TEST_AND_SMOKE.read_text(encoding="utf-8"))
    assert smoke["name"] == "Test And Smoke"
    trigger = smoke.get("on", smoke.get(True))
    assert trigger["push"]["tags"] == ["v*.*.*"]
    text = _TEST_AND_SMOKE.read_text(encoding="utf-8")
    assert "ghcr.io" not in text
    assert "packages: write" not in text
    assert "stock-ticker-backend:local" in text
    assert "stock-ticker-frontend:local" in text


def test_deploy_script_keeps_local_defaults_and_documents_ghcr() -> None:
    script = _DEPLOY.read_text(encoding="utf-8")
    assert "stock-ticker-backend:local" in script
    assert "stock-ticker-frontend:local" in script
    assert "scripts/lib/k8s_images.sh" in script
    assert "IMAGE_PULL_SECRET" in script
    assert "should_skip_image_build" in script
    assert "ensure_namespace" in script
    assert "ensure_image_pull_secret" in script
    assert "docs/kubernetes.md#github-container-registry" in script


def test_docs_cover_packages_tags_pull_and_k3s_switch() -> None:
    docs = _DOCS.read_text(encoding="utf-8")
    assert "## GitHub Container Registry" in docs
    assert "real_time_stocks_data_explorer-backend" in docs
    assert "real_time_stocks_data_explorer-frontend" in docs
    assert "read:packages" in docs
    assert "IMAGE_PULL_SECRET" in docs
    assert "imageID" in docs
    assert "stock-ticker-backend:local" in docs
    assert "Do not commit" in docs or "do not commit" in docs.lower()
    assert "kubectl create namespace stock-ticker" in docs
    assert "linux/amd64" in docs
    assert "buildcache" in docs
    assert "workflow_run" in docs
    assert "Test And Smoke" in docs
    assert "backend-pytest" in docs
    assert "Manage Actions access" in docs
