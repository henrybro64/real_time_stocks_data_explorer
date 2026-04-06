#!/usr/bin/env bash
# When a namespace stays "Terminating" forever (often after PVC or volume quirks), find
# leftover objects and optionally clear the namespace finalizer (last resort).
#
# Usage:
#   ./scripts/k8s-unstick-namespace.sh [namespace]           # list what is left
#   ./scripts/k8s-unstick-namespace.sh [namespace] --force   # finalize namespace (destructive cleanup)
#
# k3s:
#   ./scripts/k8s-unstick-namespace.sh stock-ticker
# Override kubectl:
#   KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-unstick-namespace.sh stock-ticker --force
set -euo pipefail

NS="${1:-stock-ticker}"
FORCE="${2:-}"

if [[ "${NS}" == "--force" ]]; then
  NS="stock-ticker"
  FORCE="--force"
fi

KUBECTL_CMD="${KUBECTL_CMD:-}"
if [[ -n "${KUBECTL_CMD}" ]]; then
  :
elif command -v k3s >/dev/null 2>&1; then
  KUBECTL_CMD="sudo k3s kubectl"
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL_CMD="kubectl"
else
  echo "Need kubectl or k3s" >&2
  exit 1
fi

list_leftovers() {
  echo "=== Namespace status ==="
  ${KUBECTL_CMD} get namespace "${NS}" -o wide 2>/dev/null || true
  echo
  echo "=== Namespaced resources (non-empty) ==="
  local r
  while read -r r; do
    [[ -z "${r}" ]] && continue
    local out
    out="$(${KUBECTL_CMD} get "${r}" -n "${NS}" -o name 2>/dev/null || true)"
    if [[ -n "${out}" ]]; then
      echo "--- ${r} ---"
      echo "${out}"
      ${KUBECTL_CMD} get "${r}" -n "${NS}" 2>/dev/null || true
      echo
    fi
  done < <(${KUBECTL_CMD} api-resources --verbs=list --namespaced -o name 2>/dev/null | sort -u)
}

force_finalize() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "This step needs \`jq\`. Install: sudo apt install jq" >&2
    exit 1
  fi
  echo "Clearing spec.finalizers on namespace ${NS} via /finalize (last resort) ..."
  ${KUBECTL_CMD} get namespace "${NS}" -o json \
    | jq '.spec.finalizers = []' \
    | ${KUBECTL_CMD} replace --raw "/api/v1/namespaces/${NS}/finalize" -f -
  echo "Done. Check: ${KUBECTL_CMD} get ns | grep ${NS} || echo gone"
}

list_leftovers

if [[ "${FORCE}" == "--force" ]]; then
  echo
  force_finalize
else
  echo
  echo "If nothing important is listed above but STATUS is still Terminating for hours,"
  echo "run with --force (requires jq) to finalize the namespace:"
  echo "  KUBECTL_CMD='${KUBECTL_CMD}' ./scripts/k8s-unstick-namespace.sh ${NS} --force"
fi
