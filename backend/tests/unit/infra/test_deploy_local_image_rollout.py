"""Local :local image import must restart app Deployments (#303)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_DEPLOY = _ROOT / "scripts" / "deploy-k3s.sh"
_LIB = _ROOT / "scripts" / "lib" / "k8s_images.sh"


def test_deploy_script_restarts_only_imported_local_app_workloads() -> None:
    script = _DEPLOY.read_text(encoding="utf-8")
    assert "restart_local_app_workloads_if_imported" in script
    assert "REFRESHED_LOCAL_BACKEND" in script
    assert "REFRESHED_LOCAL_FRONTEND" in script
    assert "mark_local_image_refreshed" in script
    restart_fn = re.search(
        r"^restart_local_app_workloads_if_imported\(\) \{\n.*?^\}\n",
        script,
        re.S | re.M,
    )
    assert restart_fn
    body = restart_fn.group(0)
    assert "stock-ticker-backend" in body
    assert "stock-ticker-backend-worker" in body
    assert "stock-ticker-frontend" in body
    assert "stock-ticker-postgres" not in body
    assert "stock-ticker-redis" not in body
    assert "rollout restart" in body
    assert "Failed to restart deployment/" in body
    assert "cronjob" not in body.lower()
    assert "stock-ticker-aws-health-heartbeat" not in body
    # Restart happens after apply, before waiting for pods.
    deploy_fn = re.search(r"^deploy_manifests\(\) \{\n.*?^\}\n", script, re.S | re.M)
    assert deploy_fn
    body = deploy_fn.group(0)
    apply_idx = body.index("apply_frontend_ingress_host")
    restart_idx = body.index("restart_local_app_workloads_if_imported")
    wait_idx = body.index('echo "Waiting for Redis"')
    assert apply_idx < restart_idx < wait_idx
    assert "Waiting for frontend" in body
    assert "Frontend rollout failed." in body
    assert script.rindex("deploy_manifests") < script.index("Deployment complete")
    k3d_import = _extract_func(script, "import_into_k3d")
    skip_return = k3d_import.index("return 0")
    assert skip_return < k3d_import.index("mark_local_image_refreshed")
    k3s_import = _extract_func(script, "import_into_k3s")
    skip_echo = k3s_import.index("Skipping app image import")
    first_mark = k3s_import.index("mark_local_image_refreshed")
    assert skip_echo < first_mark
    assert 'should_skip_app_image_import' in k3s_import[:first_mark]


def _extract_func(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", script, re.S | re.M)
    assert match, name
    return match.group(0)


def _run_restart(tmp_path: Path, *, backend: str, frontend: str) -> str:
    script = _DEPLOY.read_text(encoding="utf-8")
    kubectl = tmp_path / "kubectl"
    log = tmp_path / "kubectl.log"
    kubectl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"${FAKE_KUBECTL_LOG}\"\n",
        encoding="utf-8",
    )
    kubectl.chmod(0o755)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        f'source "{_LIB}"\n'
        "BACKEND_IMAGE=stock-ticker-backend:local\n"
        "FRONTEND_IMAGE=stock-ticker-frontend:local\n"
        "NAMESPACE=stock-ticker\n"
        f'REFRESHED_LOCAL_BACKEND={backend}\n'
        f'REFRESHED_LOCAL_FRONTEND={frontend}\n'
        "BACKEND_READY_BEFORE=1\n"
        "WORKER_READY_BEFORE=1\n"
        "FRONTEND_READY_BEFORE=1\n"
        + _extract_func(script, "deployment_was_ready")
        + _extract_func(script, "restart_local_app_workloads_if_imported")
        + "\nrestart_local_app_workloads_if_imported\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        env={**os.environ, "KUBECTL_CMD": str(kubectl), "FAKE_KUBECTL_LOG": str(log)},
        capture_output=True,
        text=True,
        check=True,
    )
    recorded = log.read_text(encoding="utf-8") if log.exists() else ""
    return result.stdout + "\n" + recorded


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_restart_runs_for_imported_local_backend_and_frontend(tmp_path: Path) -> None:
    output = _run_restart(tmp_path, backend="1", frontend="1")
    assert "rollout restart deployment/stock-ticker-backend" in output
    assert "rollout restart deployment/stock-ticker-backend-worker" in output
    assert "rollout restart deployment/stock-ticker-frontend" in output
    assert "postgres" not in output
    assert "redis" not in output


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_restart_skips_when_no_local_image_was_imported(tmp_path: Path) -> None:
    output = _run_restart(tmp_path, backend="0", frontend="0")
    assert "Skipping app Deployment restart" in output
    assert "rollout restart" not in output


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_restart_backend_only_does_not_touch_frontend(tmp_path: Path) -> None:
    output = _run_restart(tmp_path, backend="1", frontend="0")
    assert "rollout restart deployment/stock-ticker-backend" in output
    assert "rollout restart deployment/stock-ticker-backend-worker" in output
    assert "frontend" not in output


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_restart_frontend_only_does_not_touch_backend(tmp_path: Path) -> None:
    output = _run_restart(tmp_path, backend="0", frontend="1")
    assert "rollout restart deployment/stock-ticker-frontend" in output
    assert "stock-ticker-backend" not in output
    assert "postgres" not in output
    assert "redis" not in output


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_mark_local_image_refreshed_ignores_registry_refs(tmp_path: Path) -> None:
    script = _DEPLOY.read_text(encoding="utf-8")
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        f'source "{_LIB}"\n'
        "BACKEND_IMAGE=stock-ticker-backend:local\n"
        "FRONTEND_IMAGE=stock-ticker-frontend:local\n"
        "REFRESHED_LOCAL_BACKEND=0\n"
        "REFRESHED_LOCAL_FRONTEND=0\n"
        + _extract_func(script, "mark_local_image_refreshed")
        + "mark_local_image_refreshed ghcr.io/acme/backend:sha\n"
        + "mark_local_image_refreshed stock-ticker-backend:local\n"
        + 'printf "backend=%s frontend=%s\\n" "${REFRESHED_LOCAL_BACKEND}" "${REFRESHED_LOCAL_FRONTEND}"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "backend=1 frontend=0"


_OLD_PODS = """stock-ticker-backend stock-ticker-backend-old stock-ticker-backend:local sha256:old
stock-ticker-backend-worker stock-ticker-backend-worker-old stock-ticker-backend:local sha256:old
stock-ticker-frontend stock-ticker-frontend-old stock-ticker-frontend:local sha256:old
"""

_FAKE_KUBECTL = r"""#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${FAKE_KUBECTL_LOG}"
if [[ "$*" == *"rollout restart deployment/"* ]]; then
  args="$*"
  deploy="${args##*deployment/}"
  tmp="$(mktemp)"
  while IFS=' ' read -r dep pod image image_id; do
    [[ -z "${dep}" ]] && continue
    if [[ "${dep}" == "${deploy}" ]]; then
      printf '%s %s-new %s sha256:new\n' "${dep}" "${deploy}" "${image}"
    else
      printf '%s %s %s %s\n' "${dep}" "${pod}" "${image}" "${image_id}"
    fi
  done < "${FAKE_POD_STATE}" > "${tmp}"
  mv "${tmp}" "${FAKE_POD_STATE}"
fi
exit 0
"""

_FAKE_DOCKER = r"""#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${FAKE_DOCKER_LOG}"
exit 0
"""

_FAKE_EXIT0 = r"""#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${FAKE_CMD_LOG}"
exit 0
"""


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_import_then_restart(
    tmp_path: Path,
    *,
    mode: str,
    backend_image: str = "stock-ticker-backend:local",
    frontend_image: str = "stock-ticker-frontend:local",
    namespace: str = "stock-ticker",
        skip_import: bool = False,
        previously_ready: bool = True,
        pods: str = _OLD_PODS,
    ) -> tuple[str, str, str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exe(bindir / "docker", _FAKE_DOCKER)
    _write_exe(bindir / "sudo", _FAKE_EXIT0)
    _write_exe(bindir / "k3s", _FAKE_EXIT0)
    _write_exe(bindir / "k3d", _FAKE_EXIT0)
    kubectl = tmp_path / "kubectl"
    _write_exe(kubectl, _FAKE_KUBECTL)
    pods_file = tmp_path / "pods.txt"
    pods_file.write_text(pods, encoding="utf-8")
    script = _DEPLOY.read_text(encoding="utf-8")
    importer = "import_into_k3d" if mode == "k3d" else "import_into_k3s"
    skip_line = "SKIP_IMAGE_IMPORT=1\n" if skip_import else ""
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        f'source "{_LIB}"\n'
        + _extract_func(script, "require_cmd")
        + _extract_func(script, "should_skip_app_image_import")
        + _extract_func(script, "mark_local_image_refreshed")
        + _extract_func(script, "deployment_was_ready")
        + _extract_func(script, "restart_local_app_workloads_if_imported")
        + _extract_func(script, importer)
        + "BACKEND_IMAGE="
        + sh_quote(backend_image)
        + "\nFRONTEND_IMAGE="
        + sh_quote(frontend_image)
        + "\nNAMESPACE="
        + sh_quote(namespace)
        + "\nK3D_CLUSTER_NAME=stock-ticker\n"
        + "REFRESHED_LOCAL_BACKEND=0\nREFRESHED_LOCAL_FRONTEND=0\n"
        + f"BACKEND_READY_BEFORE={'1' if previously_ready else '0'}\n"
        + f"WORKER_READY_BEFORE={'1' if previously_ready else '0'}\n"
        + f"FRONTEND_READY_BEFORE={'1' if previously_ready else '0'}\n"
        + skip_line
        + f"{importer}\n"
        + 'printf "FLAGS backend=%s frontend=%s\\n" '
        + '"${REFRESHED_LOCAL_BACKEND}" "${REFRESHED_LOCAL_FRONTEND}"\n'
        + "restart_local_app_workloads_if_imported\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "KUBECTL_CMD": str(kubectl),
        "FAKE_KUBECTL_LOG": str(tmp_path / "kubectl.log"),
        "FAKE_POD_STATE": str(pods_file),
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
        "FAKE_CMD_LOG": str(tmp_path / "cmd.log"),
    }
    result = subprocess.run(
        ["bash", str(harness)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    kubectl_log = (tmp_path / "kubectl.log").read_text(encoding="utf-8") if (tmp_path / "kubectl.log").exists() else ""
    cmd_log = (tmp_path / "cmd.log").read_text(encoding="utf-8") if (tmp_path / "cmd.log").exists() else ""
    return result.stdout, kubectl_log, pods_file.read_text(encoding="utf-8"), cmd_log


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("mode", ["k3s", "k3d"])
def test_local_import_replaces_running_pods_and_keeps_local_tag(tmp_path: Path, mode: str) -> None:
    stdout, kubectl_log, pods, cmd_log = _run_import_then_restart(tmp_path, mode=mode)
    assert "FLAGS backend=1 frontend=1" in stdout
    assert "rollout restart deployment/stock-ticker-backend" in kubectl_log
    assert "rollout restart deployment/stock-ticker-backend-worker" in kubectl_log
    assert "rollout restart deployment/stock-ticker-frontend" in kubectl_log
    assert "stock-ticker-backend-old" not in pods
    assert "stock-ticker-backend-new stock-ticker-backend:local sha256:new" in pods
    assert "stock-ticker-backend-worker-new stock-ticker-backend:local sha256:new" in pods
    assert "stock-ticker-frontend-new stock-ticker-frontend:local sha256:new" in pods
    assert "postgres" not in kubectl_log
    assert "redis" not in kubectl_log
    if mode == "k3s":
        assert "images import" in cmd_log


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_fresh_install_import_does_not_restart_unready_pods(tmp_path: Path) -> None:
    stdout, kubectl_log, pods, _cmd_log = _run_import_then_restart(
        tmp_path,
        mode="k3s",
        previously_ready=False,
    )
    assert "FLAGS backend=1 frontend=1" in stdout
    assert "no previously ready app pods" in stdout
    assert "rollout restart" not in kubectl_log
    assert "stock-ticker-backend-old" in pods
    assert "sha256:new" not in pods


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_skip_import_does_not_replace_running_pods(tmp_path: Path) -> None:
    stdout, kubectl_log, pods, cmd_log = _run_import_then_restart(tmp_path, mode="k3s", skip_import=True)
    assert "FLAGS backend=0 frontend=0" in stdout
    assert "Skipping app Deployment restart" in stdout
    assert "rollout restart" not in kubectl_log
    assert "stock-ticker-backend-old" in pods
    assert "sha256:old" in pods
    assert "sha256:new" not in pods
    assert "images import" not in cmd_log


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_registry_images_do_not_replace_running_pods(tmp_path: Path) -> None:
    stdout, kubectl_log, pods, cmd_log = _run_import_then_restart(
        tmp_path,
        mode="k3s",
        backend_image="ghcr.io/acme/backend:sha",
        frontend_image="ghcr.io/acme/frontend:sha",
    )
    assert "FLAGS backend=0 frontend=0" in stdout
    assert "rollout restart" not in kubectl_log
    assert "stock-ticker-backend-old" in pods
    assert "sha256:new" not in pods
    assert "images import" not in cmd_log


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_custom_local_image_and_namespace_restart_that_deployment(tmp_path: Path) -> None:
    pods = """stock-ticker-backend stock-ticker-backend-old custom-backend:dev sha256:old
stock-ticker-backend-worker stock-ticker-backend-worker-old custom-backend:dev sha256:old
stock-ticker-frontend stock-ticker-frontend-old stock-ticker-frontend:local sha256:old
"""
    stdout, kubectl_log, pods_out, _cmd_log = _run_import_then_restart(
        tmp_path,
        mode="k3d",
        backend_image="custom-backend:dev",
        frontend_image="stock-ticker-frontend:local",
        namespace="acctest",
        pods=pods,
    )
    assert "FLAGS backend=1 frontend=1" in stdout
    assert "-n acctest rollout restart deployment/stock-ticker-backend" in kubectl_log
    assert "stock-ticker-backend-new custom-backend:dev sha256:new" in pods_out
    assert "stock-ticker-frontend-new stock-ticker-frontend:local sha256:new" in pods_out
    assert "postgres" not in kubectl_log
    assert "redis" not in kubectl_log
