#!/usr/bin/env bash
# From inside the stock-ticker namespace, try TCP port 5432 to hostnames that often
# map to your Mac (Docker Desktop vs Colima / Lima). Use the hostname that prints OK
# in k8s/backend-database-secret.yaml as DATABASE_URL host.
#
# Usage: ./scripts/k8s-test-host-postgres.sh [namespace]
set -euo pipefail

NS="${1:-stock-ticker}"
NAME="pg-net-test-$(date +%s)"

echo "Creating temporary pod in namespace ${NS}..."
kubectl run -n "${NS}" "${NAME}" --image=busybox:1.36 --restart=Never --command -- sleep 120

cleanup() {
  kubectl delete -n "${NS}" pod "${NAME}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl wait -n "${NS}" --for=condition=Ready "pod/${NAME}" --timeout=90s

echo ""
echo "Testing port 5432 (Postgres) from pod to your Mac..."
kubectl exec -n "${NS}" "${NAME}" -- sh -c '
  ok=""
  for h in host.lima.internal host.docker.internal; do
    printf "%s: " "$h"
    if nc -z -w 4 "$h" 5432 2>/dev/null; then
      echo "OK — use this hostname in DATABASE_URL"
      ok=1
    else
      echo "FAIL"
    fi
  done
  if [ -z "$ok" ]; then
    echo ""
    echo "Neither hostname reached port 5432. On your Mac, ensure Postgres listens on TCP"
    echo "(listen_addresses) and pg_hba.conf allows the Docker bridge. Then retry."
  fi
'
