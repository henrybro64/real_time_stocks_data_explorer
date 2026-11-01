"""Read-only #287 cluster helper."""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

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
# #308 Postgres checks. Matched before the generic deploy/pods branches below.
if [[ "$args" == *"get deploy stock-ticker-postgres"* && "$args" == *appArmorProfile* ]]; then
  printf '%s' "${FAKE_PG_APPARMOR-Unconfined}"
  exit 0
fi
if [[ "$args" == *"get pods -l app=stock-ticker-postgres"* && "$args" == *".metadata.name"* ]]; then
  for ((i = 1; i <= ${FAKE_PG_PODS:-1}; i++)); do
    printf 'stock-ticker-postgres-%s Running\n' "$i"
  done
  if [[ -n "${FAKE_PG_EXTRA_PHASE:-}" ]]; then
    printf 'stock-ticker-postgres-old %s\n' "${FAKE_PG_EXTRA_PHASE}"
  fi
  exit 0
fi
if [[ "$args" == *"get pods -l app=stock-ticker-backend"* && "$args" == *migrate-db* ]]; then
  if [[ "${FAKE_NO_BACKEND_PODS:-}" != "1" ]]; then
    printf 'stock-ticker-backend-a %s\n' "${FAKE_MIGRATE_REASON-Completed}"
  fi
  if [[ "${FAKE_MIGRATE_SECOND_POD_UNFINISHED:-}" == "1" ]]; then
    printf 'stock-ticker-backend-b \n'
  fi
  exit 0
fi
if [[ "$args" == *"get networkpolicy stock-ticker-postgres-ingress"* ]]; then
  exit "${FAKE_NO_NETWORKPOLICY:-0}"
fi
if [[ "$args" == *"get pvc stock-ticker-postgres-data"* ]]; then
  printf '%s' "${FAKE_PG_PVC_PHASE:-Bound}"
  exit 0
fi
if [[ "$args" == *pg_isready* ]]; then
  echo "127.0.0.1:5432 - accepting connections"
  exit "${FAKE_PG_ISREADY_EXIT:-0}"
fi
if [[ "$args" == *"logs deploy/stock-ticker-postgres"* ]]; then
  echo "LOG:  database system is ready to accept connections"
  if [[ -n "${FAKE_PG_LOG_LINE:-}" ]]; then
    echo "${FAKE_PG_LOG_LINE}"
  fi
  exit 0
fi
if [[ "$args" == *"get endpointslices"* ]]; then
  if [[ -n "${FAKE_NO_ENDPOINTS:-}" && "$args" == *"service-name=${FAKE_NO_ENDPOINTS}"* ]]; then
    printf 'false '
  else
    printf 'true '
  fi
  exit 0
fi
if [[ "$args" == *"stock-ticker-frontend:3000"* ]]; then
  if [[ "${FAKE_FRONTEND_DOWN:-}" == "1" ]]; then
    echo "urllib.error.URLError: <urlopen error [Errno 111] Connection refused>" >&2
    exit 1
  fi
  echo "HTTP 200"
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


def test_expected_postgres_apparmor_matches_the_manifest() -> None:
    """The helper's expected value must follow k8s/postgres.yaml (#308)."""
    docs = yaml.safe_load_all((_ROOT / "k8s" / "postgres.yaml").read_text(encoding="utf-8"))
    deployment = next(d for d in docs if d and d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    expected = container["securityContext"]["appArmorProfile"]["type"]
    assert f'EXPECTED_POSTGRES_APPARMOR="{expected}"' in _SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_reports_postgres_and_endpoint_checks(tmp_path: Path) -> None:
    result = _run_verify(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    for line in (
        "PASS Postgres container AppArmor profile is Unconfined",
        "PASS exactly one Postgres pod",
        "PASS PVC stock-ticker-postgres-data is Bound",
        "PASS Postgres pg_isready",
        "PASS no signal or permission errors in the last 200 Postgres lines",
        "PASS backend migrate-db init container Completed",
        "PASS NetworkPolicy stock-ticker-postgres-ingress limits Postgres to backend and worker",
        "PASS stock-ticker-frontend has a ready endpoint",
        "PASS frontend Service answered",
    ):
        assert line in result.stdout, line
    assert "WARNING: Postgres ran initdb" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("fake_env", "message"),
    [
        ({"FAKE_PG_APPARMOR": ""}, "AppArmor profile is 'runtime default'"),
        ({"FAKE_PG_APPARMOR": "RuntimeDefault"}, "AppArmor profile is 'RuntimeDefault'"),
        ({"FAKE_PG_PODS": "2"}, "found 2 Postgres pods"),
        ({"FAKE_PG_PODS": "0"}, "found 0 Postgres pods"),
        ({"FAKE_PG_PVC_PHASE": "Lost"}, "stock-ticker-postgres-data phase 'Lost'"),
        ({"FAKE_PG_ISREADY_EXIT": "2"}, "FAIL Postgres pg_isready"),
        (
            {"FAKE_PG_LOG_LINE": "FATAL:  could not signal for checkpoint: Permission denied"},
            "Postgres log shows signal or permission errors",
        ),
        ({"FAKE_MIGRATE_REASON": "Error"}, "migrate-db init container state 'Error'"),
        (
            {"FAKE_MIGRATE_SECOND_POD_UNFINISHED": "1"},
            "migrate-db init container state 'Completed not-finished'",
        ),
        ({"FAKE_MIGRATE_REASON": ""}, "migrate-db init container state 'not-finished'"),
        ({"FAKE_NO_BACKEND_PODS": "1"}, "migrate-db init container state 'no backend pods'"),
        ({"FAKE_NO_NETWORKPOLICY": "1"}, "NetworkPolicy stock-ticker-postgres-ingress is missing"),
        ({"FAKE_NO_ENDPOINTS": "stock-ticker-frontend"}, "stock-ticker-frontend has no ready endpoint"),
        ({"FAKE_FRONTEND_DOWN": "1"}, "FAIL frontend Service did not answer"),
    ],
)
def test_verify_script_fails_postgres_and_endpoint_problems(
    tmp_path: Path, fake_env: dict[str, str], message: str
) -> None:
    result = _run_verify(tmp_path, **fake_env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert message in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_verify_script_warns_when_postgres_ran_initdb(tmp_path: Path) -> None:
    result = _run_verify(
        tmp_path,
        FAKE_PG_LOG_LINE="The files belonging to this database system will be owned by user \"postgres\".",
    )
    # A first install runs initdb legitimately, so this is a warning, not a failure.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: Postgres ran initdb" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_evicted_postgres_pod_is_not_a_competing_instance(tmp_path: Path) -> None:
    result = _run_verify(tmp_path, FAKE_PG_EXTRA_PHASE="Failed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS exactly one Postgres pod (stock-ticker-postgres-1)" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_pending_second_postgres_pod_fails(tmp_path: Path) -> None:
    result = _run_verify(tmp_path, FAKE_PG_EXTRA_PHASE="Pending")
    assert result.returncode != 0
    assert "found 2 Postgres pods" in result.stdout


def _frontend_check_program() -> str:
    script = _SCRIPT.read_text(encoding="utf-8")
    start = script.index("python - http://stock-ticker-frontend:3000/ <<'PY'\n")
    body = script[start:].split("<<'PY'\n", 1)[1]
    return body.split("\nPY\n", 1)[0]


@pytest.mark.parametrize(("status", "ok"), [(200, True), (307, True), (308, True), (502, False)])
def test_frontend_check_matches_probe_rules_and_never_follows_redirects(
    status: int, ok: bool
) -> None:
    hits: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(status)
            if 300 <= status < 400:
                # Following this would fail: .invalid never resolves.
                self.send_header("Location", "http://redirect-target.invalid/login")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [sys.executable, "-", f"http://127.0.0.1:{server.server_port}/"],
            input=_frontend_check_program(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    assert f"HTTP {status}" in result.stdout
    assert hits == ["/"]
