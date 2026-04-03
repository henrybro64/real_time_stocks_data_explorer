#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-stock-ticker}"
BACKEND_IMAGE="${BACKEND_IMAGE:-stock-ticker-backend:local}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-stock-ticker-frontend:local}"
K3D_CLUSTER_NAME="${K3D_CLUSTER_NAME:-stock-ticker}"
KUBECTL_CMD=""

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy-k3s.sh k3d
  ./scripts/deploy-k3s.sh k3s

Modes:
  k3d  Build images locally and import them into an existing k3d cluster.
  k3s  Build images locally and import them into a native k3s containerd store.
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

build_images() {
  echo "Building backend image: ${BACKEND_IMAGE}"
  docker build -t "${BACKEND_IMAGE}" -f "${ROOT_DIR}/backend/Dockerfile" "${ROOT_DIR}"

  echo "Building frontend image: ${FRONTEND_IMAGE}"
  docker build -t "${FRONTEND_IMAGE}" -f "${ROOT_DIR}/frontend/Dockerfile" "${ROOT_DIR}"
}

import_into_k3d() {
  require_cmd k3d
  echo "Importing local images into k3d cluster: ${K3D_CLUSTER_NAME}"
  k3d image import "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}" -c "${K3D_CLUSTER_NAME}"
}

import_into_k3s() {
  require_cmd k3s

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "${tmp_dir}"' EXIT

  echo "Saving local images as tar archives"
  docker save -o "${tmp_dir}/backend.tar" "${BACKEND_IMAGE}"
  docker save -o "${tmp_dir}/frontend.tar" "${FRONTEND_IMAGE}"

  echo "Importing images into native k3s containerd"
  sudo k3s ctr -n k8s.io images import "${tmp_dir}/backend.tar"
  sudo k3s ctr -n k8s.io images import "${tmp_dir}/frontend.tar"
}

deploy_manifests() {
  echo "Applying Kubernetes manifests"
  ${KUBECTL_CMD} apply -k "${ROOT_DIR}/k8s"

  echo "Waiting for Postgres"
  ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-postgres --timeout=180s

  echo "Waiting for Redis"
  ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-redis --timeout=180s

  echo "Waiting for backend"
  ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-backend --timeout=180s

  echo "Waiting for frontend"
  ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-frontend --timeout=180s
}

if [[ -z "${MODE}" ]]; then
  usage
  exit 1
fi

require_cmd docker

if command -v kubectl >/dev/null 2>&1; then
  KUBECTL_CMD="kubectl"
elif [[ "${MODE}" == "k3s" ]] && command -v k3s >/dev/null 2>&1; then
  KUBECTL_CMD="sudo k3s kubectl"
else
  echo "Missing required command: kubectl" >&2
  exit 1
fi

case "${MODE}" in
  k3d)
    build_images
    import_into_k3d
    deploy_manifests
    ;;
  k3s)
    build_images
    import_into_k3s
    deploy_manifests
    ;;
  *)
    usage
    exit 1
    ;;
esac

cat <<EOF

Deployment complete.

Open the app with:
  kubectl -n ${NAMESPACE} port-forward svc/stock-ticker-frontend 3000:3000

Then browse:
  http://127.0.0.1:3000
EOF
