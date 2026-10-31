"""Read-only #287 cluster helper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT = _ROOT / "scripts" / "verify-k3s-post-deploy.sh"
_DOCS = _ROOT / "docs" / "post-deploy-verification.md"

_FAKE_KUBECTL = r"""#!/usr/bin/env bash
set -u
args="$*"
pvc_phase="${FAKE_PVC_PHASE:-Bound}"
policy="${FAKE_REDIS_POLICY:-noeviction}"
maxmem="${FAKE_REDIS_MAXMEMORY:-134217728}"
hb_ok="${FAKE_HEARTBEAT_OK:-1}"
hb_bad="${FAKE_HEARTBEAT_FAILED:-0}"
evicted="${FAKE_EVICTED:-0}"

if [[ "$args" == *"get pods -o wide"* ]]; then
  echo "NAME READY STATUS RESTARTS"
  echo "stock-ticker-backend-x 1/1 Running 0"
  exit 0
fi
if [[ "$args" == *"get deploy "* && "$args" == *jsonpath* ]]; then
  printf '1/1'
  exit 0
fi
if [[ "$args" == *"get pods -l app="* && "$args" == *restartCount* ]]; then
  printf '0 '
  exit 0
fi
if [[ "$args" == *"get pods -l app="* && "$args" == *imageID* ]]; then
  printf 'pod-x sha256:abc123\n'
  exit 0
fi
if [[ "$args" == *"get cronjob stock-ticker-aws-health-heartbeat"* ]]; then
  exit 0
fi
# jsonpath must be matched before the plain table: the decision query uses both
# --sort-by and -o jsonpath. A missing succeeded field is omitted by kubectl.
if [[ "$args" == *"get jobs"* && "$args" == *jsonpath* ]]; then
  if [[ "${FAKE_HEARTBEAT_OMIT_SUCCEEDED:-}" == "1" ]]; then
    if [[ "$args" == *"|"* ]]; then
      printf 'stock-ticker-aws-health-heartbeat-old|2020-01-01T00:00:00Z|1|0\n'
      printf 'stock-ticker-aws-health-heartbeat-new|2026-10-31T12:00:00Z||1\n'
    else
      printf 'stock-ticker-aws-health-heartbeat-old  1\n'
    fi
    exit 0
  fi
  if [[ "$args" == *"|"* ]]; then
    printf 'stock-ticker-aws-health-heartbeat-1|2026-10-31T12:00:00Z|%s|%s\n' "$hb_ok" "$hb_bad"
  else
    printf 'stock-ticker-aws-health-heartbeat-1 %s %s\n' "$hb_ok" "$hb_bad"
  fi
  exit 0
fi
if [[ "$args" == *"get jobs --sort-by"* ]]; then
  echo "NAME COMPLETIONS DURATION AGE"
  echo "stock-ticker-aws-health-heartbeat-1 1/1 10s 1h"
  exit 0
fi
if [[ "$args" == *"get pvc stock-ticker-redis-data"* ]]; then
  printf '%s' "$pvc_phase"
  exit 0
fi
if [[ "$args" == *"redis-cli INFO"* ]]; then
  printf 'used_memory_human:1.2M\r\nmaxmemory_human:128M\r\nevicted_keys:%s\r\n' "$evicted"
  exit 0
fi
if [[ "$args" == *"CONFIG GET maxmemory-policy"* ]]; then
  printf 'maxmemory-policy\n%s\n' "$policy"
  exit 0
fi
if [[ "$args" == *"CONFIG GET maxmemory"* ]]; then
  printf 'maxmemory\n%s\n' "$maxmem"
  exit 0
fi
if [[ "$args" == *"alembic current"* ]]; then
  echo "20261011_0020 (head)"
  exit 0
fi
if [[ "$args" == *"alembic heads"* ]]; then
  echo "20261011_0020 (head)"
  exit 0
fi
if [[ "$args" == *"logs deploy/stock-ticker-backend-worker"* ]]; then
  echo "quote refresher cycle ok"
  exit 0
fi
if [[ "$args" == *"urlopen"* && "$args" == *"/livez"* ]]; then
  echo '{"status":"ok"}'
  exit 0
fi
if [[ "$args" == *"urlopen"* && "$args" == *"/health"* ]]; then
  echo '{"status":"ok"}'
  exit 0
fi
if [[ "$args" == *"urlopen"* && "$args" == *"/api/auth/status"* ]]; then
  echo '{"authEnabled":true,"loggedIn":false,"providers":{"local":true,"entra":true},"user":null}'
  exit 0
fi
if [[ "$args" == *"exec"* && "$args" == *"python"* ]]; then
  echo "users 2"
  echo "oauth_identities 1"
  echo "watchlist_items 4"
  exit 0
fi
echo "unhandled: $args" >&2
exit 1
"""


def test_checklist_doc_tracks_issue_287() -> None:
    text = _DOCS.read_text(encoding="utf-8")
    assert "589878f" in text
    assert "verify-k3s-post-deploy.sh" in text
    assert "stock-ticker-redis-data" in text
    assert "#303" in text
    assert "oauth_identities" in text or "Entra" in text
    assert "Cannot reach the server" in text
    script_table = (_ROOT / "docs" / "kubernetes.md").read_text(encoding="utf-8")
    assert "verify-k3s-post-deploy.sh" in script_table


def test_verify_script_covers_redis_pvc_policy_and_identities() -> None:
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "stock-ticker-redis-data" in script
    assert "oauth_identities" in script
    assert "maxmemory-policy" in script
    assert "noeviction" in script
    assert "134217728" in script
    assert "imageID" in script
    assert "/livez" in script
    assert "aws-health-heartbeat" in script
    assert '--sort-by=.metadata.creationTimestamp' in script
    assert '{.status.succeeded}{"|"}{.status.failed}' in script


def _run_verify(tmp_path: Path, **fake_env: str) -> subprocess.CompletedProcess[str]:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(_FAKE_KUBECTL, encoding="utf-8")
    kubectl.chmod(0o755)
    env = {
        **os.environ,
        "KUBECTL_CMD": str(kubectl),
        "NAMESPACE": "stock-ticker",
        **fake_env,
    }
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_passes_healthy_cluster(tmp_path: Path) -> None:
    result = _run_verify(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cluster checks passed" in result.stdout
    assert "PVC stock-ticker-redis-data is Bound" in result.stdout
    assert "maxmemory-policy is noeviction" in result.stdout
    assert "oauth_identities 1" in result.stdout
    assert "latest aws-health-heartbeat Job succeeded" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_fails_unbound_redis_pvc(tmp_path: Path) -> None:
    result = _run_verify(tmp_path, FAKE_PVC_PHASE="Pending")
    assert result.returncode != 0
    assert "stock-ticker-redis-data phase 'Pending'" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_fails_wrong_redis_policy(tmp_path: Path) -> None:
    result = _run_verify(tmp_path, FAKE_REDIS_POLICY="allkeys-lru")
    assert result.returncode != 0
    assert "allkeys-lru" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_fails_failed_heartbeat_job(tmp_path: Path) -> None:
    result = _run_verify(tmp_path, FAKE_HEARTBEAT_OK="0", FAKE_HEARTBEAT_FAILED="1")
    assert result.returncode != 0
    assert "aws-health-heartbeat Job did not succeed" in result.stdout
    assert "PASS latest aws-health-heartbeat Job succeeded" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_fails_when_heartbeat_succeeded_is_omitted(tmp_path: Path) -> None:
    """A failed Job omits .status.succeeded, so kubectl prints `name  1`.

    An older successful Job must not hide the newest failure. A blank
    succeeded count is 0, and the helper must exit non-zero.
    """
    result = _run_verify(tmp_path, FAKE_HEARTBEAT_OMIT_SUCCEEDED="1")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "aws-health-heartbeat Job did not succeed" in result.stdout
    assert "succeeded=0" in result.stdout
    assert "failed=1" in result.stdout
    assert "PASS latest aws-health-heartbeat Job succeeded" not in result.stdout
