#!/usr/bin/env bash
# Read-only cluster report for #287. Does not mutate Secrets, Deployments, or rows.
# Account sign-in and feature checks stay in docs/post-deploy-verification.md.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-stock-ticker}"
KUBECTL_CMD="${KUBECTL_CMD:-kubectl}"
FAILURES=0

if [[ -z "${KUBECTL_CMD}" || "${KUBECTL_CMD}" == "kubectl" ]]; then
  if command -v kubectl >/dev/null 2>&1; then
    KUBECTL_CMD="kubectl"
  elif command -v k3s >/dev/null 2>&1; then
    KUBECTL_CMD="sudo k3s kubectl"
  fi
fi

note() {
  printf '%s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}

pass() {
  printf 'PASS %s\n' "$1"
}

echo "== Pods =="
if ! ${KUBECTL_CMD} -n "${NAMESPACE}" get pods -o wide; then
  fail "cannot list pods in ${NAMESPACE}"
  echo
  echo "Manual checklist: ${ROOT_DIR}/docs/post-deploy-verification.md"
  exit 1
fi

echo
echo "== Workload readiness =="
for deploy in \
  stock-ticker-backend \
  stock-ticker-backend-worker \
  stock-ticker-frontend \
  stock-ticker-postgres \
  stock-ticker-redis
do
  ready="$(${KUBECTL_CMD} -n "${NAMESPACE}" get deploy "${deploy}" -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null || true)"
  restarts="$(
    ${KUBECTL_CMD} -n "${NAMESPACE}" get pods -l "app=${deploy}" \
      -o jsonpath='{range .items[*]}{.status.containerStatuses[*].restartCount}{" "}{end}' 2>/dev/null || true
  )"
  if [[ "${ready}" == "1/1" ]]; then
    pass "${deploy} ready ${ready} restarts [${restarts:-0}]"
  else
    fail "${deploy} ready '${ready:-missing}' restarts [${restarts:-unknown}]"
  fi
done

echo
echo "== Heartbeat job =="
if ${KUBECTL_CMD} -n "${NAMESPACE}" get cronjob stock-ticker-aws-health-heartbeat >/dev/null 2>&1; then
  ${KUBECTL_CMD} -n "${NAMESPACE}" get jobs -l job-name --sort-by=.metadata.creationTimestamp 2>/dev/null \
    | awk 'NR==1 || /aws-health-heartbeat/' || true
  last_job="$(
    ${KUBECTL_CMD} -n "${NAMESPACE}" get jobs \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.succeeded}{" "}{.status.failed}{"\n"}{end}' \
      2>/dev/null | awk '/aws-health-heartbeat/ {name=$1; ok=$2; bad=$3} END {print name, ok, bad}'
  )"
  note "latest heartbeat job fields: ${last_job:-none}"
  note "Confirm an Error job is from the outage window or a separate AWS credential issue."
else
  note "CronJob stock-ticker-aws-health-heartbeat is not installed"
fi

echo
echo "== Backend /health =="
if ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read().decode())"
then
  pass "backend /health"
else
  fail "backend /health"
fi

echo
echo "== Auth status =="
if ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/auth/status', timeout=10).read().decode())"
then
  pass "backend /api/auth/status responded"
else
  fail "backend /api/auth/status"
fi

echo
echo "== Worker /health =="
if ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend-worker -c worker -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=10).read().decode())"
then
  pass "worker /health"
else
  fail "worker /health"
fi

echo
echo "== Worker /livez (liveness probe, #289) =="
if ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend-worker -c worker -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/livez', timeout=10).read().decode())"
then
  pass "worker /livez"
else
  fail "worker /livez"
fi

echo
echo "== Alembic current =="
# Compare against the image's own head so this check does not rot with each migration.
alembic_out="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- alembic current 2>&1 || true
)"
alembic_heads="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- alembic heads 2>&1 || true
)"
printf 'current: %s\nheads:   %s\n' "${alembic_out}" "${alembic_heads}"
head_revs="$(printf '%s\n' "${alembic_heads}" | awk '/\(head\)/ {print $1}')"
current_revs="$(printf '%s\n' "${alembic_out}" | awk '/\(head\)/ {print $1}')"
if [[ -z "${head_revs}" ]]; then
  fail "could not read alembic heads"
elif [[ "$(printf '%s\n' "${head_revs}" | sort)" == "$(printf '%s\n' "${current_revs}" | sort)" ]]; then
  pass "alembic current is at head (${head_revs//$'\n'/ })"
else
  fail "alembic current is not at head (${head_revs//$'\n'/ })"
fi

echo
echo "== Row counts (compare with the pre-upgrade backup) =="
${KUBECTL_CMD} -n "${NAMESPACE}" exec -i deploy/stock-ticker-backend -c backend -- python - <<'PY' || fail "row counts"
from sqlalchemy import text

from app.db.session import engine

tables = (
    "users",
    "watchlist_items",
    "portfolio_positions",
    "portfolio_assets",
    "realized_trades",
    "alert_rules",
    "alert_events",
    "portfolio_history_snapshots",
)
if engine is None:
    raise SystemExit("database is not configured")
with engine.connect() as conn:
    for table in tables:
        count = conn.execute(text(f"select count(*) from {table}")).scalar_one()
        print(f"{table} {count}")
PY

echo
echo "== Quote Redis cache failures in the last 200 worker lines =="
worker_logs="$(${KUBECTL_CMD} -n "${NAMESPACE}" logs deploy/stock-ticker-backend-worker -c worker --tail=200 2>&1 || true)"
if printf '%s\n' "${worker_logs}" | grep -q "Quote Redis cache failure"; then
  note "Quote Redis cache failure is still in the last 200 worker lines:"
  printf '%s\n' "${worker_logs}" | grep "Quote Redis cache failure" || true
  fail "Quote Redis cache failure still present in recent worker logs"
else
  pass "no Quote Redis cache failure in the last 200 worker lines"
fi

echo
echo "Sign-in, watchlist contents, portfolio, rankings, and alerts are still a manual pass."
echo "Checklist: ${ROOT_DIR}/docs/post-deploy-verification.md"
if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} cluster check(s) failed"
  exit 1
fi
echo "Cluster checks passed"
