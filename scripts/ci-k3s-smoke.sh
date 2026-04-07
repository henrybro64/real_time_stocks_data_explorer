#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-stock-ticker}"
KUBECTL_CMD="${KUBECTL_CMD:-sudo k3s kubectl}"

cleanup() {
  if [[ -n "${BACKEND_PORT_FORWARD_PID:-}" ]]; then
    kill "${BACKEND_PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FRONTEND_PORT_FORWARD_PID:-}" ]]; then
    kill "${FRONTEND_PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

export BACKEND_IMAGE="${BACKEND_IMAGE:-stock-ticker-backend:local}"
export FRONTEND_IMAGE="${FRONTEND_IMAGE:-stock-ticker-frontend:local}"
export KUBECTL_CMD

"${ROOT_DIR}/scripts/deploy-k3s.sh" k3s

echo "Waiting for all pods to report Ready"
${KUBECTL_CMD} -n "${NAMESPACE}" wait --for=condition=Ready pod --all --timeout=600s

echo "Verifying backend migration init container completed"
init_reason="$(
  ${KUBECTL_CMD} -n "${NAMESPACE}" get pod -l app=stock-ticker-backend \
    -o jsonpath='{.items[0].status.initContainerStatuses[0].state.terminated.reason}'
)"
if [[ "${init_reason}" != "Completed" ]]; then
  echo "Expected migrate-db init container to complete, got: ${init_reason:-<empty>}" >&2
  exit 1
fi

echo "Checking backend /health through service port-forward"
${KUBECTL_CMD} -n "${NAMESPACE}" port-forward svc/stock-ticker-backend 8000:8000 >/tmp/backend-port-forward.log 2>&1 &
BACKEND_PORT_FORWARD_PID=$!
sleep 5
curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"'

echo "Checking frontend -> backend service DNS path via Next.js rewrite"
${KUBECTL_CMD} -n "${NAMESPACE}" port-forward svc/stock-ticker-frontend 3000:3000 >/tmp/frontend-port-forward.log 2>&1 &
FRONTEND_PORT_FORWARD_PID=$!
sleep 5
curl -fsS http://127.0.0.1:3000/api/markets >/tmp/frontend-backend-proxy.json
grep -q '"market"' /tmp/frontend-backend-proxy.json

echo "Checking in-cluster backend service DNS from backend pod"
${KUBECTL_CMD} -n "${NAMESPACE}" exec deploy/stock-ticker-backend -c backend -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://stock-ticker-backend:8000/health', timeout=10).read().decode())" \
  | grep -q '"status":"ok"'

echo "k3s smoke completed"
