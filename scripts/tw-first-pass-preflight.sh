#!/usr/bin/env bash
# Read-only First-Pass executor preflight (#362).
# Never prints Secret values (TOKEN / USER_ID). Never writes Kubernetes or PostgreSQL.
#
# Usage:
#   ./scripts/tw-first-pass-preflight.sh [k3s|k3d]
# Optional:
#   NAMESPACE=stock-ticker DATABASE_URL=... ./scripts/tw-first-pass-preflight.sh k3s
set -u

MODE="${1:-k3s}"
NS="${NAMESPACE:-stock-ticker}"
SECRET_NAME="${SECRET_NAME:-stock-ticker-backend-auth}"
CRONJOB_NAME="${CRONJOB_NAME:-stock-ticker-tw-research-first-pass-drain}"
DEPLOY_NAME="${DEPLOY_NAME:-stock-ticker-backend}"
POSTGRES_DEPLOY="${POSTGRES_DEPLOY:-stock-ticker-postgres}"
KUBECTL_CMD="${KUBECTL_CMD:-}"

if [[ -z "${KUBECTL_CMD}" ]]; then
  if [[ "${MODE}" == "k3s" ]] && command -v k3s >/dev/null 2>&1; then
    KUBECTL_CMD="sudo k3s kubectl"
  elif command -v kubectl >/dev/null 2>&1; then
    KUBECTL_CMD="kubectl"
  else
    echo "Need kubectl or k3s" >&2
    exit 1
  fi
fi

# shellcheck disable=SC2086
run_kubectl() {
  ${KUBECTL_CMD} "$@"
}

echo "========== tw-first-pass-preflight (read-only) =========="
echo "namespace=${NS} mode=${MODE}"
echo

echo "=== Secret key presence (names only; values never printed) ==="
secret_json=""
if ! secret_json="$(run_kubectl -n "${NS}" get secret "${SECRET_NAME}" -o jsonpath='{.data}' 2>/dev/null)"; then
  echo "secret ${SECRET_NAME} not readable" >&2
elif [[ -z "${secret_json}" ]]; then
  echo "secret ${SECRET_NAME} not readable" >&2
else
  printf '%s' "${secret_json}" \
    | tr ',' '\n' \
    | sed 's/[{}"]//g' \
    | awk -F: 'NF{print $1}' \
    | while read -r key; do
        case "${key}" in
          TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN|TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID|TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED)
            echo "present: ${key}"
            ;;
          *)
            ;;
        esac
      done
fi

echo
echo "=== Backend effective env (set/unset from running container) ==="
# Production injects TW_RESEARCH_* via envFrom secretRef — check the live process
# env, not Deployment YAML. ENABLED is a boolean flag (safe to print its value).
# Never print TOKEN or USER_ID values.
if ! run_kubectl -n "${NS}" exec "deploy/${DEPLOY_NAME}" -c backend -- sh -c \
  'for k in TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN; do eval "v=\${$k}"; if [ -n "$v" ]; then echo "$k=set"; else echo "$k=unset"; fi; done; echo "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED value=${TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED:-unset}"'; then
  echo "backend effective env unavailable (exec failed)" >&2
fi

echo
echo "=== Backend pod image / start time ==="
run_kubectl -n "${NS}" get pods -l app=stock-ticker-backend -o jsonpath='{range .items[*]}{.metadata.name}{" image="}{.status.containerStatuses[0].imageID}{" started="}{.status.containerStatuses[0].state.running.startedAt}{"\n"}{end}' 2>/dev/null \
  || run_kubectl -n "${NS}" get pods -o wide 2>/dev/null | head -20 || true

echo
echo "=== First-Pass drain CronJob ==="
run_kubectl -n "${NS}" get cronjob "${CRONJOB_NAME}" \
  -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend,SCHEDULE:.spec.schedule,LAST_SCHEDULE:.status.lastScheduleTime,LAST_SUCCESS:.status.lastSuccessfulTime 2>/dev/null \
  || echo "cronjob ${CRONJOB_NAME} not found"

# Built-in sha256 (no extension / no writes). User ids as 12-char hex prefix only.
readonly_sql="$(cat <<'SQL'
SELECT
  left(encode(sha256(convert_to(user_id, 'UTF8')), 'hex'), 12) AS user_hash12,
  research_run_id,
  phase,
  status,
  schedule_enabled,
  first_pass_job_id,
  dimensions_json #>> '{firstPassDrain,lastTickAt}' AS last_tick_at
FROM tw_research_research_runs
ORDER BY updated_at DESC
LIMIT 20;

SELECT
  left(encode(sha256(convert_to(user_id, 'UTF8')), 'hex'), 12) AS user_hash12,
  job_id,
  status,
  jsonb_array_length(COALESCE(stock_ids_json::jsonb, '[]'::jsonb)) AS universe_size,
  reserve_expires_at
FROM tw_research_first_pass_jobs
ORDER BY updated_at DESC
LIMIT 20;

SELECT status, count(*) AS n
FROM tw_research_first_pass_symbol_results
GROUP BY status
ORDER BY 1;

SELECT schedule_enabled, count(*) AS n
FROM tw_research_research_runs
GROUP BY schedule_enabled
ORDER BY 1;

SELECT status, count(*) AS n
FROM tw_research_first_pass_jobs
GROUP BY status
ORDER BY 1;
SQL
)"

echo
echo "=== Read-only PostgreSQL summary ==="
if [[ -n "${DATABASE_URL:-}" ]]; then
  if ! printf '%s\n' "${readonly_sql}" \
    | env PGOPTIONS='-c default_transaction_read_only=on' \
      psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 -P pager=off; then
    echo "psql summary failed (DATABASE_URL)" >&2
  fi
else
  echo "DATABASE_URL unset — using in-cluster postgres (deploy/${POSTGRES_DEPLOY})"
  if ! printf '%s\n' "${readonly_sql}" \
    | run_kubectl -n "${NS}" exec -i "deploy/${POSTGRES_DEPLOY}" -- \
      env PGOPTIONS='-c default_transaction_read_only=on' \
      psql -U postgres -d stocks -P pager=off; then
    echo "psql summary failed (in-cluster)" >&2
  fi
fi

echo
echo "Done. No writes performed."
