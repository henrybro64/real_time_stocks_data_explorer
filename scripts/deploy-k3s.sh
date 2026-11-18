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
STOCK_TICKER_HOSTNAME="${STOCK_TICKER_HOSTNAME:-stock-ticker.ai}"
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
                   After a successful local app-image import, backend/worker/frontend
                   Deployments are restarted so :local tags replace running pods (#303).
                   Registry and skip-import deploys do not restart those Deployments.
  BACKEND_BASE_IMAGE   Docker base for backend (default: python:3.11-slim-bookworm)
  FRONTEND_BASE_IMAGE  Docker base for frontend (default: node:20-bookworm-slim)
  DOCKER_PULL_RETRIES / DOCKER_PULL_RETRY_DELAY_SEC  Pre-pull retry tuning (default: 3 / 15)
  DOCKER_PULL_MIRRORS  Comma-separated mirror hosts (default: mirror.gcr.io,docker.m.daocloud.io)
  DOCKER_PULL_SKIP_MIRRORS=1  Skip mirror fallbacks when Docker Hub works but mirrors do not
  STOCK_TICKER_HOSTNAME  Ingress host (default: stock-ticker.ai). Point LAN DNS at the node IP.
  STOCK_TICKER_PUBLIC_SCHEME
                   http or https. Default is http for stock-ticker.ai and https
                   for any other host (including stock.cloudforge64.com).
                   SESSION_COOKIE_SECURE follows this scheme in both directions
                   (#299). The Entra redirect URI stays as committed.
  FORCE_AUTH_SECRET_APPLY=1
                   Apply k8s/backend-auth-secret.yaml even when the live Secret
                   differs. Default is to skip and print differing keys (#284).
                   The preflight then validates that local file, so a broken
                   live SESSION_SECRET can be repaired by a forced apply.
                   Never use this to rotate LOCAL_AUTH_PASSWORD casually (#286).
  FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1
                   After apply, force First-Pass drain CronJob suspend=true even
                   if the live cluster had suspend=false (#362 Stage D). Default
                   is to preserve an operator-approved unsuspend across redeploy
                   so git's default-disabled manifest does not silently disarm
                   an already-activated First-Pass CronJob. Never enables the
                   unrelated Taiwan Research schedule CronJob.

k3d one-time setup (before first deploy):
  k3d cluster create stock-ticker -p "80:80@loadbalancer"
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

# Set when a non-registry app image is imported into k3d/k3s this run (#303).
REFRESHED_LOCAL_BACKEND=0
REFRESHED_LOCAL_FRONTEND=0
# Ready replica counts sampled before apply. A first install is 0, so we do not
# restart pods that this same apply is about to create (#303, k3s smoke).
BACKEND_READY_BEFORE=0
WORKER_READY_BEFORE=0
FRONTEND_READY_BEFORE=0

mark_local_image_refreshed() {
  local image="$1"
  if is_registry_image "${image}"; then
    return 0
  fi
  if [[ "${image}" == "${BACKEND_IMAGE}" ]]; then
    REFRESHED_LOCAL_BACKEND=1
  fi
  if [[ "${image}" == "${FRONTEND_IMAGE}" ]]; then
    REFRESHED_LOCAL_FRONTEND=1
  fi
}

deployment_was_ready() {
  [[ "${1:-0}" =~ ^[1-9][0-9]*$ ]]
}

restart_local_app_workloads_if_imported() {
  local -a deploys=()
  # Restart only workloads that were already Ready. Fresh pods from this apply
  # resolve :local at start; restarting them leaves a terminating pod that
  # makes kubectl wait time out (k3s smoke).
  if [[ "${REFRESHED_LOCAL_BACKEND}" == "1" ]] && deployment_was_ready "${BACKEND_READY_BEFORE:-0}"; then
    deploys+=(stock-ticker-backend)
  fi
  if [[ "${REFRESHED_LOCAL_BACKEND}" == "1" ]] && deployment_was_ready "${WORKER_READY_BEFORE:-0}"; then
    deploys+=(stock-ticker-backend-worker)
  fi
  if [[ "${REFRESHED_LOCAL_FRONTEND}" == "1" ]] && deployment_was_ready "${FRONTEND_READY_BEFORE:-0}"; then
    deploys+=(stock-ticker-frontend)
  fi
  if [[ ${#deploys[@]} -eq 0 ]]; then
    if [[ "${REFRESHED_LOCAL_BACKEND}" != "1" && "${REFRESHED_LOCAL_FRONTEND}" != "1" ]]; then
      echo "Skipping app Deployment restart (no local app image was imported)"
    else
      echo "Skipping app Deployment restart (no previously ready app pods; fresh pods use the imported image)"
    fi
    return 0
  fi
  echo "Restarting ${deploys[*]} so imported :local images replace running pods (#303)"
  local deploy
  for deploy in "${deploys[@]}"; do
    if ! ${KUBECTL_CMD} -n "${NAMESPACE}" rollout restart "deployment/${deploy}"; then
      echo "Failed to restart deployment/${deploy} after importing a local image (#303)." >&2
      ${KUBECTL_CMD} -n "${NAMESPACE}" get pods -l "app=${deploy}" || true
      exit 1
    fi
  done
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
    # Bake git SHA + build time into NEXT_PUBLIC_* at `next build` (#357).
    # Runtime ConfigMap cannot rewrite these; unknown SHA means the deploy
    # path forgot --build-arg or an old image is still being served.
    local frontend_build_sha="${BUILD_SHA:-$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
    if [[ -z "${BUILD_SHA:-}" ]] && [[ -n "$(git -C "${ROOT_DIR}" status --porcelain 2>/dev/null || true)" ]]; then
      # Dirty working tree must not look like a clean published SHA (#357).
      if [[ "${frontend_build_sha}" != unknown ]] && [[ "${frontend_build_sha}" != *-dirty ]]; then
        frontend_build_sha="${frontend_build_sha}-dirty"
      fi
    fi
    local frontend_build_time="${BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
    echo "Building frontend image: ${FRONTEND_IMAGE} (BUILD_SHA=${frontend_build_sha})"
    docker build \
      --build-arg "NODE_BASE_IMAGE=${frontend_base}" \
      --build-arg "BUILD_SHA=${frontend_build_sha}" \
      --build-arg "BUILD_TIME=${frontend_build_time}" \
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
  k3d cluster create ${K3D_CLUSTER_NAME} -p "80:80@loadbalancer"
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
  k3d cluster create ${K3D_CLUSTER_NAME} -p "80:80@loadbalancer"
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
  if should_skip_app_image_import; then
    return 0
  fi
  if ! is_registry_image "${BACKEND_IMAGE}"; then
    mark_local_image_refreshed "${BACKEND_IMAGE}"
  fi
  if ! is_registry_image "${FRONTEND_IMAGE}"; then
    mark_local_image_refreshed "${FRONTEND_IMAGE}"
  fi
}

import_into_k3s() {
  require_cmd k3s

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  # Expand the path when registering the trap. A delayed EXIT after this function
  # returns must not expand the now-unbound local under ``set -u``.
  # shellcheck disable=SC2064
  trap "rm -rf $(printf '%q' "${tmp_dir}")" EXIT

  if should_skip_app_image_import; then
    echo "Skipping app image import; k3s will pull ${BACKEND_IMAGE} and ${FRONTEND_IMAGE}"
  else
    echo "Saving local images as tar archives"
    if ! is_registry_image "${BACKEND_IMAGE}"; then
      docker save -o "${tmp_dir}/backend.tar" "${BACKEND_IMAGE}"
      echo "Importing backend image into native k3s containerd"
      sudo k3s ctr -n k8s.io images import "${tmp_dir}/backend.tar"
      mark_local_image_refreshed "${BACKEND_IMAGE}"
    fi
    if ! is_registry_image "${FRONTEND_IMAGE}"; then
      docker save -o "${tmp_dir}/frontend.tar" "${FRONTEND_IMAGE}"
      echo "Importing frontend image into native k3s containerd"
      sudo k3s ctr -n k8s.io images import "${tmp_dir}/frontend.tar"
      mark_local_image_refreshed "${FRONTEND_IMAGE}"
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

DATA SAFETY: if this cluster already holds data, do NOT delete or recreate the PVC, the PV, or PGDATA,
and do not reinstall k3s. Back up first (scripts/k8s-backup-postgres.sh) and read docs/postgres-apparmor.md#recovery.

- Pod Running but never Ready, PID 1 stuck in "gosu postgres", nothing on 5432, or logs show
  "could not signal" / "Permission denied": AppArmor signal denial on Ubuntu 26.04 (#308).
  k8s/postgres.yaml sets appArmorProfile Unconfined on the postgres container; confirm the live Deployment has it.
  See docs/postgres-apparmor.md#diagnose.
- PVC Pending + pod Node=<none> + Event "VolumeBinding: context deadline exceeded": local-path provisioner
  cannot bind storage in time — NOT initdb. Check: df -h; kubectl -n kube-system logs deployment/local-path-provisioner --tail=80;
  kubectl -n kube-system rollout restart deployment/local-path-provisioner.
  Deleting the PVC is only safe on a first install that never held data.
- PVC stuck Pending: no StorageClass or wrong name. Run: kubectl get storageclass && kubectl get pvc -n stock-ticker
  k3s usually expects storageClassName: local-path (see k8s/postgres.yaml).
- ext4 PVC root contains lost+found: Postgres must use PGDATA under a subdir (manifest sets /var/lib/postgresql/data/pgdata).
- postgres:16-alpine still pulling (first boot needs network). Check: kubectl describe pod -n stock-ticker -l app=stock-ticker-postgres
- Volume permissions: see pod Events.
- RWO PVC + RollingUpdate: use strategy Recreate on stock-ticker-postgres (see k8s/postgres.yaml) so only one pod mounts the volume.
- Corrupt or half-initialized data on the PVC: stop and restore from a backup (scripts/k8s-restore-postgres.sh,
  docs/kubernetes.md). Never run initdb against existing data.

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
- If using host Postgres instead: wrong hostname / pg_hba — see docs/kubernetes.md#host-postgres-optional and scripts/k8s-test-host-postgres.sh

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

public_scheme() {
  local host="${STOCK_TICKER_HOSTNAME:-stock-ticker.ai}"
  local scheme="${STOCK_TICKER_PUBLIC_SCHEME:-}"
  if [[ -z "${scheme}" ]]; then
    if [[ "${host}" == "stock-ticker.ai" ]]; then
      scheme="http"
    else
      scheme="https"
    fi
  fi
  case "${scheme}" in
    http|https) printf '%s' "${scheme}" ;;
    *)
      echo "STOCK_TICKER_PUBLIC_SCHEME must be http or https" >&2
      exit 1
      ;;
  esac
}

public_origin() {
  local host="${STOCK_TICKER_HOSTNAME:-stock-ticker.ai}"
  case "${host}" in
    *[!A-Za-z0-9.-]*)
      echo "STOCK_TICKER_HOSTNAME must be a hostname" >&2
      exit 1
      ;;
  esac
  printf '%s://%s' "$(public_scheme)" "${host}"
}

ensure_backend_auth_secret() {
  local secret_path="${ROOT_DIR}/k8s/backend-auth-secret.yaml"
  local cookie_secure="false"
  if [[ "$(public_scheme)" == "https" ]]; then
    cookie_secure="true"
  fi
  if [[ ! -f "${secret_path}" ]]; then
    require_cmd openssl
    local session_secret capture_token
    session_secret="$(openssl rand -hex 32)"
    capture_token="$(openssl rand -hex 24)"
    # umask 077 so the session secret is never created world-readable.
    (
      umask 077
      cat >"${secret_path}" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: stock-ticker-backend-auth
type: Opaque
stringData:
  SESSION_SECRET: "${session_secret}"
  LOCAL_AUTH_USERNAME: ""
  LOCAL_AUTH_PASSWORD: ""
  PORTFOLIO_HISTORY_CAPTURE_TOKEN: "${capture_token}"
  SESSION_COOKIE_SECURE: "${cookie_secure}"
EOF
    )
    echo "Generated ${secret_path} (gitignored). Register a user after login, or set LOCAL_AUTH_* locally."
  else
    echo "Using existing ${secret_path}"
    _set_local_secret_key "${secret_path}" SESSION_COOKIE_SECURE "${cookie_secure}"
  fi
  chmod 600 "${secret_path}"
}

_set_local_secret_key() {
  # Structure-aware stringData edit only. Never prints the value. Validates key
  # name, requires a Secret document with stringData, and writes atomically (#362).
  SECRET_EDIT_PATH="$1" SECRET_EDIT_KEY="$2" SECRET_EDIT_VALUE="$3" python3 - <<'PY'
import os
import re
import tempfile
from pathlib import Path

path = Path(os.environ["SECRET_EDIT_PATH"])
key = os.environ["SECRET_EDIT_KEY"]
value = os.environ["SECRET_EDIT_VALUE"]

if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key or ""):
    raise SystemExit("refusing invalid Secret key name")
if not path.is_file():
    raise SystemExit(f"secret file missing: {path}")

text = path.read_text(encoding="utf-8")
if "kind: Secret" not in text and "kind:\"Secret\"" not in text:
    # Accept plain stringData-only fixtures used by unit harnesses.
    if "stringData:" not in text:
        raise SystemExit("refusing edit: not a Secret YAML with stringData")

def yaml_quote(raw: str) -> str:
    escaped = (
        raw.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'

lines = text.splitlines(keepends=True)
out: list[str] = []
in_string_data = False
found = False
string_data_indent = ""
pending_insert_indent = "  "

for line in lines:
    stripped = line.lstrip(" \t")
    indent = line[: len(line) - len(stripped)]
    if re.match(r"^stringData:\s*(#.*)?$", line.rstrip("\n\r")):
        in_string_data = True
        string_data_indent = indent
        pending_insert_indent = indent + "  "
        out.append(line)
        continue
    if in_string_data:
        # Blank / whitespace-only lines are not mapping boundaries (keepends
        # leaves "\n" in stripped after lstrip of spaces/tabs).
        content = stripped.strip()
        # Left the mapping when indentation returns to stringData level or less
        # on a non-empty, non-comment line.
        if content and not content.startswith("#"):
            if len(indent) <= len(string_data_indent):
                if not found:
                    out.append(f"{pending_insert_indent}{key}: {yaml_quote(value)}\n")
                    found = True
                in_string_data = False
                out.append(line)
                continue
            m = re.match(r"^([A-Za-z0-9_]+)\s*:", content)
            if m and m.group(1) == key:
                out.append(f"{indent}{key}: {yaml_quote(value)}\n")
                found = True
                continue
        out.append(line)
        continue
    out.append(line)

if in_string_data and not found:
    out.append(f"{pending_insert_indent}{key}: {yaml_quote(value)}\n")
    found = True
if not found:
    raise SystemExit("refusing edit: stringData block not found")

# Never print the secret value — only confirm the key name on stderr if needed.
new_text = "".join(out)
# Validate the written key appears under stringData without dumping values.
joined = "".join(out)
sd_idx = joined.find("stringData:")
if sd_idx < 0 or f"{key}:" not in joined[sd_idx:]:
    raise SystemExit("refusing edit: post-validate failed for stringData key")

fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
os.close(fd)
tmp_path = Path(tmp_name)
try:
    tmp_path.write_text(new_text, encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
finally:
    if tmp_path.exists():
        tmp_path.unlink(missing_ok=True)
PY
  unset SECRET_EDIT_PATH SECRET_EDIT_KEY SECRET_EDIT_VALUE
}

# Placeholder rules mirror backend/app/core/auth_settings.py so the preflight
# blocks exactly what the API rejects, no more (#284).
_lowercase() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'
}

_has_placeholder_marker() {
  local lowered
  lowered="$(_lowercase "${1:-}")"
  [[ "${lowered}" == *replace* || "${lowered}" == *change-me* || "${lowered}" == *changeme* ]]
}

_session_secret_is_placeholder() {
  local value="${1:-}"
  case "${value}" in
    dev-session-secret-change-me|k8s-dev-replace-with-openssl-rand-hex-32-chars-minimum-16|change-me|secret|session-secret)
      return 0
      ;;
  esac
  _has_placeholder_marker "${value}"
}

_capture_token_is_placeholder() {
  local value="${1:-}"
  [[ -z "${value}" ]] && return 1
  case "${value}" in
    k8s-dev-replace-with-random-capture-token|change-me|capture-token|replace-with-long-random-token)
      return 0
      ;;
  esac
  _has_placeholder_marker "${value}"
}

_bootstrap_password_is_placeholder() {
  # Backend compares the trimmed, casefolded value against an exact list only.
  local trimmed
  trimmed="$(printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$(_lowercase "${trimmed}")" in
    change-me-local-password|change-me|password|admin)
      return 0
      ;;
  esac
  return 1
}

_live_secret_data_value() {
  local secret_name="$1"
  local key="$2"
  local encoded
  encoded="$(
    ${KUBECTL_CMD} -n "${NAMESPACE}" get secret "${secret_name}" \
      -o "jsonpath={.data.${key}}" 2>/dev/null || true
  )"
  if [[ -z "${encoded}" ]]; then
    return 0
  fi
  # macOS base64 uses -D; GNU uses -d / --decode.
  printf '%s' "${encoded}" | base64 -d 2>/dev/null \
    || printf '%s' "${encoded}" | base64 -D 2>/dev/null \
    || true
}

_preflight_auth_value() {
  local key="$1"
  if [[ "${PREFLIGHT_AUTH_SOURCE}" == "local" ]]; then
    _local_auth_secret_value "${ROOT_DIR}/k8s/backend-auth-secret.yaml" "${key}"
  else
    _live_secret_data_value stock-ticker-backend-auth "${key}"
  fi
}

preflight_live_auth_secrets() {
  # Stop before rollout when the auth Secret the pods will see holds values the
  # new image rejects (#284). Capture-token placeholders no longer crash the API,
  # but SESSION_SECRET still does. This step never rewrites LOCAL_AUTH_*.
  # With FORCE_AUTH_SECRET_APPLY=1 the local file replaces the live Secret, so
  # check that file instead; otherwise a broken live Secret could never be repaired.
  local problems=()
  local session_secret portfolio_token archive_token local_password
  local rankings_token taiwan_token local_user auth_label reset_password
  local local_secret_path="${ROOT_DIR}/k8s/backend-auth-secret.yaml"

  PREFLIGHT_AUTH_SOURCE=""
  if [[ "${FORCE_AUTH_SECRET_APPLY:-0}" == "1" && -f "${local_secret_path}" ]]; then
    PREFLIGHT_AUTH_SOURCE="local"
    auth_label="${local_secret_path}"
  elif ${KUBECTL_CMD} -n "${NAMESPACE}" get secret stock-ticker-backend-auth >/dev/null 2>&1; then
    PREFLIGHT_AUTH_SOURCE="live"
    auth_label="stock-ticker-backend-auth"
  fi

  if [[ -n "${PREFLIGHT_AUTH_SOURCE}" ]]; then
    session_secret="$(_preflight_auth_value SESSION_SECRET)"
    portfolio_token="$(_preflight_auth_value PORTFOLIO_HISTORY_CAPTURE_TOKEN)"
    rankings_token="$(_preflight_auth_value RANKINGS_CAPTURE_TOKEN)"
    taiwan_token="$(_preflight_auth_value TAIWAN_MARKET_DATA_CAPTURE_TOKEN)"
    local_user="$(_preflight_auth_value LOCAL_AUTH_USERNAME)"
    local_password="$(_preflight_auth_value LOCAL_AUTH_PASSWORD)"
    # Backend strips SESSION_SECRET before checking length and placeholders.
    session_secret="$(printf '%s' "${session_secret}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    if [[ -z "${session_secret}" ]]; then
      problems+=("${auth_label}/SESSION_SECRET is empty (API will refuse to start)")
    elif [[ ${#session_secret} -lt 16 ]]; then
      problems+=("${auth_label}/SESSION_SECRET is shorter than 16 characters (API will refuse to start)")
    elif _session_secret_is_placeholder "${session_secret}"; then
      problems+=("${auth_label}/SESSION_SECRET is a placeholder (API will refuse to start)")
    fi
    if [[ -n "${local_user}" && -z "${local_password}" ]] || [[ -z "${local_user}" && -n "${local_password}" ]]; then
      problems+=("${auth_label} LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD must both be set or both be empty")
    elif [[ -n "${local_password}" && ${#local_password} -lt 8 ]]; then
      problems+=("${auth_label}/LOCAL_AUTH_PASSWORD is shorter than 8 characters (API will refuse to start)")
    fi
    if _capture_token_is_placeholder "${portfolio_token}"; then
      problems+=("${auth_label}/PORTFOLIO_HISTORY_CAPTURE_TOKEN is a placeholder (capture disabled until rotated)")
    fi
    if _capture_token_is_placeholder "${rankings_token}"; then
      problems+=("${auth_label}/RANKINGS_CAPTURE_TOKEN is a placeholder (capture disabled until rotated)")
    fi
    if _capture_token_is_placeholder "${taiwan_token}"; then
      problems+=("${auth_label}/TAIWAN_MARKET_DATA_CAPTURE_TOKEN is a placeholder (capture disabled until rotated)")
    fi
    reset_password="$(_lowercase "$(_preflight_auth_value LOCAL_AUTH_RESET_PASSWORD)")"
    case "${reset_password}" in
      1|true|yes|on)
        problems+=("${auth_label}/LOCAL_AUTH_RESET_PASSWORD is on: every API restart resets the bootstrap account's password. Turn it off after a one-time reset (#286)")
        ;;
    esac
    if [[ -n "${local_password}" ]] && _bootstrap_password_is_placeholder "${local_password}"; then
      problems+=("${auth_label}/LOCAL_AUTH_PASSWORD is a placeholder — clear BOTH LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD (do not set a new password; that can overwrite a stored account when LOCAL_AUTH_RESET_PASSWORD is enabled)")
    fi
  fi

  if ${KUBECTL_CMD} -n "${NAMESPACE}" get secret stock-ticker-backend-rankings-archive >/dev/null 2>&1; then
    archive_token="$(_live_secret_data_value stock-ticker-backend-rankings-archive RANKINGS_ARCHIVE_CAPTURE_TOKEN)"
    if _capture_token_is_placeholder "${archive_token}"; then
      problems+=("stock-ticker-backend-rankings-archive/RANKINGS_ARCHIVE_CAPTURE_TOKEN is a placeholder (capture disabled until rotated)")
    fi
  fi

  if ((${#problems[@]} == 0)); then
    return 0
  fi

  echo "Preflight failed: Secrets contain values the new image will reject or disable (#284):" >&2
  local item
  for item in "${problems[@]}"; do
    echo "  - ${item}" >&2
  done
  cat <<'EOF' >&2

Fix SESSION_SECRET (required) before redeploying, either by fixing
k8s/backend-auth-secret.yaml and re-running with FORCE_AUTH_SECRET_APPLY=1, or live:
  kubectl -n stock-ticker patch secret stock-ticker-backend-auth --type merge \
    -p "{\"stringData\":{\"SESSION_SECRET\":\"$(openssl rand -hex 32)\"}}"

For a placeholder archive capture token (optional; API still boots after #284):
  kubectl -n stock-ticker patch secret stock-ticker-backend-rankings-archive --type merge \
    -p "{\"stringData\":{\"RANKINGS_ARCHIVE_CAPTURE_TOKEN\":\"$(openssl rand -hex 32)\"}}"

Do NOT set a new LOCAL_AUTH_PASSWORD to silence a placeholder check — clear both
LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD instead (#286).
EOF
  # Only hard-fail on SESSION_SECRET / LOCAL_AUTH_PASSWORD problems.
  local hard=0
  for item in "${problems[@]}"; do
    if [[ "${item}" == *SESSION_SECRET* || "${item}" == *LOCAL_AUTH_PASSWORD* ]]; then
      hard=1
    fi
  done
  if [[ "${hard}" -eq 1 ]]; then
    exit 1
  fi
  echo "Continuing: remaining findings are non-fatal warnings." >&2
}

_local_auth_secret_value() {
  local secret_path="$1"
  local key="$2"
  python3 - <<'PY' "${secret_path}" "${key}"
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
wanted = sys.argv[2]
values = {}
in_string_data = False
for line in text.splitlines():
    if line.strip() == "stringData:":
        in_string_data = True
        continue
    if in_string_data and line and not line[0].isspace():
        break
    stripped = line.strip()
    if in_string_data and stripped and not stripped.startswith("#") and ":" in stripped:
        name, _, raw = stripped.partition(":")
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        values[name.strip()] = raw
if wanted == "--keys":
    print("\n".join(sorted(values)))
else:
    print(values.get(wanted, ""))
PY
}

_live_secret_keys() {
  ${KUBECTL_CMD} -n "${NAMESPACE}" get secret "$1" \
    -o go-template='{{range $k, $v := .data}}{{$k}}{{"\n"}}{{end}}' 2>/dev/null || true
}

apply_backend_auth_secret() {
  ensure_backend_auth_secret
  local secret_path="${ROOT_DIR}/k8s/backend-auth-secret.yaml"
  if ${KUBECTL_CMD} -n "${NAMESPACE}" get secret stock-ticker-backend-auth >/dev/null 2>&1; then
    # Compare every key in either Secret, not a hand-picked list, so a stale local
    # value (for example LOCAL_AUTH_RESET_PASSWORD=true) cannot slip through (#286).
    local differing=() key live_value local_value
    while IFS= read -r key; do
      [[ -z "${key}" ]] && continue
      live_value="$(_live_secret_data_value stock-ticker-backend-auth "${key}")"
      local_value="$(_local_auth_secret_value "${secret_path}" "${key}")"
      if [[ "${live_value}" != "${local_value}" ]]; then
        differing+=("${key}")
      fi
    done < <(
      {
        _live_secret_keys stock-ticker-backend-auth
        _local_auth_secret_value "${secret_path}" --keys
      } | sort -u
    )

    if ((${#differing[@]} > 0)) && [[ "${FORCE_AUTH_SECRET_APPLY:-0}" != "1" ]]; then
      echo "Skipping apply of ${secret_path}: live Secret differs on: ${differing[*]}"
      echo "Set FORCE_AUTH_SECRET_APPLY=1 to overwrite. Do not overwrite LOCAL_AUTH_* casually (#284/#286)."
      _sync_session_cookie_secure
      return 0
    fi
  fi
  ${KUBECTL_CMD} apply -n "${NAMESPACE}" -f "${secret_path}"
  _sync_session_cookie_secure
}

_sync_session_cookie_secure() {
  # Match the selected public scheme in both directions. Leaving Secure=true
  # after a switch back to HTTP makes browsers drop the session cookie (#299).
  local desired="false"
  if [[ "$(public_scheme)" == "https" ]]; then
    desired="true"
  fi
  local live_cookie
  live_cookie="$(_live_secret_data_value stock-ticker-backend-auth SESSION_COOKIE_SECURE)"
  if [[ "${live_cookie}" == "${desired}" ]]; then
    return 0
  fi
  echo "Setting SESSION_COOKIE_SECURE=${desired} for $(public_origin) (#299)"
  ${KUBECTL_CMD} -n "${NAMESPACE}" patch secret stock-ticker-backend-auth --type merge \
    -p "{\"stringData\":{\"SESSION_COOKIE_SECURE\":\"${desired}\"}}"
}

apply_backend_entra_secret() {
  # The live cluster already has Secret stock-ticker-entra. Never replace it
  # from this script: that would risk overwriting ENTRA_CLIENT_SECRET (#299).
  # The client id is public and lives in the ConfigMap.
  if ${KUBECTL_CMD} -n "${NAMESPACE}" get secret stock-ticker-entra >/dev/null 2>&1; then
    local live_secret
    live_secret="$(_live_secret_data_value stock-ticker-entra ENTRA_CLIENT_SECRET)"
    if [[ -n "${live_secret}" ]]; then
      echo "Keeping existing Secret stock-ticker-entra (ENTRA_CLIENT_SECRET is set; value not printed)"
    else
      echo "Secret stock-ticker-entra exists but ENTRA_CLIENT_SECRET is empty. Leaving it unchanged."
    fi
    return 0
  fi
  local secret_path="${ROOT_DIR}/k8s/backend-entra-secret.yaml"
  if [[ -f "${secret_path}" ]]; then
    local local_secret
    local_secret="$(_local_auth_secret_value "${secret_path}" ENTRA_CLIENT_SECRET)"
    if [[ -n "${local_secret}" ]]; then
      echo "Applying ${secret_path} (Secret stock-ticker-entra was absent)"
      ${KUBECTL_CMD} apply -n "${NAMESPACE}" -f "${secret_path}"
      return 0
    fi
  fi
  echo "Secret stock-ticker-entra was not found. Microsoft token exchange has no client secret until you create it."
  echo "Do not print the value. Example:"
  echo "  kubectl -n ${NAMESPACE} create secret generic stock-ticker-entra --from-literal=ENTRA_CLIENT_SECRET=..."
}

warn_if_entra_redirect_host_differs() {
  local redirect_host host
  host="${STOCK_TICKER_HOSTNAME:-stock-ticker.ai}"
  redirect_host="$(
    python3 - "${ROOT_DIR}/k8s/backend.yaml" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'ENTRA_REDIRECT_URI:\s*"https?://([^"/]+)', text)
print(match.group(1) if match else "")
PY
  )"
  if [[ -n "${redirect_host}" && "${redirect_host}" != "${host}" ]]; then
    echo "Warning: ENTRA_REDIRECT_URI host is ${redirect_host} but Ingress host is ${host}." >&2
    echo "Deploy with STOCK_TICKER_HOSTNAME=${redirect_host} so the browser origin matches Entra (#299)." >&2
  fi
}

render_public_origin_manifests() {
  local dest="$1"
  rm -rf "${dest}"
  mkdir -p "${dest}"
  cp -R "${ROOT_DIR}/k8s/." "${dest}/"
  # Leave Entra redirect URIs as committed. Rewriting them would drop
  # https://stock.cloudforge64.com on a default stock-ticker.ai deploy (#299).
  PUBLIC_RENDER_DEST="${dest}" \
    PUBLIC_RENDER_HOST="${STOCK_TICKER_HOSTNAME}" \
    python3 - <<'PY'
import os
import re
from pathlib import Path

dest = Path(os.environ["PUBLIC_RENDER_DEST"])
host = os.environ["PUBLIC_RENDER_HOST"]
ingress = dest / "frontend-ingress.yaml"
ingress_text = ingress.read_text(encoding="utf-8")
ingress_text, host_count = re.subn(
    r"host: stock-ticker\.ai",
    f"host: {host}",
    ingress_text,
    count=1,
)
if host_count != 1:
    raise SystemExit("frontend-ingress.yaml is missing the stock-ticker.ai host")
ingress.write_text(ingress_text, encoding="utf-8")
PY
  unset PUBLIC_RENDER_DEST PUBLIC_RENDER_ORIGIN PUBLIC_RENDER_HOST
}

apply_frontend_ingress_host() {
  local ingress_path="${ROOT_DIR}/k8s/frontend-ingress.yaml"
  local rendered
  rendered="$(mktemp)"
  sed "s/host: stock-ticker\\.ai/host: ${STOCK_TICKER_HOSTNAME}/" \
    "${ingress_path}" >"${rendered}"
  echo "Applying Ingress host: ${STOCK_TICKER_HOSTNAME}"
  ${KUBECTL_CMD} apply -n "${NAMESPACE}" -f "${rendered}"
  rm -f "${rendered}"
}

# First-Pass drain CronJob (#362 Stage D). Git stays default-disabled; redeploy
# must not wipe an operator-approved unsuspend. Schedule CronJob is never preserved enabled.
FIRST_PASS_DRAIN_CRONJOB_NAME="${FIRST_PASS_DRAIN_CRONJOB_NAME:-stock-ticker-tw-research-first-pass-drain}"
TW_RESEARCH_SCHEDULE_CRONJOB_NAME="${TW_RESEARCH_SCHEDULE_CRONJOB_NAME:-stock-ticker-tw-research-schedule}"
FIRST_PASS_DRAIN_DESIRED_SUSPEND_ANNOTATION="${FIRST_PASS_DRAIN_DESIRED_SUSPEND_ANNOTATION:-stock-ticker.ai/first-pass-drain-desired-suspend}"

read_cronjob_suspend() {
  # Prints: true | false | missing (confirmed NotFound / empty suspend).
  # Returns 1 on API/network/RBAC or other non-NotFound kubectl failures so
  # callers can fail closed instead of treating errors as absence.
  local name="$1"
  local err_file out rc=0 err=""
  local restore_e=0
  [[ $- == *e* ]] && restore_e=1
  err_file="$(mktemp)"
  set +e
  out="$(${KUBECTL_CMD} -n "${NAMESPACE}" get cronjob "${name}" -o jsonpath='{.spec.suspend}' 2>"${err_file}")"
  rc=$?
  if [[ "${restore_e}" -eq 1 ]]; then set -e; else set +e; fi
  err="$(tr -d '\r' <"${err_file}" | tr '\n' ' ')"
  rm -f "${err_file}"
  if [[ "${rc}" -eq 0 ]]; then
    if [[ -z "${out}" ]]; then
      echo "missing"
      return 0
    fi
    echo "${out}"
    return 0
  fi
  if grep -qiE '(NotFound|not found)' <<<"${err}"; then
    echo "missing"
    return 0
  fi
  echo "ERROR: cannot determine CronJob ${name} suspend state${err:+ (${err})}" >&2
  return 1
}

normalize_desired_suspend_annotation() {
  # go-template index on a missing key can print "<no value>"; treat as unset.
  local raw="${1:-}"
  raw="${raw//$'\r'/}"
  if [[ -z "${raw}" || "${raw}" == "<no value>" ]]; then
    echo ""
    return 0
  fi
  echo "${raw}"
}

read_cronjob_desired_suspend_annotation() {
  # Prints annotation value or empty when absent/unreadable.
  local name="$1"
  local raw
  raw="$(
    ${KUBECTL_CMD} -n "${NAMESPACE}" get cronjob "${name}" \
      -o "go-template={{index .metadata.annotations \"${FIRST_PASS_DRAIN_DESIRED_SUSPEND_ANNOTATION}\"}}" \
      2>/dev/null || true
  )"
  normalize_desired_suspend_annotation "${raw}"
}

capture_first_pass_drain_cron_suspend() {
  # Capture live suspend + optional operator annotation before kubectl apply -k
  # rewrites the object from git (suspend: true). Fail closed if live state is
  # unknowable (API/RBAC/network) — never treat that as "missing".
  local suspend_rc=0
  if FIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE="$(read_cronjob_suspend "${FIRST_PASS_DRAIN_CRONJOB_NAME}")"; then
    suspend_rc=0
  else
    suspend_rc=$?
  fi
  if [[ "${suspend_rc}" -ne 0 ]]; then
    echo "ERROR: refusing deploy — cannot determine First-Pass drain CronJob suspend state before apply" >&2
    exit 1
  fi
  FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE="$(read_cronjob_desired_suspend_annotation "${FIRST_PASS_DRAIN_CRONJOB_NAME}")"
  echo "First-Pass drain CronJob pre-apply suspend=${FIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE} desired=${FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE:-unset}"
}

rewrite_rendered_first_pass_drain_suspend() {
  # Smallest reliable preserve: write approved suspend into rendered manifests
  # before kubectl apply so apply itself does not flip live false → git true.
  local root="$1"
  local suspend_bool="$2"
  local file="${root}/backend-tw-research-first-pass-drain-cronjob.yaml"
  if [[ ! -f "${file}" ]]; then
    echo "ERROR: missing rendered First-Pass CronJob manifest: ${file}" >&2
    return 1
  fi
  FIRST_PASS_DRAIN_RENDER_FILE="${file}" FIRST_PASS_DRAIN_RENDER_SUSPEND="${suspend_bool}" python3 - <<'PY'
import os
import pathlib
import re
import sys

path = pathlib.Path(os.environ["FIRST_PASS_DRAIN_RENDER_FILE"])
want = os.environ["FIRST_PASS_DRAIN_RENDER_SUSPEND"]
if want not in {"true", "false"}:
    raise SystemExit(f"invalid suspend bool: {want!r}")
text = path.read_text(encoding="utf-8")
new, n = re.subn(
    r"(?m)^(\s*suspend:\s*)(true|false)\s*$",
    rf"\g<1>{want}",
    text,
    count=1,
)
if n != 1:
    raise SystemExit(f"could not rewrite CronJob suspend in {path}")
path.write_text(new, encoding="utf-8")
print(f"rendered {path.name} spec.suspend={want}", file=sys.stderr)
PY
}

prepare_rendered_first_pass_drain_suspend() {
  # Git stays default-disabled. Only rewrite rendered YAML when live was false
  # (operator-approved). FORCE keeps rendered suspend=true.
  local root="$1"
  if [[ "${FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND:-}" == "1" ]]; then
    echo "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1 — rendered First-Pass CronJob stays suspend=true"
    return 0
  fi
  if [[ "${FIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE:-}" == "false" ]]; then
    echo "Rendering First-Pass drain CronJob suspend=false into apply manifests (live was unsuspended)"
    rewrite_rendered_first_pass_drain_suspend "${root}" false
    return $?
  fi
  return 0
}

patch_cronjob_suspend() {
  local name="$1"
  local suspend_bool="$2"
  ${KUBECTL_CMD} -n "${NAMESPACE}" patch cronjob "${name}" --type merge \
    -p "{\"spec\":{\"suspend\":${suspend_bool}}}"
}

annotate_first_pass_drain_desired_suspend() {
  local suspend_bool="$1"
  ${KUBECTL_CMD} -n "${NAMESPACE}" annotate cronjob "${FIRST_PASS_DRAIN_CRONJOB_NAME}" \
    "${FIRST_PASS_DRAIN_DESIRED_SUSPEND_ANNOTATION}=${suspend_bool}" --overwrite
}

restore_first_pass_drain_cron_suspend() {
  # After apply: preferred path already rendered live suspend=false into
  # manifests. This still reconciles annotation / FORCE and patches if apply
  # left the object suspended. Annotation alone must not re-enable a paused
  # CronJob. FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1 re-disables.
  local before="${FIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE:-missing}"
  local desired
  desired="$(normalize_desired_suspend_annotation "${FIRST_PASS_DRAIN_CRON_DESIRED_BEFORE:-}")"
  local after="" after_rc=0
  if after="$(read_cronjob_suspend "${FIRST_PASS_DRAIN_CRONJOB_NAME}")"; then
    after_rc=0
  else
    after_rc=$?
  fi
  if [[ "${after_rc}" -ne 0 ]]; then
    echo "ERROR: cannot determine First-Pass drain CronJob suspend state after apply" >&2
    return 1
  fi
  if [[ "${after}" == "missing" ]]; then
    echo "First-Pass drain CronJob missing after apply — skip suspend restore" >&2
    return 0
  fi
  if [[ "${FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND:-}" == "1" ]]; then
    echo "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1 — forcing First-Pass drain CronJob suspend=true"
    patch_cronjob_suspend "${FIRST_PASS_DRAIN_CRONJOB_NAME}" true
    annotate_first_pass_drain_desired_suspend true
    return 0
  fi
  if [[ "${before}" == "false" ]]; then
    if [[ "${after}" == "false" ]]; then
      echo "First-Pass drain CronJob already suspend=false after apply (rendered preserve)"
      annotate_first_pass_drain_desired_suspend false || true
      return 0
    fi
    echo "Preserving operator-approved First-Pass drain CronJob suspend=false across redeploy (#362)"
    if ! patch_cronjob_suspend "${FIRST_PASS_DRAIN_CRONJOB_NAME}" false; then
      echo "ERROR: First-Pass drain left suspended (was active) — re-run CONFIRM=YES scripts/tw-first-pass-cronjob-stage-d.sh enable k3s" >&2
      return 1
    fi
    annotate_first_pass_drain_desired_suspend false || true
    return 0
  fi
  if [[ "${before}" == "true" && "${desired}" == "false" ]]; then
    echo "WARNING: First-Pass drain live suspend=true but desired=false — leaving suspended; run CONFIRM=YES scripts/tw-first-pass-cronjob-stage-d.sh enable k3s if intended" >&2
    annotate_first_pass_drain_desired_suspend true || true
    echo "First-Pass drain CronJob remains suspended (live suspend=true; annotation reconciled to true)"
    return 0
  fi
  # Leave git default (suspend=true). Record desired=true when CronJob exists.
  annotate_first_pass_drain_desired_suspend true || true
  echo "First-Pass drain CronJob remains suspended (git default / operator-disabled)"
}

apply_kustomize_preserving_first_pass_suspend() {
  # Run kubectl apply -k. On failure, still restore live suspend=false when
  # captured before apply so a partial rewrite cannot leave an active CronJob
  # stuck at git's suspend=true without an explicit restore.
  local path="$1"
  local apply_rc=0
  # Use && so set -e does not abort before we capture the real apply status
  # (a failed command in `if cmd; fi` leaves $?==0 after fi).
  ${KUBECTL_CMD} apply -k "${path}" && return 0
  apply_rc=$?
  echo "kubectl apply -k failed (rc=${apply_rc})" >&2
  if [[ "${FIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE:-}" == "false" ]]; then
    restore_first_pass_drain_cron_suspend || true
    echo "First-Pass drain suspend state restored after failed apply" >&2
  else
    echo "First-Pass drain CronJob left suspended — re-run enable" >&2
  fi
  return "${apply_rc}"
}

ensure_tw_research_schedule_cronjob_suspended() {
  # Unrelated Taiwan Research schedule CronJob must stay disabled (#330/#362).
  local state="" state_rc=0
  if state="$(read_cronjob_suspend "${TW_RESEARCH_SCHEDULE_CRONJOB_NAME}")"; then
    state_rc=0
  else
    state_rc=$?
  fi
  if [[ "${state_rc}" -ne 0 ]]; then
    echo "ERROR: cannot determine Taiwan Research schedule CronJob suspend state" >&2
    return 1
  fi
  if [[ "${state}" == "missing" ]]; then
    echo "Taiwan Research schedule CronJob not present — ok"
    return 0
  fi
  if [[ "${state}" != "true" ]]; then
    echo "WARNING: ${TW_RESEARCH_SCHEDULE_CRONJOB_NAME} suspend=${state}; forcing suspend=true (#362)" >&2
    patch_cronjob_suspend "${TW_RESEARCH_SCHEDULE_CRONJOB_NAME}" true
  else
    echo "Taiwan Research schedule CronJob remains suspended"
  fi
}

first_pass_drain_interrupt_restore() {
  # INT/TERM/ERR during deploy after capture: restore live unsuspend once.
  # ERR while an explicit restore is in progress must not recurse.
  if [[ "${FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE:-0}" == "1" ]]; then
    return 0
  fi
  if [[ "${FIRST_PASS_DRAIN_INTERRUPT_RESTORE_DONE:-0}" == "1" ]]; then
    return 0
  fi
  FIRST_PASS_DRAIN_INTERRUPT_RESTORE_DONE=1
  if [[ "${FIRST_PASS_DRAIN_CRON_SUSPEND_BEFORE:-}" != "false" ]]; then
    return 0
  fi
  restore_first_pass_drain_cron_suspend || true
  echo "First-Pass drain restore attempted after interrupted deploy" >&2
}

first_pass_drain_on_signal() {
  # Bash resumes after a returning INT/TERM trap; restore then exit so deploy stops.
  local code="${1:-1}"
  # INT/TERM during explicit restore: allow interrupt restore to still patch.
  if [[ "${FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE:-0}" == "1" ]]; then
    FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE=0
    FIRST_PASS_DRAIN_INTERRUPT_RESTORE_DONE=0
  fi
  first_pass_drain_interrupt_restore
  clear_first_pass_drain_interrupt_trap
  exit "${code}"
}

install_first_pass_drain_interrupt_trap() {
  FIRST_PASS_DRAIN_INTERRUPT_RESTORE_DONE=0
  FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE=0
  # INT/TERM must exit; ERR restores only (set -e exits the failing command path).
  trap 'first_pass_drain_on_signal 130' INT
  trap 'first_pass_drain_on_signal 143' TERM
  trap first_pass_drain_interrupt_restore ERR
}

clear_first_pass_drain_interrupt_trap() {
  trap - INT TERM ERR
}

deploy_manifests() {
  echo "Applying Kubernetes manifests"
  BACKEND_READY_BEFORE="$(${KUBECTL_CMD} -n "${NAMESPACE}" get deploy stock-ticker-backend -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  WORKER_READY_BEFORE="$(${KUBECTL_CMD} -n "${NAMESPACE}" get deploy stock-ticker-backend-worker -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  FRONTEND_READY_BEFORE="$(${KUBECTL_CMD} -n "${NAMESPACE}" get deploy stock-ticker-frontend -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  ensure_image_pull_secret
  ensure_namespace
  preflight_live_auth_secrets
  apply_backend_auth_secret
  apply_backend_entra_secret
  local rendered_k8s
  rendered_k8s="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '${rendered_k8s}'" RETURN
  render_public_origin_manifests "${rendered_k8s}"
  warn_if_entra_redirect_host_differs
  capture_first_pass_drain_cron_suspend
  if ! prepare_rendered_first_pass_drain_suspend "${rendered_k8s}"; then
    echo "ERROR: refusing deploy — could not render First-Pass drain suspend into manifests" >&2
    exit 1
  fi
  install_first_pass_drain_interrupt_trap
  if uses_custom_app_images "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}" || [[ -n "${IMAGE_PULL_SECRET}" ]]; then
    local overlay_dir
    overlay_dir="$(mktemp -d)"
    # Expand the path now; RETURN must not read a function-local after it goes out of scope.
    # Keep RETURN cleanup; INT/TERM/ERR remain the interrupt restore trap.
    # shellcheck disable=SC2064
    trap "rm -rf '${overlay_dir}' '${rendered_k8s}'" RETURN
    write_k8s_image_overlay \
      "${overlay_dir}" \
      "${BACKEND_IMAGE}" \
      "${FRONTEND_IMAGE}" \
      "${IMAGE_PULL_SECRET}" \
      "${rendered_k8s}"
    echo "Using image overlay: backend=${BACKEND_IMAGE} frontend=${FRONTEND_IMAGE}"
    if ! apply_kustomize_preserving_first_pass_suspend "${overlay_dir}"; then
      clear_first_pass_drain_interrupt_trap
      exit 1
    fi
  else
    if ! apply_kustomize_preserving_first_pass_suspend "${rendered_k8s}"; then
      clear_first_pass_drain_interrupt_trap
      exit 1
    fi
  fi
  # Explicit restore reconciles annotation / FORCE; INT/TERM can still recover
  # until this finishes (do not pre-mark interrupt-done).
  FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE=1
  if ! restore_first_pass_drain_cron_suspend; then
    FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE=0
    clear_first_pass_drain_interrupt_trap
    exit 1
  fi
  FIRST_PASS_DRAIN_IN_EXPLICIT_RESTORE=0
  clear_first_pass_drain_interrupt_trap
  ensure_tw_research_schedule_cronjob_suspended
  apply_frontend_ingress_host
  restart_local_app_workloads_if_imported

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

  echo "Waiting for backend worker"
  if ! ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-backend-worker --timeout=600s; then
    echo "Backend worker rollout failed."
    ${KUBECTL_CMD} -n "${NAMESPACE}" get pods -l app=stock-ticker-backend-worker || true
    ${KUBECTL_CMD} -n "${NAMESPACE}" logs deployment/stock-ticker-backend-worker -c worker --tail=80 || true
    exit 1
  fi

  echo "Waiting for frontend"
  if ! ${KUBECTL_CMD} -n "${NAMESPACE}" rollout status deployment/stock-ticker-frontend --timeout=180s; then
    echo "Frontend rollout failed." >&2
    ${KUBECTL_CMD} -n "${NAMESPACE}" get pods -l app=stock-ticker-frontend || true
    ${KUBECTL_CMD} -n "${NAMESPACE}" logs deployment/stock-ticker-frontend --tail=80 || true
    exit 1
  fi

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
