#!/usr/bin/env bash
# Shared image-name helpers for GHCR publishing and optional k3s pulls (#214).
# Safe to source from deploy-k3s.sh or invoke as:
#   ./scripts/lib/k8s_images.sh write-overlay <outdir>

DEFAULT_BACKEND_IMAGE="${DEFAULT_BACKEND_IMAGE:-stock-ticker-backend:local}"
DEFAULT_FRONTEND_IMAGE="${DEFAULT_FRONTEND_IMAGE:-stock-ticker-frontend:local}"
GHCR_REGISTRY="${GHCR_REGISTRY:-ghcr.io}"

# Owner/repo default to this project's GitHub coordinates. CI overrides via env.
GHCR_OWNER="${GHCR_OWNER:-henrybro64}"
GHCR_REPOSITORY_NAME="${GHCR_REPOSITORY_NAME:-real_time_stocks_data_explorer}"

k8s_images_lowercase() {
  local value="${1:-}"
  printf '%s' "${value}" | tr '[:upper:]' '[:lower:]'
  return 0
}

ghcr_owner() {
  k8s_images_lowercase "${GHCR_OWNER}"
  return 0
}

ghcr_repository_name() {
  k8s_images_lowercase "${GHCR_REPOSITORY_NAME}"
  return 0
}

ghcr_backend_image_name() {
  printf '%s/%s/%s-backend' "$(k8s_images_lowercase "${GHCR_REGISTRY}")" "$(ghcr_owner)" "$(ghcr_repository_name)"
  return 0
}

ghcr_frontend_image_name() {
  printf '%s/%s/%s-frontend' "$(k8s_images_lowercase "${GHCR_REGISTRY}")" "$(ghcr_owner)" "$(ghcr_repository_name)"
  return 0
}

# True when the reference includes a registry host or namespaced path (e.g. ghcr.io/a/b:tag).
is_registry_image() {
  local ref="${1:-}"
  if [[ "${ref}" == */* ]]; then
    return 0
  fi
  return 1
}

# Strip @sha256:... (or other @digest) from a pull spec.
image_ref_without_digest() {
  local ref="${1:-}"
  printf '%s' "${ref%%@*}"
  return 0
}

image_digest_part() {
  local ref="${1:-}"
  if [[ "${ref}" == *"@"* ]]; then
    printf '%s' "${ref#*@}"
  fi
  return 0
}

# Tag is the last colon after the last slash, ignoring a digest suffix.
image_has_explicit_tag() {
  local without_digest after_slash
  without_digest="$(image_ref_without_digest "${1:-}")"
  after_slash="${without_digest##*/}"
  if [[ "${after_slash}" == *":"* ]]; then
    return 0
  fi
  return 1
}

image_name_part() {
  local without_digest
  without_digest="$(image_ref_without_digest "${1:-}")"
  if image_has_explicit_tag "${1:-}"; then
    printf '%s' "${without_digest%:*}"
  else
    printf '%s' "${without_digest}"
  fi
  return 0
}

image_tag_part() {
  local without_digest
  without_digest="$(image_ref_without_digest "${1:-}")"
  if image_has_explicit_tag "${1:-}"; then
    printf '%s' "${without_digest##*:}"
  else
    printf '%s' "latest"
  fi
  return 0
}

image_pull_policy_for() {
  local ref="${1:-}"
  if ! is_registry_image "${ref}"; then
    printf '%s' "Never"
    return 0
  fi
  if [[ -n "$(image_digest_part "${ref}")" ]]; then
    printf '%s' "IfNotPresent"
    return 0
  fi
  if [[ "$(image_tag_part "${ref}")" == "latest" ]]; then
    printf '%s' "Always"
  else
    printf '%s' "IfNotPresent"
  fi
  return 0
}

kustomize_image_transform() {
  local resource_name="${1:?resource name required}"
  local image_ref="${2:?image ref required}"
  local name digest
  name="$(image_name_part "${image_ref}")"
  digest="$(image_digest_part "${image_ref}")"
  printf '  - name: %s\n' "${resource_name}"
  printf '    newName: %s\n' "${name}"
  if [[ -n "${digest}" ]]; then
    if image_has_explicit_tag "${image_ref}"; then
      printf '    newTag: "%s"\n' "$(image_tag_part "${image_ref}")"
    fi
    printf '    digest: %s\n' "${digest}"
  else
    printf '    newTag: "%s"\n' "$(image_tag_part "${image_ref}")"
  fi
  return 0
}

uses_custom_app_images() {
  local backend="${1:-${DEFAULT_BACKEND_IMAGE}}"
  local frontend="${2:-${DEFAULT_FRONTEND_IMAGE}}"
  if [[ "${backend}" != "${DEFAULT_BACKEND_IMAGE}" || "${frontend}" != "${DEFAULT_FRONTEND_IMAGE}" ]]; then
    return 0
  fi
  return 1
}

# Write a kustomize overlay that retargets app images and pull policy.
# Args: outdir backend_image frontend_image [image_pull_secret]
write_k8s_image_overlay() {
  local outdir="${1:?outdir required}"
  local backend_image="${2:?backend image required}"
  local frontend_image="${3:?frontend image required}"
  local pull_secret="${4:-}"
  local resources_dir="${5:-}"

  mkdir -p "${outdir}"
  local backend_policy frontend_policy backend_transform frontend_transform
  backend_policy="$(image_pull_policy_for "${backend_image}")"
  frontend_policy="$(image_pull_policy_for "${frontend_image}")"
  backend_transform="$(kustomize_image_transform stock-ticker-backend "${backend_image}")"
  frontend_transform="$(kustomize_image_transform stock-ticker-frontend "${frontend_image}")"

  if [[ -z "${resources_dir}" ]]; then
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    resources_dir="${here}/k8s"
  fi

  # Copy the base tree into the overlay so kustomize load-restrictions stay satisfied.
  rm -rf "${outdir}/k8s"
  mkdir -p "${outdir}"
  cp -R "${resources_dir}" "${outdir}/k8s"

  local secret_patch=""
  local cronjob_secret_patch=""
  if [[ -n "${pull_secret}" ]]; then
    secret_patch=$(
      cat <<EOF
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value:
          - name: ${pull_secret}
EOF
    )
    cronjob_secret_patch=$(
      cat <<EOF
      - op: add
        path: /spec/jobTemplate/spec/template/spec/imagePullSecrets
        value:
          - name: ${pull_secret}
EOF
    )
  fi

  cat >"${outdir}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - k8s
images:
${backend_transform}
${frontend_transform}
patches:
  - target:
      kind: Deployment
      name: stock-ticker-backend
    patch: |-
      - op: replace
        path: /spec/template/spec/initContainers/0/imagePullPolicy
        value: ${backend_policy}
      - op: replace
        path: /spec/template/spec/containers/0/imagePullPolicy
        value: ${backend_policy}
${secret_patch}
  - target:
      kind: Deployment
      name: stock-ticker-frontend
    patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/imagePullPolicy
        value: ${frontend_policy}
${secret_patch}
  - target:
      kind: Deployment
      name: stock-ticker-backend-worker
    patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/imagePullPolicy
        value: ${backend_policy}
${secret_patch}
  - target:
      kind: CronJob
      name: stock-ticker-aws-health-heartbeat
    patch: |-
      - op: replace
        path: /spec/jobTemplate/spec/template/spec/containers/0/imagePullPolicy
        value: ${backend_policy}
${cronjob_secret_patch}
EOF
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  cmd="${1:-}"
  case "${cmd}" in
    backend-name)
      ghcr_backend_image_name
      echo
      ;;
    frontend-name)
      ghcr_frontend_image_name
      echo
      ;;
    write-overlay)
      write_k8s_image_overlay "${2:?outdir}" "${3:?backend}" "${4:?frontend}" "${5:-}"
      ;;
    *)
      echo "Usage: $0 backend-name|frontend-name|write-overlay <outdir> <backend> <frontend> [pull-secret]" >&2
      exit 1
      ;;
  esac
fi
