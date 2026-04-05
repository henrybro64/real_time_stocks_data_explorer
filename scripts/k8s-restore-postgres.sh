#!/usr/bin/env bash
# Restore a SQL dump produced by k8s-backup-postgres.sh into the in-cluster database.
# Run AFTER Postgres is Running and (usually) AFTER the backend init container has run
# migrations, unless your dump strategy matches an empty DB — see README.
#
# Usage:
#   ./scripts/k8s-restore-postgres.sh backup.sql [namespace]
set -euo pipefail

FILE="${1:?usage: $0 backup.sql [namespace]}"
NS="${2:-stock-ticker}"

if [[ ! -f "${FILE}" ]]; then
  echo "File not found: ${FILE}" >&2
  exit 1
fi

echo "Restoring ${FILE} into ${NS}/stock-ticker-postgres ..."
kubectl exec -i -n "${NS}" deploy/stock-ticker-postgres -- \
  sh -c 'PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" psql \
    -h 127.0.0.1 \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-stocks}" \
    -v ON_ERROR_STOP=1' < "${FILE}"

echo "Restore finished."
