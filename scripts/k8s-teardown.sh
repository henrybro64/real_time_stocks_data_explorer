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
KUBECTL_CMD="${KUBECTL_CMD:-}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/k8s-teardown.sh k3d
  ./scripts/k8s-teardown.sh k3s

Removes everything from kubectl apply -k k8s (including the Postgres PVC and its data).

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

echo "Deleting resources from ${ROOT_DIR}/k8s ..."
${KUBECTL_CMD} delete -k "${ROOT_DIR}/k8s" --wait=true --timeout=120s

echo
echo "Teardown finished. The stock-ticker namespace and app resources (including Postgres PVC) are gone."
echo "To remove the cluster software too: k3d cluster delete <name>  OR  sudo /usr/local/bin/k3s-uninstall.sh"
