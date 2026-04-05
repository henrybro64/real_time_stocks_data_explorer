#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-stock-ticker}"
BACKEND_IMAGE="${BACKEND_IMAGE:-stock-ticker-backend:local}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-stock-ticker-frontend:local}"
K3D_CLUSTER_NAME="${K3D_CLUSTER_NAME:-stock-ticker}"
# Optional override, e.g. KUBECTL_CMD=kubectl ./scripts/deploy-k3s.sh k3s after fixing ~/.kube/config
KUBECTL_CMD="${KUBECTL_CMD:-}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy-k3s.sh k3d
  ./scripts/deploy-k3s.sh k3s

Modes:
  k3d  Build images locally and import them into an existing k3d cluster.
  k3s  Build images locally and import them into a native k3s containerd store.

k3d one-time setup (before first deploy):
  k3d cluster create stock-ticker
  kubectl config use-context k3d-stock-ticker

k3s: uses "sudo k3s kubectl" by default (kubeconfig under /etc/rancher is root-readable).
  Override: KUBECTL_CMD=kubectl ./scripts/deploy-k3s.sh k3s  (after copying k3s.yaml to ~/.kube/config)
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

ensure_k3d_cluster_exists() {
  require_cmd k3d
  local names
  names="$(k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' || true)"
  if ! echo "${names}" | grep -qx "${K3D_CLUSTER_NAME}"; then
    cat <<EOF >&2
Error: k3d cluster '${K3D_CLUSTER_NAME}' was not found.

Create it once, then point kubectl at it:
  k3d cluster create ${K3D_CLUSTER_NAME}
  kubectl config use-context k3d-${K3D_CLUSTER_NAME}

Re-run:
  ./scripts/deploy-k3s.sh k3d
EOF
    exit 1
  fi
}

require_kubectl_context_for_k3d() {
  local expected="k3d-${K3D_CLUSTER_NAME}"
  local cur
  cur="$(kubectl config current-context 2>/dev/null || echo "")"
  if [[ "${cur}" != "${expected}" ]]; then
    cat <<EOF >&2
Error: kubectl is not using the k3d context for this cluster.

  Expected context: ${expected}
  Current context:  ${cur:-<none>}

If you see connection refused to kubernetes.docker.internal, Docker Desktop Kubernetes
is selected instead of k3d. Fix:

  kubectl config use-context ${expected}

If that context is missing, create the cluster first:
  k3d cluster create ${K3D_CLUSTER_NAME}
EOF
    exit 1
  fi
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
  # EXIT must not reference `tmp_dir` after this function returns (local goes out of scope with set -u).
  trap 'rm -rf "${tmp_dir}"' EXIT

  echo "Saving local images as tar archives"
  docker save -o "${tmp_dir}/backend.tar" "${BACKEND_IMAGE}"
  docker save -o "${tmp_dir}/frontend.tar" "${FRONTEND_IMAGE}"

  echo "Importing images into native k3s containerd"
  sudo k3s ctr -n k8s.io images import "${tmp_dir}/backend.tar"
  sudo k3s ctr -n k8s.io images import "${tmp_dir}/frontend.tar"

  rm -rf "${tmp_dir}"
  trap - EXIT
}

print_postgres_debug_help() {
  cat <<'EOF' >&2

Postgres rollout failed. Common causes on k3s:
- PVC stuck Pending: no StorageClass or wrong name. Run: kubectl get storageclass && kubectl get pvc -n stock-ticker
  k3s usually expects storageClassName: local-path (see k8s/postgres.yaml).
- ext4 PVC root contains lost+found: Postgres must use PGDATA under a subdir (manifest sets /var/lib/postgresql/data/pgdata).
  If you deployed before that fix, delete the PVC and re-apply so the volume initializes cleanly.
- postgres:16-alpine still pulling (first boot needs network). Check: kubectl describe pod -n stock-ticker -l app=stock-ticker-postgres
- Volume permissions: see pod Events.

Debug:
  kubectl -n stock-ticker get pvc
  kubectl -n stock-ticker get pods -l app=stock-ticker-postgres
  kubectl -n stock-ticker describe pod -l app=stock-ticker-postgres
  kubectl -n stock-ticker logs -l app=stock-ticker-postgres --tail=80
EOF
}

print_backend_debug_help() {
  cat <<'EOF' >&2

Backend rollout failed. Common causes:
- Postgres pod not ready or DATABASE_URL wrong (default: in-cluster stock-ticker-postgres).
- If using host Postgres instead: wrong hostname / pg_hba — see README and scripts/k8s-test-host-postgres.sh

Debug (use the same kubectl command you use for apply, e.g. sudo k3s kubectl on Ubuntu):
  kubectl -n stock-ticker get pods -l app=stock-ticker-backend
  kubectl -n stock-ticker logs deployment/stock-ticker-backend -c migrate-db
  kubectl -n stock-ticker logs deployment/stock-ticker-backend -c backend
  kubectl -n stock-ticker describe pod -l app=stock-ticker-backend
EOF
}

deploy_manifests() {
  echo "Applying Kubernetes manifests"
  ${KUBECTL_CMD} apply -k "${ROOT_DIR}/k8s"

  echo "Waiting for Redis"
  ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-redis --timeout=180s

  echo "Waiting for Postgres"
  if ! ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-postgres --timeout=300s; then
    print_postgres_debug_help
    exit 1
  fi

  echo "Waiting for backend"
  if ! ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-backend --timeout=600s; then
    print_backend_debug_help
    exit 1
  fi

  echo "Waiting for frontend"
  ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-frontend --timeout=180s
}

if [[ -z "${MODE}" ]]; then
  usage
  exit 1
fi

require_cmd docker

if [[ -n "${KUBECTL_CMD}" ]]; then
  :
elif [[ "${MODE}" == "k3s" ]] && command -v k3s >/dev/null 2>&1; then
  # Plain kubectl often cannot read /etc/rancher/k3s/k3s.yaml (root-only). k3s embeds kubectl.
  KUBECTL_CMD="sudo k3s kubectl"
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL_CMD="kubectl"
elif command -v k3s >/dev/null 2>&1; then
  KUBECTL_CMD="sudo k3s kubectl"
else
  echo "Missing required command: kubectl (or k3s for k3s mode)" >&2
  exit 1
fi

case "${MODE}" in
  k3d)
    ensure_k3d_cluster_exists
    require_kubectl_context_for_k3d
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
  ${KUBECTL_CMD} -n ${NAMESPACE} port-forward svc/stock-ticker-frontend 3000:3000

Then browse:
  http://127.0.0.1:3000
EOF
