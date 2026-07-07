#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-stock-ticker}"
KUBECTL_CMD="${KUBECTL_CMD:-sudo k3s kubectl}"
ARTIFACT_DIR="${ARTIFACT_DIR:-${ROOT_DIR}/artifacts/k3s-smoke}"
LOG_FILE="${LOG_FILE:-${ARTIFACT_DIR}/ci-k3s-smoke.log}"
SUMMARY_FILE="${SUMMARY_FILE:-${ARTIFACT_DIR}/summary.txt}"

mkdir -p "${ARTIFACT_DIR}"
read -r -a KUBECTL_ARR <<< "${KUBECTL_CMD}"
exec > >(tee "${LOG_FILE}") 2>&1

cleanup() {
  if [[ -n "${BACKEND_PORT_FORWARD_PID:-}" ]]; then
    kill "${BACKEND_PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FRONTEND_PORT_FORWARD_PID:-}" ]]; then
    kill "${FRONTEND_PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

run_cmd() {
  printf '+ ' >&2
  printf '%q ' "$@" >&2
  printf '\n' >&2
  "$@"
}

run_kubectl() {
  printf '+ %s' "${KUBECTL_CMD}" >&2
  for arg in "$@"; do
    printf ' %q' "${arg}" >&2
  done
  printf '\n' >&2
  "${KUBECTL_ARR[@]}" "$@"
}

snapshot_kubectl() {
  local title="$1"
  shift

  {
    echo
    echo "## ${title}"
    printf '+ %s' "${KUBECTL_CMD}"
    for arg in "$@"; do
      printf ' %q' "${arg}"
    done
    printf '\n'
    "${KUBECTL_ARR[@]}" "$@"
  } | tee -a "${SUMMARY_FILE}"
}

append_summary_section() {
  local title="$1"
  local content="$2"

  {
    echo
    echo "## ${title}"
    printf '%s\n' "${content}"
  } | tee -a "${SUMMARY_FILE}"
}

export BACKEND_IMAGE="${BACKEND_IMAGE:-stock-ticker-backend:local}"
export FRONTEND_IMAGE="${FRONTEND_IMAGE:-stock-ticker-frontend:local}"
export KUBECTL_CMD

{
  echo "# k3s smoke evidence"
  echo "Timestamp (UTC): $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "Namespace: ${NAMESPACE}"
  echo "KUBECTL_CMD: ${KUBECTL_CMD}"
  echo "Deploy command: ${ROOT_DIR}/scripts/deploy-k3s.sh k3s"
  echo "Smoke command: ${ROOT_DIR}/scripts/ci-k3s-smoke.sh"
} | tee "${SUMMARY_FILE}"

run_cmd "${ROOT_DIR}/scripts/deploy-k3s.sh" k3s

echo "Waiting for all pods to report Ready"
run_kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod --all --timeout=600s

snapshot_kubectl "Deployments" -n "${NAMESPACE}" get deploy
snapshot_kubectl "Pods" -n "${NAMESPACE}" get pods -o wide
snapshot_kubectl "Services" -n "${NAMESPACE}" get svc
snapshot_kubectl "PersistentVolumeClaims" -n "${NAMESPACE}" get pvc

echo "Verifying backend migration init container completed"
init_reason="$(
  run_kubectl -n "${NAMESPACE}" get pod -l app=stock-ticker-backend \
    -o jsonpath='{.items[0].status.initContainerStatuses[0].state.terminated.reason}'
)"
if [[ "${init_reason}" != "Completed" ]]; then
  echo "Expected migrate-db init container to complete, got: ${init_reason:-<empty>}" >&2
  exit 1
fi
append_summary_section "Backend init container" "migrate-db terminated reason: ${init_reason}"

echo "Checking backend /health through service port-forward"
run_kubectl -n "${NAMESPACE}" port-forward svc/stock-ticker-backend 8000:8000 >"${ARTIFACT_DIR}/backend-port-forward.log" 2>&1 &
BACKEND_PORT_FORWARD_PID=$!
sleep 5
backend_health="$(run_cmd curl -fsS http://127.0.0.1:8000/health)"
printf '%s\n' "${backend_health}" > "${ARTIFACT_DIR}/backend-health.json"
grep -q '"status":"ok"' <<< "${backend_health}"
append_summary_section "Backend /health" "${backend_health}"

echo "Checking frontend -> backend service DNS path via Next.js rewrite"
run_kubectl -n "${NAMESPACE}" port-forward svc/stock-ticker-frontend 3000:3000 >"${ARTIFACT_DIR}/frontend-port-forward.log" 2>&1 &
FRONTEND_PORT_FORWARD_PID=$!
sleep 5
frontend_proxy="$(run_cmd curl -fsS http://127.0.0.1:3000/api/markets)"
printf '%s\n' "${frontend_proxy}" > "${ARTIFACT_DIR}/frontend-backend-proxy.json"
grep -q '"market"' <<< "${frontend_proxy}"
append_summary_section "Frontend /api/markets via frontend service" "${frontend_proxy}"

echo "Checking in-cluster backend service DNS from backend pod"
backend_dns_health="$(
  run_kubectl -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- python -c \
    "import urllib.request; print(urllib.request.urlopen('http://stock-ticker-backend:8000/health', timeout=10).read().decode())"
)"
grep -q '"status":"ok"' <<< "${backend_dns_health}"
append_summary_section "Backend pod -> backend service DNS /health" "${backend_dns_health}"

echo "Checking backend container can query Postgres"
postgres_probe="$(
  run_kubectl -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- python -c \
    "from sqlalchemy import text; from app.db.session import engine; conn = engine.connect(); print(conn.execute(text('select 1')).scalar_one()); conn.close()"
)"
grep -q '^1$' <<< "${postgres_probe}"
printf '%s\n' "${postgres_probe}" > "${ARTIFACT_DIR}/postgres-probe.txt"
append_summary_section "Backend pod -> Postgres probe" "${postgres_probe}"

echo "Checking backend container can reach Redis"
redis_probe="$(
  run_kubectl -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- python -c \
    "from app.services.quote_redis_cache import _redis; client = _redis(); assert client is not None, 'Redis client unavailable'; print(client.ping())"
)"
grep -q '^True$' <<< "${redis_probe}"
printf '%s\n' "${redis_probe}" > "${ARTIFACT_DIR}/redis-probe.txt"
append_summary_section "Backend pod -> Redis probe" "${redis_probe}"

echo "k3s smoke completed"
