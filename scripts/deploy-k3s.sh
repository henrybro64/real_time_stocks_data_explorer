#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/k8s_images.sh
source "${ROOT_DIR}/scripts/lib/k8s_images.sh"
NAMESPACE="${NAMESPACE:-stock-ticker}"
BACKEND_IMAGE="${BACKEND_IMAGE:-${DEFAULT_BACKEND_IMAGE}}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-${DEFAULT_FRONTEND_IMAGE}}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-}"
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
  BACKEND_IMAGE / FRONTEND_IMAGE
                   Default local tags (stock-ticker-backend:local, stock-ticker-frontend:local).
                   Set both to ghcr.io/...:<tag> to skip local builds and pull from GHCR.
                   Registry-only k3s deploys do not require Docker.
                   See docs/kubernetes.md#github-container-registry.
  IMAGE_PULL_SECRET  Existing docker-registry secret name for private GHCR pulls.
                   Creates namespace stock-ticker first; fails if the secret is missing.
                   Do not commit the PAT.
  SKIP_IMAGE_BUILD=1   Skip docker build (default when both images are registry references).
  SKIP_IMAGE_IMPORT=1  Skip importing app images into k3d/k3s (default for registry images).
  BACKEND_BASE_IMAGE   Docker base for backend (default: python:3.11-slim-bookworm)
  FRONTEND_BASE_IMAGE  Docker base for frontend (default: node:20-bookworm-slim)
  DOCKER_PULL_RETRIES / DOCKER_PULL_RETRY_DELAY_SEC  Pre-pull retry tuning (default: 3 / 15)
  DOCKER_PULL_MIRRORS  Comma-separated mirror hosts (default: mirror.gcr.io,docker.m.daocloud.io)
  DOCKER_PULL_SKIP_MIRRORS=1  Skip mirror fallbacks when Docker Hub works but mirrors do not

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

should_skip_image_build() {
  if [[ "${SKIP_IMAGE_BUILD:-}" == "1" ]]; then
    return 0
  fi
  is_registry_image "${BACKEND_IMAGE}" && is_registry_image "${FRONTEND_IMAGE}"
}

should_skip_app_image_import() {
  if [[ "${SKIP_IMAGE_IMPORT:-}" == "1" ]]; then
    return 0
  fi
  is_registry_image "${BACKEND_IMAGE}" && is_registry_image "${FRONTEND_IMAGE}"
}

build_images() {
  if should_skip_image_build; then
    echo "Skipping local image builds (using ${BACKEND_IMAGE} and ${FRONTEND_IMAGE})"
    return 0
  fi

  local backend_base="${BACKEND_BASE_IMAGE:-python:3.11-slim-bookworm}"
  local frontend_base="${FRONTEND_BASE_IMAGE:-node:20-bookworm-slim}"

  if ! is_registry_image "${BACKEND_IMAGE}"; then
    pull_docker_image_with_retry "${backend_base}"
    echo "Building backend image: ${BACKEND_IMAGE}"
    docker build \
      --build-arg "PYTHON_BASE_IMAGE=${backend_base}" \
      -t "${BACKEND_IMAGE}" \
      -f "${ROOT_DIR}/backend/Dockerfile" \
      "${ROOT_DIR}"
  else
    echo "Skipping backend build; will use registry image ${BACKEND_IMAGE}"
  fi

  if ! is_registry_image "${FRONTEND_IMAGE}"; then
    pull_docker_image_with_retry "${frontend_base}"
    echo "Building frontend image: ${FRONTEND_IMAGE}"
    docker build \
      --build-arg "NODE_BASE_IMAGE=${frontend_base}" \
      -t "${FRONTEND_IMAGE}" \
      -f "${ROOT_DIR}/frontend/Dockerfile" \
      "${ROOT_DIR}"
  else
    echo "Skipping frontend build; will use registry image ${FRONTEND_IMAGE}"
  fi
}

print_docker_hub_pull_help() {
  cat <<'EOF' >&2

Could not pull a base image from Docker Hub (often TLS handshake timeout or rate limits).

Try:
  1. Pull via Google mirror, tag locally, then deploy:
       docker pull mirror.gcr.io/library/python:3.11-slim-bookworm
       docker tag mirror.gcr.io/library/python:3.11-slim-bookworm python:3.11-slim-bookworm
       docker pull mirror.gcr.io/library/node:20-bookworm-slim
       docker tag mirror.gcr.io/library/node:20-bookworm-slim node:20-bookworm-slim
       ./scripts/deploy-k3s.sh k3s
  2. Configure a registry mirror in /etc/docker/daemon.json, then: sudo systemctl restart docker
     Example: { "registry-mirrors": ["https://mirror.gcr.io"] }
  3. Override base images:
       BACKEND_BASE_IMAGE=mirror.gcr.io/library/python:3.11-slim-bookworm \
       FRONTEND_BASE_IMAGE=mirror.gcr.io/library/node:20-bookworm-slim \
       ./scripts/deploy-k3s.sh k3s
  4. Add a reachable mirror: DOCKER_PULL_MIRRORS=mirror.gcr.io,your.mirror.host ./scripts/deploy-k3s.sh k3s

See docs/kubernetes.md — "Docker Hub pull timeouts".
EOF
}

normalize_docker_library_image() {
  local image="$1"
  case "${image}" in
    docker.io/library/*)
      echo "${image#docker.io/library/}"
      ;;
    library/*)
      echo "${image#library/}"
      ;;
    *)
      echo "${image}"
      ;;
  esac
}

is_simple_docker_hub_library_image() {
  local image="$1"
  [[ "${image}" != */* ]]
}

docker_hub_library_mirror_ref() {
  local mirror_host="$1"
  local image="$2"
  local normalized name tag

  normalized="$(normalize_docker_library_image "${image}")"
  if ! is_simple_docker_hub_library_image "${normalized}"; then
    return 1
  fi

  name="${normalized%%:*}"
  tag="${normalized#*:}"
  if [[ "${name}" == "${tag}" ]]; then
    tag="latest"
  fi
  echo "${mirror_host%/}/library/${name}:${tag}"
}

try_docker_pull() {
  local image="$1"
  local max_attempts="$2"
  local delay_sec="$3"
  local attempt=1

  while (( attempt <= max_attempts )); do
    echo "Pulling base image: ${image} (attempt ${attempt}/${max_attempts})"
    if docker pull "${image}"; then
      return 0
    fi
    if (( attempt == max_attempts )); then
      return 1
    fi
    echo "Pull failed; retrying in ${delay_sec}s..."
    sleep "${delay_sec}"
    attempt=$((attempt + 1))
  done
}

pull_docker_image_with_retry() {
  local canonical_image="$1"
  local normalized_image
  local max_attempts="${DOCKER_PULL_RETRIES:-3}"
  local delay_sec="${DOCKER_PULL_RETRY_DELAY_SEC:-15}"
  local mirror_hosts="${DOCKER_PULL_MIRRORS:-mirror.gcr.io,docker.m.daocloud.io}"
  local mirror_host mirror_ref

  normalized_image="$(normalize_docker_library_image "${canonical_image}")"

  if try_docker_pull "${canonical_image}" "${max_attempts}" "${delay_sec}"; then
    return 0
  fi

  if [[ "${DOCKER_PULL_SKIP_MIRRORS:-0}" == "1" ]] || ! is_simple_docker_hub_library_image "${normalized_image}"; then
    echo "Failed to pull ${canonical_image}." >&2
    print_docker_hub_pull_help
    return 1
  fi

  echo "Docker Hub unreachable for ${canonical_image}; trying registry mirrors..."
  IFS=',' read -r -a mirror_list <<< "${mirror_hosts}"
  for mirror_host in "${mirror_list[@]}"; do
    mirror_host="${mirror_host#"${mirror_host%%[![:space:]]*}"}"
    mirror_host="${mirror_host%"${mirror_host##*[![:space:]]}"}"
    [[ -n "${mirror_host}" ]] || continue
    if ! mirror_ref="$(docker_hub_library_mirror_ref "${mirror_host}" "${normalized_image}")"; then
      continue
    fi
    if try_docker_pull "${mirror_ref}" 2 "${delay_sec}"; then
      echo "Tagging ${mirror_ref} as ${canonical_image}"
      docker tag "${mirror_ref}" "${canonical_image}"
      return 0
    fi
  done

  echo "Failed to pull ${canonical_image} from Docker Hub and all configured mirrors." >&2
  print_docker_hub_pull_help
  return 1
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
  local import_args=(postgres:16-alpine redis:7-alpine)
  if should_skip_app_image_import; then
    echo "Skipping app image import; cluster will pull ${BACKEND_IMAGE} and ${FRONTEND_IMAGE}"
  else
    if ! is_registry_image "${BACKEND_IMAGE}"; then
      import_args+=("${BACKEND_IMAGE}")
    fi
    if ! is_registry_image "${FRONTEND_IMAGE}"; then
      import_args+=("${FRONTEND_IMAGE}")
    fi
  fi
  k3d image import "${import_args[@]}" -c "${K3D_CLUSTER_NAME}"
}

import_into_k3s() {
  require_cmd k3s

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  # EXIT must not reference `tmp_dir` after this function returns (local goes out of scope with set -u).
  trap 'rm -rf "${tmp_dir}"' EXIT

  if should_skip_app_image_import; then
    echo "Skipping app image import; k3s will pull ${BACKEND_IMAGE} and ${FRONTEND_IMAGE}"
  else
    echo "Saving local images as tar archives"
    if ! is_registry_image "${BACKEND_IMAGE}"; then
      docker save -o "${tmp_dir}/backend.tar" "${BACKEND_IMAGE}"
      echo "Importing backend image into native k3s containerd"
      sudo k3s ctr -n k8s.io images import "${tmp_dir}/backend.tar"
    fi
    if ! is_registry_image "${FRONTEND_IMAGE}"; then
      docker save -o "${tmp_dir}/frontend.tar" "${FRONTEND_IMAGE}"
      echo "Importing frontend image into native k3s containerd"
      sudo k3s ctr -n k8s.io images import "${tmp_dir}/frontend.tar"
    fi
  fi

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

ensure_namespace() {
  echo "Ensuring namespace ${NAMESPACE} exists"
  ${KUBECTL_CMD} create namespace "${NAMESPACE}" --dry-run=client -o yaml | ${KUBECTL_CMD} apply -f -
}

ensure_image_pull_secret() {
  if [[ -z "${IMAGE_PULL_SECRET}" ]]; then
    if uses_custom_app_images "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}"; then
      echo "Warning: registry images without IMAGE_PULL_SECRET; private GHCR pulls will fail." >&2
    fi
    return 0
  fi

  ensure_namespace
  if ${KUBECTL_CMD} -n "${NAMESPACE}" get secret "${IMAGE_PULL_SECRET}" >/dev/null 2>&1; then
    return 0
  fi

  cat <<EOF >&2
Error: IMAGE_PULL_SECRET='${IMAGE_PULL_SECRET}' was not found in namespace ${NAMESPACE}.

Create the secret once after the namespace exists. Do not commit the PAT:

  ${KUBECTL_CMD} create namespace ${NAMESPACE}
  ${KUBECTL_CMD} -n ${NAMESPACE} create secret docker-registry ${IMAGE_PULL_SECRET} \\
    --docker-server=ghcr.io \\
    --docker-username=<github-username> \\
    --docker-password="\$CR_PAT"

Then re-run with IMAGE_PULL_SECRET=${IMAGE_PULL_SECRET}.
See docs/kubernetes.md#github-container-registry.
EOF
  exit 1
}

deploy_manifests() {
  echo "Applying Kubernetes manifests"
  ensure_image_pull_secret
  if uses_custom_app_images "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}" || [[ -n "${IMAGE_PULL_SECRET}" ]]; then
    local overlay_dir
    overlay_dir="$(mktemp -d)"
    # Expand the path now; RETURN must not read a function-local after it goes out of scope.
    # shellcheck disable=SC2064
    trap "rm -rf '${overlay_dir}'" RETURN
    write_k8s_image_overlay \
      "${overlay_dir}" \
      "${BACKEND_IMAGE}" \
      "${FRONTEND_IMAGE}" \
      "${IMAGE_PULL_SECRET}" \
      "${ROOT_DIR}/k8s"
    echo "Using image overlay: backend=${BACKEND_IMAGE} frontend=${FRONTEND_IMAGE}"
    ${KUBECTL_CMD} apply -k "${overlay_dir}"
  else
    ${KUBECTL_CMD} apply -k "${ROOT_DIR}/k8s"
  fi

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

needs_docker() {
  if [[ "${MODE}" == "k3d" ]]; then
    return 0
  fi
  if should_skip_image_build && should_skip_app_image_import; then
    return 1
  fi
  return 0
}

if [[ -z "${MODE}" ]]; then
  usage
  exit 1
fi

if needs_docker; then
  require_cmd docker
fi

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
