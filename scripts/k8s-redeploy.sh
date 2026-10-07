#!/usr/bin/env bash
# Tear down the app in Kubernetes (namespace + PVC + all k8s/ resources), then build, import, and deploy again.
# Does not uninstall k3d/k3s or remove Docker images.
#
# Usage:
#   ./scripts/k8s-redeploy.sh k3d
#   ./scripts/k8s-redeploy.sh k3s
set -euo pipefail

MODE="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/k8s-redeploy.sh k3d
  ./scripts/k8s-redeploy.sh k3s

Runs k8s-teardown.sh then deploy-k3s.sh (full reset of the stock-ticker app and Postgres data).

If teardown fails because the namespace is stuck Terminating, see docs/kubernetes.md (k8s-unstick-namespace.sh).
EOF
}

if [[ "${MODE}" != "k3d" && "${MODE}" != "k3s" ]]; then
  usage >&2
  exit 1
fi

echo "=== Step 1: teardown (${MODE}) ==="
"${ROOT_DIR}/scripts/k8s-teardown.sh" "${MODE}"

echo
echo "=== Step 2: deploy (${MODE}) ==="
if ! "${ROOT_DIR}/scripts/deploy-k3s.sh" "${MODE}"; then
  echo >&2
  echo "Deploy failed. Collect logs with: ${ROOT_DIR}/scripts/k8s-diagnose.sh ${MODE}" >&2
  exit 1
fi
