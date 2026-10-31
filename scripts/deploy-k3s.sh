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
  BACKEND_BASE_IMAGE   Docker base for backend (default: python:3.11-slim-bookworm)
  FRONTEND_BASE_IMAGE  Docker base for frontend (default: node:20-bookworm-slim)
  DOCKER_PULL_RETRIES / DOCKER_PULL_RETRY_DELAY_SEC  Pre-pull retry tuning (default: 3 / 15)
  DOCKER_PULL_MIRRORS  Comma-separated mirror hosts (default: mirror.gcr.io,docker.m.daocloud.io)
  DOCKER_PULL_SKIP_MIRRORS=1  Skip mirror fallbacks when Docker Hub works but mirrors do not
  STOCK_TICKER_HOSTNAME  Ingress host (default: stock-ticker.ai). Point LAN DNS at the node IP.
  STOCK_TICKER_PUBLIC_SCHEME
                   http or https. Default is http for stock-ticker.ai and https
                   for any other host (including stock.cloudforge64.com). The
                   Entra redirect URI uses this scheme and the Ingress host.
                   https also sets SESSION_COOKIE_SECURE=true (#299).
  FORCE_AUTH_SECRET_APPLY=1
                   Apply k8s/backend-auth-secret.yaml even when the live Secret
                   differs. Default is to skip and print differing keys (#284).
                   The preflight then validates that local file, so a broken
                   live SESSION_SECRET can be repaired by a forced apply.
                   Never use this to rotate LOCAL_AUTH_PASSWORD casually (#286).

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
- Corrupt or half-initialized data on the PVC: delete deployment + PVC, re-apply (see docs/kubernetes.md).

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
    if [[ "${cookie_secure}" == "true" ]]; then
      _set_local_secret_key "${secret_path}" SESSION_COOKIE_SECURE true
    fi
  fi
  chmod 600 "${secret_path}"
}

_set_local_secret_key() {
  SECRET_EDIT_PATH="$1" SECRET_EDIT_KEY="$2" SECRET_EDIT_VALUE="$3" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["SECRET_EDIT_PATH"])
key = os.environ["SECRET_EDIT_KEY"]
value = os.environ["SECRET_EDIT_VALUE"]
prefix = f"  {key}:"
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
found = False
out = []
for line in lines:
    if line.startswith(prefix):
        out.append(f'{prefix} "{value}"\n')
        found = True
    else:
        out.append(line)
if not found:
    raise SystemExit(f"missing {key}")
path.write_text("".join(out), encoding="utf-8")
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
      _ensure_https_session_cookie
      return 0
    fi
  fi
  ${KUBECTL_CMD} apply -n "${NAMESPACE}" -f "${secret_path}"
  _ensure_https_session_cookie
}

_ensure_https_session_cookie() {
  if [[ "$(public_scheme)" != "https" ]]; then
    return 0
  fi
  local live_cookie
  live_cookie="$(_live_secret_data_value stock-ticker-backend-auth SESSION_COOKIE_SECURE)"
  if [[ "${live_cookie}" == "true" ]]; then
    return 0
  fi
  echo "Setting SESSION_COOKIE_SECURE=true for $(public_origin) (#299)"
  ${KUBECTL_CMD} -n "${NAMESPACE}" patch secret stock-ticker-backend-auth --type merge \
    -p '{"stringData":{"SESSION_COOKIE_SECURE":"true"}}'
}

_live_configmap_value() {
  ${KUBECTL_CMD} -n "${NAMESPACE}" get configmap "$1" \
    -o "jsonpath={.data.$2}" 2>/dev/null || true
}

_write_entra_secret_file() {
  # stdin is client_id, NUL, client_secret. Nothing is printed.
  local path="$1"
  local client_id="" client_secret=""
  IFS= read -r -d '' client_id || true
  IFS= read -r -d '' client_secret || true
  ENTRA_SECRET_PATH="${path}" \
    ENTRA_PRESERVE_CLIENT_ID="${client_id}" \
    ENTRA_PRESERVE_CLIENT_SECRET="${client_secret}" \
    python3 - <<'PY'
import os
from pathlib import Path

def quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'

path = Path(os.environ["ENTRA_SECRET_PATH"])
client_id = os.environ.get("ENTRA_PRESERVE_CLIENT_ID", "")
client_secret = os.environ.get("ENTRA_PRESERVE_CLIENT_SECRET", "")
path.write_text(
    "\n".join(
        [
            "# Preserved by deploy-k3s.sh. Gitignored. Do not commit (#299).",
            "apiVersion: v1",
            "kind: Secret",
            "metadata:",
            "  name: stock-ticker-backend-entra",
            "type: Opaque",
            "stringData:",
            f"  ENTRA_CLIENT_ID: {quote(client_id)}",
            f"  ENTRA_CLIENT_SECRET: {quote(client_secret)}",
            "",
        ]
    ),
    encoding="utf-8",
)
PY
  unset ENTRA_SECRET_PATH ENTRA_PRESERVE_CLIENT_ID ENTRA_PRESERVE_CLIENT_SECRET
  unset client_id client_secret
  chmod 600 "${path}"
}

apply_backend_entra_secret() {
  # Apply before the ConfigMap. An empty ENTRA_CLIENT_ID in git would otherwise
  # replace a client id that still lives only on stock-ticker-backend-env (#299).
  local secret_path="${ROOT_DIR}/k8s/backend-entra-secret.yaml"
  if ${KUBECTL_CMD} -n "${NAMESPACE}" get secret stock-ticker-backend-entra >/dev/null 2>&1; then
    local live_entra_id
    live_entra_id="$(_live_secret_data_value stock-ticker-backend-entra ENTRA_CLIENT_ID)"
    if [[ -n "${live_entra_id}" ]]; then
      echo "Keeping existing Secret stock-ticker-backend-entra"
      return 0
    fi
  fi
  if [[ -f "${secret_path}" ]]; then
    local local_id
    local_id="$(_local_auth_secret_value "${secret_path}" ENTRA_CLIENT_ID)"
    if [[ -n "${local_id}" ]]; then
      echo "Applying ${secret_path}"
      ${KUBECTL_CMD} apply -n "${NAMESPACE}" -f "${secret_path}"
      return 0
    fi
  fi
  local live_id live_secret
  live_id="$(_live_configmap_value stock-ticker-backend-env ENTRA_CLIENT_ID)"
  live_secret="$(_live_configmap_value stock-ticker-backend-env ENTRA_CLIENT_SECRET)"
  if [[ -z "${live_id}" ]]; then
    live_id="$(_live_secret_data_value stock-ticker-backend-auth ENTRA_CLIENT_ID)"
  fi
  if [[ -z "${live_secret}" ]]; then
    live_secret="$(_live_secret_data_value stock-ticker-backend-auth ENTRA_CLIENT_SECRET)"
  fi
  if [[ -z "${live_id}" && -z "${live_secret}" ]]; then
    echo "No Entra client id on the cluster yet. Microsoft login stays off until ${secret_path} is filled (#299)."
    return 0
  fi
  echo "Preserving Entra client credentials into stock-ticker-backend-entra before ConfigMap apply (#299)"
  printf '%s\0%s' "${live_id}" "${live_secret}" | _write_entra_secret_file "${secret_path}"
  unset live_id live_secret
  ${KUBECTL_CMD} apply -n "${NAMESPACE}" -f "${secret_path}"
}

render_public_origin_manifests() {
  local dest="$1"
  rm -rf "${dest}"
  mkdir -p "${dest}"
  cp -R "${ROOT_DIR}/k8s/." "${dest}/"
  PUBLIC_RENDER_DEST="${dest}" \
    PUBLIC_RENDER_ORIGIN="$(public_origin)" \
    PUBLIC_RENDER_HOST="${STOCK_TICKER_HOSTNAME}" \
    python3 - <<'PY'
import os
import re
from pathlib import Path

dest = Path(os.environ["PUBLIC_RENDER_DEST"])
origin = os.environ["PUBLIC_RENDER_ORIGIN"]
host = os.environ["PUBLIC_RENDER_HOST"]
backend = dest / "backend.yaml"
text = backend.read_text(encoding="utf-8")
text, redirect_count = re.subn(
    r'ENTRA_REDIRECT_URI:\s*".*"',
    f'ENTRA_REDIRECT_URI: "{origin}/api/auth/callback/entra"',
    text,
    count=1,
)
text, logout_count = re.subn(
    r'ENTRA_POST_LOGOUT_REDIRECT_URI:\s*".*"',
    f'ENTRA_POST_LOGOUT_REDIRECT_URI: "{origin}/"',
    text,
    count=1,
)
if redirect_count != 1 or logout_count != 1:
    raise SystemExit("backend.yaml is missing Entra redirect settings")
backend.write_text(text, encoding="utf-8")
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

deploy_manifests() {
  echo "Applying Kubernetes manifests"
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
  echo "Entra redirect origin: $(public_origin)"
  if uses_custom_app_images "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}" || [[ -n "${IMAGE_PULL_SECRET}" ]]; then
    local overlay_dir
    overlay_dir="$(mktemp -d)"
    # Expand the path now; RETURN must not read a function-local after it goes out of scope.
    # shellcheck disable=SC2064
    trap "rm -rf '${overlay_dir}' '${rendered_k8s}'" RETURN
    write_k8s_image_overlay \
      "${overlay_dir}" \
      "${BACKEND_IMAGE}" \
      "${FRONTEND_IMAGE}" \
      "${IMAGE_PULL_SECRET}" \
      "${rendered_k8s}"
    echo "Using image overlay: backend=${BACKEND_IMAGE} frontend=${FRONTEND_IMAGE}"
    ${KUBECTL_CMD} apply -k "${overlay_dir}"
  else
    ${KUBECTL_CMD} apply -k "${rendered_k8s}"
  fi
  apply_frontend_ingress_host

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
