#!/usr/bin/env bash
# Restore a SQL dump produced by k8s-backup-postgres.sh into the in-cluster database.
#
# Usage:
#   ./scripts/k8s-restore-postgres.sh backup.sql [namespace]
#
# After a fresh deploy the backend init container runs `alembic upgrade head`. Restore
# **data-only** dumps (--data-only from k8s-backup-postgres.sh) once migrations finish.
# Full schema+data dumps need an empty database (wipe PVC / first boot) or they fail on
# duplicate objects with ON_ERROR_STOP=1.
#
# k3s: KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-restore-postgres.sh backup.sql
set -euo pipefail

FILE="${1:?usage: $0 backup.sql [namespace]}"
NS="${2:-stock-ticker}"
KUBECTL_CMD="${KUBECTL_CMD:-}"

if [[ ! -f "${FILE}" ]]; then
  echo "File not found: ${FILE}" >&2
  exit 1
fi

if [[ ! -s "${FILE}" ]]; then
  echo "Error: backup file is empty: ${FILE}" >&2
  exit 1
fi

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

echo "Restoring ${FILE} into ${NS}/stock-ticker-postgres (${KUBECTL_CMD}) ..."
${KUBECTL_CMD} exec -i -n "${NS}" deploy/stock-ticker-postgres -- \
  sh -c 'PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" psql \
    -h 127.0.0.1 \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-stocks}" \
    -v ON_ERROR_STOP=1' < "${FILE}"

echo "Restore finished."
