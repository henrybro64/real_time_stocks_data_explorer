#!/usr/bin/env bash
# Read-only First-Pass executor preflight (#362).
# Never prints Secret values. Never writes Kubernetes or PostgreSQL.
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

echo "========== tw-first-pass-preflight (read-only) =========="
echo "namespace=${NS} mode=${MODE}"
echo

echo "=== Secret key presence (names only; values never printed) ==="
${KUBECTL_CMD} -n "${NS}" get secret "${SECRET_NAME}" -o jsonpath='{.data}' 2>/dev/null \
  | tr ',' '\n' \
  | sed 's/[{}"]//g' \
  | awk -F: 'NF{print $1}' \
  | while read -r key; do
      case "${key}" in
        TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN|TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID|TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED)
          echo "present: ${key}"
          ;;
      esac
    done || echo "secret ${SECRET_NAME} not readable"

echo
echo "=== Backend deployment env flags (set/unset only) ==="
for key in TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN; do
  if ${KUBECTL_CMD} -n "${NS}" get deploy "${DEPLOY_NAME}" -o yaml 2>/dev/null | grep -q "name: ${key}"; then
    echo "${key}=set"
  else
    echo "${key}=unset"
  fi
done

echo
echo "=== Backend pod image / start time ==="
${KUBECTL_CMD} -n "${NS}" get pods -l app=stock-ticker-backend -o jsonpath='{range .items[*]}{.metadata.name}{" image="}{.status.containerStatuses[0].imageID}{" started="}{.status.containerStatuses[0].state.running.startedAt}{"\n"}{end}' 2>/dev/null \
  || ${KUBECTL_CMD} -n "${NS}" get pods -o wide 2>/dev/null | head -20 || true

echo
echo "=== First-Pass drain CronJob ==="
${KUBECTL_CMD} -n "${NS}" get cronjob "${CRONJOB_NAME}" \
  -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend,SCHEDULE:.spec.schedule,LAST_SCHEDULE:.status.lastScheduleTime,LAST_SUCCESS:.status.lastSuccessfulTime 2>/dev/null \
  || echo "cronjob ${CRONJOB_NAME} not found"

echo
echo "=== Optional read-only PostgreSQL summary ==="
if [[ -n "${DATABASE_URL:-}" ]]; then
  PGOPTIONS='-c default_transaction_read_only=on' psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 <<'SQL' || echo "psql summary failed"
SELECT count(*) AS research_runs FROM tw_research_research_runs;
SELECT count(*) AS first_pass_jobs FROM tw_research_first_pass_jobs;
SELECT status, count(*) FROM tw_research_first_pass_jobs GROUP BY status ORDER BY 1;
SELECT schedule_enabled, count(*) FROM tw_research_research_runs GROUP BY schedule_enabled;
SELECT status, count(*) FROM tw_research_first_pass_symbol_results GROUP BY status ORDER BY 1;
SQL
else
  echo "DATABASE_URL unset — skipping psql"
fi

echo
echo "Done. No writes performed."
