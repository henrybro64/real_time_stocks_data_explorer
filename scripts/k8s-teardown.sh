#!/usr/bin/env bash
# Remove all resources defined in k8s/ (namespace stock-ticker, Postgres PVC, deployments, etc.).
# Does not uninstall k3d or k3s — only the app.
#
# Usage:
#   ./scripts/k8s-teardown.sh k3d
#   ./scripts/k8s-teardown.sh k3s
# Override kubectl: KUBECTL_CMD=kubectl ./scripts/k8s-teardown.sh k3s
set -euo pipefail

MODE="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-stock-ticker}"
KUBECTL_CMD="${KUBECTL_CMD:-}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/k8s-teardown.sh k3d
  ./scripts/k8s-teardown.sh k3s

Removes everything from kubectl apply -k k8s (including the Postgres PVC and its data).
Also removes k8s/observability when that overlay is present (Prometheus/Grafana RBAC).

Optional: KUBECTL_CMD=kubectl ./scripts/k8s-teardown.sh k3s
EOF
}

if [[ -z "${MODE}" ]] || [[ "${MODE}" != "k3d" && "${MODE}" != "k3s" ]]; then
  usage >&2
  exit 1
fi

if [[ -n "${KUBECTL_CMD}" ]]; then
  :
elif [[ "${MODE}" == "k3s" ]] && command -v k3s >/dev/null 2>&1; then
  KUBECTL_CMD="sudo k3s kubectl"
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL_CMD="kubectl"
elif command -v k3s >/dev/null 2>&1; then
  KUBECTL_CMD="sudo k3s kubectl"
else
  echo "Missing required command: kubectl (or k3s for k3s mode)" >&2
  exit 1
fi

if ! ${KUBECTL_CMD} get ns &>/dev/null; then
  echo "Error: cannot reach the Kubernetes cluster (check kubectl / k3s)." >&2
  exit 1
fi

if ! ${KUBECTL_CMD} get namespace "${NAMESPACE}" &>/dev/null; then
  echo "Namespace ${NAMESPACE} not found — nothing to delete (already clean)."
  exit 0
fi

echo "Deleting resources from ${ROOT_DIR}/k8s ..."
echo "(This waits until the namespace is fully removed; the shell may look idle for a bit.)"
if ${KUBECTL_CMD} get deploy -n "${NAMESPACE}" prometheus &>/dev/null \
  || ${KUBECTL_CMD} get deploy -n "${NAMESPACE}" grafana &>/dev/null \
  || ${KUBECTL_CMD} get clusterrole stock-ticker-prometheus &>/dev/null; then
  echo "Deleting observability overlay from ${ROOT_DIR}/k8s/observability ..."
  ${KUBECTL_CMD} delete -k "${ROOT_DIR}/k8s/observability" --ignore-not-found=true --wait=false || true
fi
${KUBECTL_CMD} delete -k "${ROOT_DIR}/k8s" --wait=true --timeout=300s

echo
echo "Teardown finished. The stock-ticker namespace and app resources (including Postgres PVC) are gone."
echo "To remove the cluster software too: k3d cluster delete <name>  OR  sudo /usr/local/bin/k3s-uninstall.sh"
