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

PLACEHOLDER_POD=""

cleanup() {
  if [[ -n "${BACKEND_PORT_FORWARD_PID:-}" ]]; then
    kill "${BACKEND_PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FRONTEND_PORT_FORWARD_PID:-}" ]]; then
    kill "${FRONTEND_PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${PLACEHOLDER_POD}" ]]; then
    "${KUBECTL_ARR[@]}" -n "${NAMESPACE}" delete pod "${PLACEHOLDER_POD}" \
      --ignore-not-found --wait=false >/dev/null 2>&1 || true
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

dump_proxy_failure_evidence() {
  {
    echo
    echo "## Frontend/backend logs after /api/markets failure"
  } | tee -a "${SUMMARY_FILE}"
  run_kubectl -n "${NAMESPACE}" logs deploy/stock-ticker-frontend --tail=200 \
    >"${ARTIFACT_DIR}/frontend-pod.log" 2>&1 || true
  run_kubectl -n "${NAMESPACE}" logs deploy/stock-ticker-backend -c backend --tail=200 \
    >"${ARTIFACT_DIR}/backend-pod.log" 2>&1 || true
  run_kubectl -n "${NAMESPACE}" exec deploy/stock-ticker-frontend -- \
    sh -c 'printf "BACKEND_PROXY_URL=%s\n" "${BACKEND_PROXY_URL:-}"' \
    >"${ARTIFACT_DIR}/frontend-backend-proxy-env.txt" 2>&1 || true
}

# k3s runs with AUTH_ENABLED=true, so market-data GETs need this session (#272).
SMOKE_USERNAME="k3s-smoke"
SMOKE_PASSWORD="k3s-smoke-pass"

# Retry HTTP GETs so brief Next.js rewrite / CoreDNS races do not fail the smoke.
curl_get_ok() {
  local url="$1"
  local out_file="$2"
  local attempts="${3:-10}"
  local delay_seconds="${4:-3}"
  local cookie_jar="${5:-}"
  local attempt http_code body curl_status
  local -a curl_args

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    printf '+ curl attempt %s/%s %s\n' "${attempt}" "${attempts}" "${url}" >&2
    curl_args=(-sS -w '\n%{http_code}' --max-time 20)
    if [[ -n "${cookie_jar}" ]]; then
      curl_args+=(-b "${cookie_jar}")
    fi
    set +e
    body="$(curl "${curl_args[@]}" "${url}")"
    curl_status=$?
    set -e
    if [[ "${curl_status}" -eq 0 ]]; then
      http_code="$(printf '%s\n' "${body}" | tail -n 1)"
      body="$(printf '%s\n' "${body}" | sed '$d')"
      if [[ "${http_code}" == "200" ]]; then
        printf '%s\n' "${body}" >"${out_file}"
        printf '%s\n' "${body}"
        return 0
      fi
      printf 'HTTP %s for %s\n' "${http_code}" "${url}" >&2
      printf '%s\n' "${body}" >"${out_file}.attempt-${attempt}.http-${http_code}.txt"
    else
      printf 'curl exit %s for %s\n' "${curl_status}" "${url}" >&2
    fi
    if [[ "${attempt}" -lt "${attempts}" ]]; then
      sleep "${delay_seconds}"
    fi
  done

  dump_proxy_failure_evidence
  echo "Failed to GET ${url} after ${attempts} attempts" >&2
  return 1
}

# Register a disposable user, or sign in when that username already exists.
ensure_market_data_session() {
  local base_url="$1"
  local cookie_jar="$2"
  local payload http_code attempt

  payload="$(printf '{"username":"%s","password":"%s"}' "${SMOKE_USERNAME}" "${SMOKE_PASSWORD}")"
  rm -f "${cookie_jar}"
  for ((attempt = 1; attempt <= 5; attempt++)); do
    http_code="$(
      curl -sS -o "${ARTIFACT_DIR}/auth-register.body" -w '%{http_code}' \
        -c "${cookie_jar}" -b "${cookie_jar}" \
        -H 'Content-Type: application/json' \
        --data "${payload}" \
        --max-time 20 \
        "${base_url}/api/auth/register/local" || true
    )"
    if [[ "${http_code}" == "200" ]]; then
      echo "Registered smoke session via ${base_url}"
      return 0
    fi
    http_code="$(
      curl -sS -o "${ARTIFACT_DIR}/auth-login.body" -w '%{http_code}' \
        -c "${cookie_jar}" -b "${cookie_jar}" \
        -H 'Content-Type: application/json' \
        --data "${payload}" \
        --max-time 20 \
        "${base_url}/api/auth/login/local" || true
    )"
    if [[ "${http_code}" == "200" ]]; then
      echo "Signed in smoke session via ${base_url}"
      return 0
    fi
    printf 'Smoke sign-in HTTP %s via %s (attempt %s/5)\n' \
      "${http_code}" "${base_url}" "${attempt}" >&2
    sleep 2
  done
  echo "Failed to establish a smoke session via ${base_url}" >&2
  return 1
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

# deploy-k3s.sh already waits for redis/postgres/backend/frontend rollouts.
# Avoid a second 600s wait on every pod, which flakes when infra pods are slow.
echo "Confirming core workload pods are Ready"
if ! run_kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod \
  -l 'app in (stock-ticker-postgres, stock-ticker-redis, stock-ticker-backend, stock-ticker-backend-worker, stock-ticker-frontend)' \
  --timeout=120s; then
  snapshot_kubectl "Pods (not Ready)" -n "${NAMESPACE}" get pods -o wide
  run_kubectl -n "${NAMESPACE}" describe pod -l 'app in (stock-ticker-postgres, stock-ticker-redis)' \
    >>"${SUMMARY_FILE}" 2>&1 || true
  exit 1
fi

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

echo "Verifying migrate-db waited for Postgres before Alembic (#308)"
migrate_log="$(run_kubectl -n "${NAMESPACE}" logs deploy/stock-ticker-backend -c migrate-db)"
printf '%s\n' "${migrate_log}" > "${ARTIFACT_DIR}/migrate-db.log"
if ! grep -q 'db.wait.ready' <<< "${migrate_log}"; then
  echo "migrate-db log has no db.wait.ready line; see ${ARTIFACT_DIR}/migrate-db.log" >&2
  exit 1
fi
append_summary_section "migrate-db Postgres wait" "$(grep 'db.wait' <<< "${migrate_log}")"

echo "Verifying the Postgres AppArmor setting reached the cluster (#308)"
# The API server drops fields it does not know; this proves the field was kept.
pg_apparmor="$(
  run_kubectl -n "${NAMESPACE}" get deploy stock-ticker-postgres \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="postgres")].securityContext.appArmorProfile.type}'
)"
if [[ "${pg_apparmor}" != "Unconfined" ]]; then
  echo "Expected Postgres appArmorProfile Unconfined, got: ${pg_apparmor:-<empty>}" >&2
  exit 1
fi
append_summary_section "Postgres AppArmor profile (#308)" "${pg_apparmor}"

echo "Checking backend /health through service port-forward"
run_kubectl -n "${NAMESPACE}" port-forward svc/stock-ticker-backend 8000:8000 >"${ARTIFACT_DIR}/backend-port-forward.log" 2>&1 &
BACKEND_PORT_FORWARD_PID=$!
sleep 5
backend_health="$(curl_get_ok http://127.0.0.1:8000/health "${ARTIFACT_DIR}/backend-health.json" 8 2)"
grep -q '"status":"ok"' <<< "${backend_health}"
append_summary_section "Backend /health" "${backend_health}"

echo "Signing in before market-data reads"
backend_cookie_jar="${ARTIFACT_DIR}/backend-session.cookies"
ensure_market_data_session "http://127.0.0.1:8000" "${backend_cookie_jar}"

echo "Checking backend /api/markets through service port-forward"
backend_markets="$(curl_get_ok http://127.0.0.1:8000/api/markets "${ARTIFACT_DIR}/backend-markets.json" 8 2 "${backend_cookie_jar}")"
grep -q '"market"' <<< "${backend_markets}"
append_summary_section "Backend /api/markets" "${backend_markets}"

echo "Checking frontend -> backend service DNS path via Next.js rewrite"
run_kubectl -n "${NAMESPACE}" port-forward svc/stock-ticker-frontend 3000:3000 >"${ARTIFACT_DIR}/frontend-port-forward.log" 2>&1 &
FRONTEND_PORT_FORWARD_PID=$!
sleep 5
frontend_cookie_jar="${ARTIFACT_DIR}/frontend-session.cookies"
ensure_market_data_session "http://127.0.0.1:3000" "${frontend_cookie_jar}"
frontend_proxy="$(curl_get_ok http://127.0.0.1:3000/api/markets "${ARTIFACT_DIR}/frontend-backend-proxy.json" 12 3 "${frontend_cookie_jar}")"
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

echo "Checking a pod without an allowed label cannot reach Postgres (#308 NetworkPolicy)"
if [[ "${BACKEND_IMAGE}" == */* ]]; then np_pull_policy="IfNotPresent"; else np_pull_policy="Never"; fi
np_check="$(
  run_kubectl -n "${NAMESPACE}" run smoke-postgres-denied --rm -i --restart=Never \
    --image="${BACKEND_IMAGE}" --image-pull-policy="${np_pull_policy}" --labels=app=smoke-postgres-denied \
    --command -- python -c \
    "import socket
# Resolve outside the try: a DNS failure must not pass as BLOCKED.
address = socket.gethostbyname('stock-ticker-postgres')
try:
    socket.create_connection((address, 5432), timeout=5).close()
    print('REACHED')
except OSError as exc:
    print('BLOCKED', type(exc).__name__)" 2>&1 || true
)"
printf '%s\n' "${np_check}" > "${ARTIFACT_DIR}/postgres-networkpolicy.txt"
if ! grep -q '^BLOCKED' <<< "${np_check}"; then
  echo "Expected the NetworkPolicy to block an unlabeled pod from Postgres, got: ${np_check}" >&2
  exit 1
fi
append_summary_section "Unlabeled pod -> Postgres (expect BLOCKED)" "$(grep '^BLOCKED' <<< "${np_check}")"

echo "Checking backend container can reach Redis"
redis_probe="$(
  run_kubectl -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- python -c \
    "from app.services.quote_redis_cache import _redis; client = _redis(); assert client is not None, 'Redis client unavailable'; print(client.ping())"
)"
grep -q '^True$' <<< "${redis_probe}"
printf '%s\n' "${redis_probe}" > "${ARTIFACT_DIR}/redis-probe.txt"
append_summary_section "Backend pod -> Redis probe" "${redis_probe}"

smoke_placeholder_capture_token() {
  # A placeholder archive token must boot and return 503, not CrashLoop (#284).
  local pod_name="smoke-placeholder-capture"
  local session_secret pull_policy
  session_secret="$(openssl rand -hex 32)"
  if [[ "${BACKEND_IMAGE}" == */* ]]; then
    pull_policy="IfNotPresent"
  else
    pull_policy="Never"
  fi
  PLACEHOLDER_POD="${pod_name}"
  run_kubectl -n "${NAMESPACE}" delete pod "${pod_name}" --ignore-not-found --wait=true >/dev/null 2>&1 || true
  cat <<EOF | run_kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  containers:
    - name: backend
      image: ${BACKEND_IMAGE}
      imagePullPolicy: ${pull_policy}
      workingDir: /app/backend
      command: ["python", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
      env:
        - name: AUTH_ENABLED
          value: "true"
        - name: LOCAL_AUTH_ENABLED
          value: "true"
        - name: RUN_BACKGROUND_JOBS
          value: "false"
        - name: DATABASE_URL
          value: ""
        - name: REDIS_URL
          value: ""
        - name: SESSION_SECRET
          value: "${session_secret}"
        - name: LOCAL_AUTH_USERNAME
          value: ""
        - name: LOCAL_AUTH_PASSWORD
          value: ""
        - name: RANKINGS_ARCHIVE_CAPTURE_TOKEN
          value: "replace-with-long-random-token"
EOF
  if ! run_kubectl -n "${NAMESPACE}" wait --for=condition=Ready "pod/${pod_name}" --timeout=180s; then
    run_kubectl -n "${NAMESPACE}" logs "${pod_name}" >"${ARTIFACT_DIR}/placeholder-capture.log" 2>&1 || true
    echo "Placeholder-capture pod did not become Ready" >&2
    exit 1
  fi
  local attempt probe_ok=0
  for ((attempt = 1; attempt <= 15; attempt++)); do
    if run_kubectl -n "${NAMESPACE}" exec -i "${pod_name}" -- python - <<'PY'
import urllib.error
import urllib.request

health = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10)
if health.status != 200:
    raise SystemExit(f"health {health.status}")
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/rankings/archive/capture",
    data=b"{}",
    headers={
        "Content-Type": "application/json",
        "X-Rankings-Archive-Capture-Token": "replace-with-long-random-token",
    },
    method="POST",
)
try:
    urllib.request.urlopen(request, timeout=10)
except urllib.error.HTTPError as exc:
    if exc.code != 503:
        raise SystemExit(f"expected 503, got {exc.code}")
else:
    raise SystemExit("placeholder token was accepted")
print("ok")
PY
    then
      probe_ok=1
      break
    fi
    sleep 2
  done
  run_kubectl -n "${NAMESPACE}" logs "${pod_name}" >"${ARTIFACT_DIR}/placeholder-capture.log" 2>&1 || true
  if [[ "${probe_ok}" -ne 1 ]]; then
    echo "Placeholder capture token did not boot with HTTP 503" >&2
    exit 1
  fi
  if ! grep -q "auth.capture_token.placeholder_disabled" "${ARTIFACT_DIR}/placeholder-capture.log" \
    && ! grep -q "Placeholder capture token disabled" "${ARTIFACT_DIR}/placeholder-capture.log"; then
    echo "Placeholder capture token did not log that it was disabled" >&2
    exit 1
  fi
  append_summary_section "Placeholder capture token" "API stayed up; archive capture returned 503"
  run_kubectl -n "${NAMESPACE}" delete pod "${pod_name}" --wait=false >/dev/null 2>&1 || true
  PLACEHOLDER_POD=""
}

echo "Checking a placeholder capture token does not crash the API"
smoke_placeholder_capture_token

echo "k3s smoke completed"
