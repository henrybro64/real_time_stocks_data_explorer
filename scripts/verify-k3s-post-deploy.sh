#!/usr/bin/env bash
# Read-only cluster report for #287. Does not mutate Secrets, Deployments, or rows.
# Account sign-in and feature checks stay in docs/post-deploy-verification.md.
# Does not prove the running :local image is a given git SHA. deploy-k3s.sh
# restarts app Deployments after a local import (#303).
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
echo "== Running image IDs (informational; :local tags do not carry a git SHA) =="
note "deploy-k3s.sh restarts backend/worker/frontend after a local import (#303)."
for deploy in stock-ticker-backend stock-ticker-backend-worker stock-ticker-frontend; do
  image_id="$(
    ${KUBECTL_CMD} -n "${NAMESPACE}" get pods -l "app=${deploy}" \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}' \
      2>/dev/null || true
  )"
  if [[ -n "${image_id}" ]]; then
    note "${image_id}"
  else
    fail "could not read imageID for ${deploy}"
  fi
done

echo
echo "== Heartbeat job =="
if ${KUBECTL_CMD} -n "${NAMESPACE}" get cronjob stock-ticker-aws-health-heartbeat >/dev/null 2>&1; then
  ${KUBECTL_CMD} -n "${NAMESPACE}" get jobs --sort-by=.metadata.creationTimestamp 2>/dev/null \
    | awk 'NR==1 || /aws-health-heartbeat/' || true
  # Pipe-separated fields keep a missing .status.succeeded from sliding into
  # the failed count. --sort-by is ascending, so the last heartbeat row is newest.
  # A failed Job often omits succeeded; that must count as 0, not as success.
  last_job="$(
    ${KUBECTL_CMD} -n "${NAMESPACE}" get jobs --sort-by=.metadata.creationTimestamp \
      -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.metadata.creationTimestamp}{"|"}{.status.succeeded}{"|"}{.status.failed}{"\n"}{end}' \
      2>/dev/null | awk -F'|' '
        /aws-health-heartbeat/ { name=$1; ok=$3; bad=$4 }
        END {
          if (name == "") exit
          if (ok == "") ok = 0
          if (bad == "") bad = 0
          print name, ok, bad
        }
      '
  )"
  note "latest heartbeat job fields: ${last_job:-none}"
  last_ok="$(printf '%s\n' "${last_job}" | awk '{print $2}')"
  last_bad="$(printf '%s\n' "${last_job}" | awk '{print $3}')"
  if [[ -z "${last_job}" || "${last_job}" == "none" ]]; then
    note "No heartbeat Job found yet; confirm the CronJob has run."
  elif [[ "${last_ok}" == "1" ]]; then
    pass "latest aws-health-heartbeat Job succeeded"
  else
    fail "latest aws-health-heartbeat Job did not succeed (succeeded=${last_ok:-?} failed=${last_bad:-?}); see docs/aws-health-monitor.md"
  fi
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
    "oauth_identities",
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
echo "== Redis PVC (#293) =="
pvc_phase="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" get pvc stock-ticker-redis-data \
    -o jsonpath='{.status.phase}' 2>/dev/null || true
)"
if [[ "${pvc_phase}" == "Bound" ]]; then
  pass "PVC stock-ticker-redis-data is Bound"
else
  fail "PVC stock-ticker-redis-data phase '${pvc_phase:-missing}' (want Bound)"
fi

echo
echo "== Redis memory, policy, and evictions (#293) =="
# noeviction should keep this at zero. A non-zero count means keys, including locks, were deleted.
redis_info="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-redis -c redis -- redis-cli INFO 2>&1 || true
)"
redis_line() {
  printf '%s\n' "${redis_info}" | tr -d '\r' | awk -F: -v key="$1" '$1 == key {print $2}'
}
used_memory="$(redis_line used_memory_human)"
max_memory="$(redis_line maxmemory_human)"
evicted_keys="$(redis_line evicted_keys)"
note "used_memory=${used_memory:-unknown} maxmemory=${max_memory:-unknown} evicted_keys=${evicted_keys:-unknown}"
if [[ -z "${evicted_keys}" ]]; then
  fail "could not read Redis INFO"
elif [[ "${evicted_keys}" == "0" ]]; then
  pass "Redis has evicted no keys"
else
  fail "Redis evicted ${evicted_keys} keys; raise maxmemory or find what is filling Redis"
fi
redis_policy="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-redis -c redis -- \
    redis-cli CONFIG GET maxmemory-policy 2>/dev/null | tr -d '\r' | awk 'NR==2 {print $1}'
)"
redis_maxbytes="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-redis -c redis -- \
    redis-cli CONFIG GET maxmemory 2>/dev/null | tr -d '\r' | awk 'NR==2 {print $1}'
)"
if [[ "${redis_policy}" == "noeviction" ]]; then
  pass "Redis maxmemory-policy is noeviction"
else
  fail "Redis maxmemory-policy is '${redis_policy:-missing}' (want noeviction)"
fi
# 128mb from redis.conf is 134217728 bytes.
if [[ "${redis_maxbytes}" == "134217728" ]]; then
  pass "Redis maxmemory is 128mb"
else
  fail "Redis maxmemory is '${redis_maxbytes:-missing}' bytes (want 134217728)"
fi

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
