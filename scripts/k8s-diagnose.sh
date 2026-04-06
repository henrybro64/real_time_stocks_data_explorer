#!/usr/bin/env bash
# Print Kubernetes state useful for debugging deploy/redeploy issues (stock-ticker app on k3s/k3d).
#
# Usage:
#   ./scripts/k8s-diagnose.sh k3s
#   ./scripts/k8s-diagnose.sh k3d
#   KUBECTL_CMD=kubectl ./scripts/k8s-diagnose.sh k3s
# Do not use set -e — we want as much output as possible even if one kubectl fails.
set -u

MODE="${1:-}"
NS="${NAMESPACE:-stock-ticker}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL_CMD="${KUBECTL_CMD:-}"

if [[ "${MODE}" != "k3d" && "${MODE}" != "k3s" ]]; then
  echo "Usage: $0 k3d|k3s" >&2
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
  echo "Need kubectl or k3s" >&2
  exit 1
fi

echo "========== k8s-diagnose (${MODE}) =========="
echo "Repo k8s dir: ${ROOT_DIR}/k8s"
echo

echo "=== kubectl version / cluster ==="
${KUBECTL_CMD} version --client=true -o yaml 2>/dev/null | head -20 || ${KUBECTL_CMD} version --short 2>/dev/null || true
${KUBECTL_CMD} get nodes -o wide 2>&1 || true
echo

echo "=== storageclass ==="
${KUBECTL_CMD} get storageclass 2>&1 || true
echo

echo "=== namespace ${NS} ==="
${KUBECTL_CMD} get namespace "${NS}" -o wide 2>&1 || true
echo

echo "=== pods, pvc, svc (${NS}) ==="
${KUBECTL_CMD} get pods,svc,pvc -n "${NS}" -o wide 2>&1 || true
echo

echo "=== postgres pod describe (if any) ==="
${KUBECTL_CMD} describe pod -n "${NS}" -l app=stock-ticker-postgres 2>&1 | tail -80 || true
echo

echo "=== backend pod describe (if any) ==="
${KUBECTL_CMD} describe pod -n "${NS}" -l app=stock-ticker-backend 2>&1 | tail -40 || true
echo

echo "=== recent events (${NS}) ==="
${KUBECTL_CMD} get events -n "${NS}" --sort-by=.lastTimestamp 2>&1 | tail -35 || true
echo

echo "=== local-path-provisioner (kube-system) ==="
${KUBECTL_CMD} -n kube-system get pods -l app=local-path-provisioner -o wide 2>&1 || true
${KUBECTL_CMD} -n kube-system logs deployment/local-path-provisioner --tail=50 2>&1 || true
echo

echo "=== disk (node) ==="
df -h 2>/dev/null || true
echo
echo "=== end k8s-diagnose ==="
echo "Paste this output when asking for help. For stuck namespace: scripts/k8s-unstick-namespace.sh ${NS}"
