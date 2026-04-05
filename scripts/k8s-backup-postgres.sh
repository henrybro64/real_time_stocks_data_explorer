#!/usr/bin/env bash
# Logical backup of the in-cluster Postgres database (pg_dump).
# Run from the repo root while the cluster is up and stock-ticker-postgres is Ready.
#
# Usage:
#   ./scripts/k8s-backup-postgres.sh [namespace] [output.sql]
#
# Default output: ./stocks-YYYYMMDD-HHMMSS.sql in the current directory.
set -euo pipefail

NS="${1:-stock-ticker}"
OUT="${2:-stocks-$(date +%Y%m%d-%H%M%S).sql}"

echo "Backing up Postgres in namespace ${NS} to ${OUT} ..."
kubectl exec -n "${NS}" deploy/stock-ticker-postgres -- \
  sh -c 'PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" pg_dump \
    -h 127.0.0.1 \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-stocks}" \
    --no-owner \
    --no-acl' > "${OUT}"

echo "Done: ${OUT} ($(wc -c < "${OUT}" | tr -d " ") bytes)"
