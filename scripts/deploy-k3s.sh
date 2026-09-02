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

Optional env:
  OBSERVABILITY=1  Also apply k8s/observability (Prometheus, Grafana, kube-state-metrics).
                   See docs/observability.md. Default smoke path leaves this off.
  BACKEND_BASE_IMAGE   Docker base for backend (default: python:3.11-slim-bookworm)
  FRONTEND_BASE_IMAGE  Docker base for frontend (default: node:20-bookworm-slim)
  DOCKER_PULL_RETRIES / DOCKER_PULL_RETRY_DELAY_SEC  Pre-pull retry tuning (default: 5 / 15)

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
  local backend_base="${BACKEND_BASE_IMAGE:-python:3.11-slim-bookworm}"
  local frontend_base="${FRONTEND_BASE_IMAGE:-node:20-bookworm-slim}"

  pull_docker_image_with_retry "${backend_base}"
  pull_docker_image_with_retry "${frontend_base}"

  echo "Building backend image: ${BACKEND_IMAGE}"
  docker build \
    --build-arg "PYTHON_BASE_IMAGE=${backend_base}" \
    -t "${BACKEND_IMAGE}" \
    -f "${ROOT_DIR}/backend/Dockerfile" \
    "${ROOT_DIR}"

  echo "Building frontend image: ${FRONTEND_IMAGE}"
  docker build \
    --build-arg "NODE_BASE_IMAGE=${frontend_base}" \
    -t "${FRONTEND_IMAGE}" \
    -f "${ROOT_DIR}/frontend/Dockerfile" \
    "${ROOT_DIR}"
}

print_docker_hub_pull_help() {
  cat <<'EOF' >&2

Could not pull a base image from Docker Hub (often TLS handshake timeout or rate limits).

Try:
  1. Retry: ./scripts/deploy-k3s.sh k3s
  2. Pre-pull manually: docker pull python:3.11-slim-bookworm && docker pull node:20-bookworm-slim
  3. Configure a registry mirror in /etc/docker/daemon.json, then: sudo systemctl restart docker
     Example (pick a mirror reachable from your network):
       { "registry-mirrors": ["https://mirror.gcr.io"] }
  4. Use a mirror prefix via env (if your mirror hosts library images):
       BACKEND_BASE_IMAGE=your-mirror/library/python:3.11-slim-bookworm \
       FRONTEND_BASE_IMAGE=your-mirror/library/node:20-bookworm-slim \
       ./scripts/deploy-k3s.sh k3s

See docs/kubernetes.md — "Docker Hub pull timeouts".
EOF
}

pull_docker_image_with_retry() {
  local image="$1"
  local max_attempts="${DOCKER_PULL_RETRIES:-5}"
  local delay_sec="${DOCKER_PULL_RETRY_DELAY_SEC:-15}"
  local attempt=1

  while (( attempt <= max_attempts )); do
    echo "Pulling base image: ${image} (attempt ${attempt}/${max_attempts})"
    if docker pull "${image}"; then
      return 0
    fi
    if (( attempt == max_attempts )); then
      echo "Failed to pull ${image} after ${max_attempts} attempts." >&2
      print_docker_hub_pull_help
      return 1
    fi
    echo "Pull failed; retrying in ${delay_sec}s..."
    sleep "${delay_sec}"
    attempt=$((attempt + 1))
  done
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
  echo "Pulling postgres and redis images (so the cluster does not wait on first in-pod pull)"
  docker pull postgres:16-alpine
  docker pull redis:7-alpine
  echo "Importing images into k3d cluster: ${K3D_CLUSTER_NAME}"
  k3d image import postgres:16-alpine redis:7-alpine "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}" -c "${K3D_CLUSTER_NAME}"
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

  echo "Pulling postgres and redis into k3s containerd (avoids slow image pull during Postgres rollout)"
  sudo k3s ctr -n k8s.io images pull docker.io/library/postgres:16-alpine
  sudo k3s ctr -n k8s.io images pull docker.io/library/redis:7-alpine

  rm -rf "${tmp_dir}"
  trap - EXIT
}

print_postgres_debug_help() {
  cat <<'EOF' >&2

Postgres rollout failed. Common causes on k3s:
- PVC Pending + pod Node=<none> + Event "VolumeBinding: context deadline exceeded": local-path provisioner
  cannot bind storage in time — NOT initdb. Check: df -h; kubectl -n kube-system logs deployment/local-path-provisioner --tail=80;
  kubectl -n kube-system rollout restart deployment/local-path-provisioner; then delete pvc and re-apply if needed.
- PVC stuck Pending: no StorageClass or wrong name. Run: kubectl get storageclass && kubectl get pvc -n stock-ticker
  k3s usually expects storageClassName: local-path (see k8s/postgres.yaml).
- ext4 PVC root contains lost+found: Postgres must use PGDATA under a subdir (manifest sets /var/lib/postgresql/data/pgdata).
  If you deployed before that fix, delete the PVC and re-apply so the volume initializes cleanly.
- postgres:16-alpine still pulling (first boot needs network). Check: kubectl describe pod -n stock-ticker -l app=stock-ticker-postgres
- Volume permissions: see pod Events.
- RWO PVC + RollingUpdate: use strategy Recreate on stock-ticker-postgres (see k8s/postgres.yaml) so only one pod mounts the volume.
- Corrupt or half-initialized data on the PVC: delete deployment + PVC, re-apply (see README).

Debug:
  kubectl -n stock-ticker get pvc
  kubectl -n stock-ticker get pods -l app=stock-ticker-postgres
  kubectl -n stock-ticker describe pod -l app=stock-ticker-postgres
  kubectl -n stock-ticker logs -l app=stock-ticker-postgres --tail=80
  ./scripts/k8s-diagnose.sh k3s   # or: ./scripts/k8s-diagnose.sh k3d
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
  echo "  First-time init (initdb on the PVC) often takes 2–6+ minutes; this step is quiet until Ready."
  echo "  Watch elsewhere: ${KUBECTL_CMD} -n ${NAMESPACE} get pods -w"
  if ! ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-postgres --timeout=2460s; then
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

  if [[ "${OBSERVABILITY:-0}" == "1" ]]; then
    echo "Applying observability stack (Prometheus / Grafana / kube-state-metrics)"
    ${KUBECTL_CMD} apply -k "${ROOT_DIR}/k8s/observability"
    ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/kube-state-metrics --timeout=180s
    ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/prometheus --timeout=180s
    ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/grafana --timeout=180s
  fi
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

From this machine (port-forward):
  ${KUBECTL_CMD} -n ${NAMESPACE} port-forward svc/stock-ticker-frontend 3000:3000
  Then browse: http://127.0.0.1:3000

From other PCs on the LAN (NodePort — use your node's IP, e.g. from kubectl get nodes -o wide):
  http://<node-ip>:30080
  If the browser cannot connect, allow the port: sudo ufw allow 30080/tcp
EOF

if [[ "${OBSERVABILITY:-0}" == "1" ]]; then
  cat <<EOF

Observability (see docs/observability.md):
  ${KUBECTL_CMD} -n ${NAMESPACE} port-forward svc/grafana 3000:3000
  Grafana NodePort: http://<node-ip>:30300
  Dashboard: Kubernetes Workload Health
EOF
fi
