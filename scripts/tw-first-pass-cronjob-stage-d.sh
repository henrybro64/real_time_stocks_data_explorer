#!/usr/bin/env bash
# Stage D operator helpers for First-Pass drain CronJob activation (#362).
#
# Default-disabled. Never prints Secret values. Never starts research batches.
# Destructive / enabling actions require CONFIRM=YES.
#
# Usage:
#   ./scripts/tw-first-pass-cronjob-stage-d.sh status|verify [k3s|k3d]
#   CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh enable [k3s|k3d]
#   CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh pause|suspend [k3s|k3d]
#   CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh resume [k3s|k3d]
#   CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh rollback [k3s|k3d]
#
# Companion docs:
#   docs/tw-research-first-pass-cronjob-stage-d-362.md
#   docs/tw-research-first-pass-executor-362-366.md
#   scripts/tw-first-pass-preflight.sh
set -euo pipefail

ACTION="${1:-status}"
MODE="${2:-k3s}"
NS="${NAMESPACE:-stock-ticker}"
FIRST_PASS_CRON="${FIRST_PASS_DRAIN_CRONJOB_NAME:-stock-ticker-tw-research-first-pass-drain}"
SCHEDULE_CRON="${TW_RESEARCH_SCHEDULE_CRONJOB_NAME:-stock-ticker-tw-research-schedule}"
DESIRED_ANNOTATION="${FIRST_PASS_DRAIN_DESIRED_SUSPEND_ANNOTATION:-stock-ticker.ai/first-pass-drain-desired-suspend}"
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

require_confirm() {
  if [[ "${CONFIRM:-}" != "YES" ]]; then
    echo "Refusing ${ACTION}: set CONFIRM=YES after explicit operator approval." >&2
    exit 2
  fi
}

read_suspend() {
  local name="$1"
  local value
  if ! value="$(run_kubectl -n "${NS}" get cronjob "${name}" -o jsonpath='{.spec.suspend}' 2>/dev/null)"; then
    echo "missing"
    return 0
  fi
  if [[ -z "${value}" ]]; then
    echo "missing"
    return 0
  fi
  echo "${value}"
}

read_desired() {
  local name="$1"
  run_kubectl -n "${NS}" get cronjob "${name}" \
    -o "go-template={{index .metadata.annotations \"${DESIRED_ANNOTATION}\"}}" \
    2>/dev/null || true
}

print_status() {
  local fp_suspend sched_suspend desired
  fp_suspend="$(read_suspend "${FIRST_PASS_CRON}")"
  sched_suspend="$(read_suspend "${SCHEDULE_CRON}")"
  desired="$(read_desired "${FIRST_PASS_CRON}")"
  echo "========== tw-first-pass-cronjob-stage-d (#362) =========="
  echo "namespace=${NS} mode=${MODE} action=${ACTION}"
  echo "first_pass_cron=${FIRST_PASS_CRON} suspend=${fp_suspend} desired=${desired:-unset}"
  echo "schedule_cron=${SCHEDULE_CRON} suspend=${sched_suspend}"
  echo
  echo "Git manifests stay suspend:true (default-disabled)."
  echo "deploy-k3s.sh preserves first-pass suspend=false across redeploy unless"
  echo "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1. Schedule CronJob is never preserved enabled."
  echo
  run_kubectl -n "${NS}" get cronjob "${FIRST_PASS_CRON}" "${SCHEDULE_CRON}" \
    -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend,SCHEDULE:.spec.schedule,LAST_SCHEDULE:.status.lastScheduleTime,LAST_SUCCESS:.status.lastSuccessfulTime \
    2>/dev/null || true
}

verify_baseline() {
  print_status
  local fp_suspend sched_suspend
  fp_suspend="$(read_suspend "${FIRST_PASS_CRON}")"
  sched_suspend="$(read_suspend "${SCHEDULE_CRON}")"
  local rc=0
  if [[ "${fp_suspend}" == "missing" ]]; then
    echo "FAIL: First-Pass drain CronJob missing" >&2
    rc=1
  fi
  if [[ "${sched_suspend}" != "true" && "${sched_suspend}" != "missing" ]]; then
    echo "FAIL: Taiwan Research schedule CronJob must remain suspended (got ${sched_suspend})" >&2
    rc=1
  else
    echo "OK: schedule CronJob suspended or absent"
  fi
  if [[ "${fp_suspend}" == "true" ]]; then
    echo "OK: First-Pass drain CronJob currently suspended (default-disabled / paused)"
  elif [[ "${fp_suspend}" == "false" ]]; then
    echo "NOTE: First-Pass drain CronJob is ACTIVE (operator-enabled). Redeploy will preserve this."
  fi
  return "${rc}"
}

set_first_pass_suspend() {
  local suspend_bool="$1"
  require_confirm
  echo "Patching ${FIRST_PASS_CRON} spec.suspend=${suspend_bool}"
  run_kubectl -n "${NS}" patch cronjob "${FIRST_PASS_CRON}" --type merge \
    -p "{\"spec\":{\"suspend\":${suspend_bool}}}"
  run_kubectl -n "${NS}" annotate cronjob "${FIRST_PASS_CRON}" \
    "${DESIRED_ANNOTATION}=${suspend_bool}" --overwrite
  # Never touch schedule CronJob enablement here — only assert it stays suspended.
  local sched_suspend
  sched_suspend="$(read_suspend "${SCHEDULE_CRON}")"
  if [[ "${sched_suspend}" != "true" && "${sched_suspend}" != "missing" ]]; then
    echo "WARNING: forcing schedule CronJob back to suspend=true" >&2
    run_kubectl -n "${NS}" patch cronjob "${SCHEDULE_CRON}" --type merge \
      -p '{"spec":{"suspend":true}}'
  fi
  print_status
}

print_rollback_app_steps() {
  cat <<'EOF'
App-level pause (keeps CronJob state; stops claiming new drain work):
  Authenticated POST /api/tw-research/research-runs/first-pass-drain
  body: {"enabled":false}
  → clears Phase-1 schedule_enabled and Phase-2 phase2Drain.enabled.
  Does not delete Research Run / First-Pass Job / symbol rows.

Optional Secret disarm (never print values):
  Set TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=false on stock-ticker-backend-auth
  then restart backend. Prefer structure-aware local sync via deploy-k3s
  _set_local_secret_key — never commit credentials.

Resume later (separate approval):
  1) Secret ENABLED=true + owner/token present (names only in preflight)
  2) POST first-pass-drain enabled=true (re-arms current phase only)
  3) CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh resume k3s
EOF
}

case "${ACTION}" in
  status)
    print_status
    ;;
  verify)
    verify_baseline
    ;;
  enable|resume)
    set_first_pass_suspend false
    echo
    echo "Enabled Cron ticks only. Ensure drain Secret ENABLED/USER_ID/TOKEN and"
    echo "research_run.schedule_enabled before expecting unattended progress."
    echo "Do not enable the schedule CronJob. Do not start Phase 2 automatically."
    ;;
  pause|suspend)
    set_first_pass_suspend true
    echo
    echo "Future First-Pass Cron ticks paused. Existing Job/lease/symbol rows kept."
    print_rollback_app_steps
    ;;
  rollback)
    set_first_pass_suspend true
    echo
    echo "CronJob re-suspended. Complete app-level rollback separately:"
    print_rollback_app_steps
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    echo "Use: status|verify|enable|pause|suspend|resume|rollback" >&2
    exit 1
    ;;
esac
