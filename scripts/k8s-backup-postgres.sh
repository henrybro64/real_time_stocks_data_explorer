#!/usr/bin/env bash
# Logical backup of the in-cluster Postgres database (pg_dump).
# Run from the repo root while the cluster is up and stock-ticker-postgres is Ready.
#
# Usage:
#   ./scripts/k8s-backup-postgres.sh [namespace] [output.sql] [--data-only]
#
# Default output: ./stocks-YYYYMMDD-HHMMSS.sql in the current directory.
# Use --data-only when restoring after a fresh deploy (Alembic already migrated schema).
#
# k3s (root-only kubeconfig): KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-backup-postgres.sh
set -euo pipefail

DATA_ONLY=false
POSITIONAL=()
for arg in "$@"; do
  if [[ "${arg}" == "--data-only" ]]; then
    DATA_ONLY=true
  else
    POSITIONAL+=("${arg}")
  fi
done

NS="${POSITIONAL[0]:-stock-ticker}"
OUT="${POSITIONAL[1]:-stocks-$(date +%Y%m%d-%H%M%S).sql}"
KUBECTL_CMD="${KUBECTL_CMD:-}"

if [[ -n "${KUBECTL_CMD}" ]]; then
  :
elif command -v kubectl >/dev/null 2>&1 && kubectl get ns &>/dev/null 2>&1; then
  KUBECTL_CMD="kubectl"
elif command -v k3s >/dev/null 2>&1 && sudo k3s kubectl get ns &>/dev/null 2>&1; then
  KUBECTL_CMD="sudo k3s kubectl"
else
  echo "Error: cannot reach Kubernetes (set KUBECTL_CMD or fix kubectl / k3s access)." >&2
  exit 1
fi

if [[ "${DATA_ONLY}" == true ]]; then
  DUMP_EXTRA="--data-only"
else
  DUMP_EXTRA=""
fi

echo "Backing up Postgres in namespace ${NS} to ${OUT} (${KUBECTL_CMD}) ..."
if ! ${KUBECTL_CMD} get deploy -n "${NS}" stock-ticker-postgres &>/dev/null; then
  echo "Error: deploy/stock-ticker-postgres not found in namespace ${NS}." >&2
  exit 1
fi

${KUBECTL_CMD} exec -n "${NS}" deploy/stock-ticker-postgres -- \
  sh -c 'PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" pg_dump \
    -h 127.0.0.1 \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-stocks}" \
    --no-owner \
    --no-acl \
    '"${DUMP_EXTRA}" > "${OUT}"

if [[ ! -s "${OUT}" ]]; then
  echo "Error: backup file is empty — check ${KUBECTL_CMD} access and Postgres readiness." >&2
  rm -f "${OUT}"
  exit 1
fi

echo "Done: ${OUT} ($(wc -c < "${OUT}" | tr -d " ") bytes)"
