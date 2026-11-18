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
SECRET_NAME="${SECRET_NAME:-stock-ticker-backend-auth}"
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

normalize_desired_suspend_annotation() {
  local raw="${1:-}"
  raw="${raw//$'\r'/}"
  if [[ -z "${raw}" || "${raw}" == "<no value>" ]]; then
    echo ""
    return 0
  fi
  echo "${raw}"
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
  local raw
  raw="$(
    run_kubectl -n "${NS}" get cronjob "${name}" \
      -o "go-template={{index .metadata.annotations \"${DESIRED_ANNOTATION}\"}}" \
      2>/dev/null || true
  )"
  normalize_desired_suspend_annotation "${raw}"
}

secret_key_present() {
  # Presence only via .data index — never prints the value.
  local key="$1"
  local marker
  marker="$(
    run_kubectl -n "${NS}" get secret "${SECRET_NAME}" \
      -o "go-template={{if index .data \"${key}\"}}present{{end}}" \
      2>/dev/null || true
  )"
  [[ "${marker}" == "present" ]]
}

list_active_first_pass_drain_jobs() {
  # Prints active Job names owned by the First-Pass drain CronJob (one per line).
  # JSON is piped on stdin (never as argv — large cluster job lists exceed
  # Linux MAX_ARG_STRLEN). rc!=0 means list/parse failure; empty + rc0 = none.
  local list_rc=0
  set +e
  run_kubectl -n "${NS}" get jobs -o json 2>/dev/null \
    | FIRST_PASS_CRON="${FIRST_PASS_CRON}" python3 -c '
import json, os, sys
cron = os.environ.get("FIRST_PASS_CRON", "")
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(2)
for item in payload.get("items") or []:
    meta = item.get("metadata") or {}
    owners = meta.get("ownerReferences") or []
    owned = any(
        own.get("kind") == "CronJob" and str(own.get("name") or "") == cron
        for own in owners
    )
    if not owned:
        continue
    status = item.get("status") or {}
    try:
        active_n = int(status.get("active") or 0)
    except (TypeError, ValueError):
        active_n = 0
    if active_n > 0:
        print(str(meta.get("name") or ""))
'
  list_rc=$?
  set -e
  return "${list_rc}"
}

report_active_drain_jobs() {
  local jobs=""
  local list_rc=0
  set +e
  jobs="$(list_active_first_pass_drain_jobs)"
  list_rc=$?
  set -e
  ACTIVE_DRAIN_JOBS_LIST_RC="${list_rc}"
  jobs="$(printf '%s\n' "${jobs}" | sed '/^$/d' || true)"
  if [[ "${list_rc}" -ne 0 ]]; then
    echo "active_first_pass_drain_jobs: unknown (could not list Jobs)"
    return 1
  fi
  if [[ -z "${jobs}" ]]; then
    echo "active_first_pass_drain_jobs: none"
    return 0
  fi
  echo "active_first_pass_drain_jobs:"
  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    echo "  - ${job}"
  done <<< "${jobs}"
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
  echo "deploy-k3s.sh preserves first-pass suspend=false across redeploy only when"
  echo "pre-apply LIVE suspend was false (not annotation alone)."
  echo "FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1 forces re-suspend. Schedule CronJob stays suspended."
  echo
  run_kubectl -n "${NS}" get cronjob "${FIRST_PASS_CRON}" "${SCHEDULE_CRON}" \
    -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend,SCHEDULE:.spec.schedule,LAST_SCHEDULE:.status.lastScheduleTime,LAST_SUCCESS:.status.lastSuccessfulTime \
    2>/dev/null || true
  echo
  # Do not abort status on list failure; verify inspects ACTIVE_DRAIN_JOBS_LIST_RC.
  report_active_drain_jobs || true
}

verify_baseline() {
  print_status
  local fp_suspend sched_suspend
  fp_suspend="$(read_suspend "${FIRST_PASS_CRON}")"
  sched_suspend="$(read_suspend "${SCHEDULE_CRON}")"
  local rc=0
  # print_status already reported jobs; flag unknown as verify failure.
  if [[ "${ACTIVE_DRAIN_JOBS_LIST_RC:-0}" -ne 0 ]]; then
    echo "FAIL: could not list First-Pass drain Jobs" >&2
    rc=1
  fi
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
    echo "NOTE: First-Pass drain CronJob is ACTIVE (operator-enabled). Redeploy preserves LIVE suspend=false."
  fi
  return "${rc}"
}

refuse_enable_unless_ready() {
  # Exit 3 with the failed check name. Never prints Secret/env values.
  local failed=""

  if ! secret_key_present "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN"; then
    failed="secret missing key TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN"
  elif ! secret_key_present "TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID"; then
    failed="secret missing key TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID"
  elif ! secret_key_present "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED"; then
    failed="secret missing key TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED"
  fi
  if [[ -n "${failed}" ]]; then
    echo "Refusing ${ACTION}: ${failed}" >&2
    exit 3
  fi

  # Presence-only backend env (same spirit as tw-first-pass-preflight.sh).
  local env_report
  if ! env_report="$(
    run_kubectl -n "${NS}" exec "deploy/${DEPLOY_NAME}" -c backend -- sh -c \
      'echo "ENABLED=${TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED:-unset}"; if [ -n "${TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID:-}" ]; then echo USER_ID=set; else echo USER_ID=unset; fi; if [ -n "${TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN:-}" ]; then echo TOKEN=set; else echo TOKEN=unset; fi'
  )"; then
    echo "Refusing ${ACTION}: backend effective env unavailable (exec failed)" >&2
    exit 3
  fi
  if ! printf '%s\n' "${env_report}" | grep -q '^ENABLED=true$'; then
    echo "Refusing ${ACTION}: backend TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED is not true" >&2
    exit 3
  fi
  if ! printf '%s\n' "${env_report}" | grep -q '^USER_ID=set$'; then
    echo "Refusing ${ACTION}: backend TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID unset" >&2
    exit 3
  fi
  if ! printf '%s\n' "${env_report}" | grep -q '^TOKEN=set$'; then
    echo "Refusing ${ACTION}: backend TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN unset" >&2
    exit 3
  fi

  local active_jobs=""
  local list_rc=0
  set +e
  active_jobs="$(list_active_first_pass_drain_jobs)"
  list_rc=$?
  set -e
  active_jobs="$(printf '%s\n' "${active_jobs}" | sed '/^$/d' || true)"
  if [[ "${list_rc}" -ne 0 ]]; then
    echo "Refusing ${ACTION}: cannot list Jobs to verify no active First-Pass drain Job" >&2
    exit 3
  fi
  if [[ -n "${active_jobs}" ]]; then
    echo "Refusing ${ACTION}: active Job owned by First-Pass CronJob still running:" >&2
    printf '%s\n' "${active_jobs}" >&2
    exit 3
  fi

  local sched_suspend
  sched_suspend="$(read_suspend "${SCHEDULE_CRON}")"
  if [[ "${sched_suspend}" != "true" && "${sched_suspend}" != "missing" ]]; then
    echo "Refusing ${ACTION}: schedule CronJob must be suspended (got ${sched_suspend})" >&2
    exit 3
  fi
}

note_inflight_after_pause() {
  local jobs=""
  local list_rc=0
  set +e
  jobs="$(list_active_first_pass_drain_jobs)"
  list_rc=$?
  set -e
  jobs="$(printf '%s\n' "${jobs}" | sed '/^$/d' || true)"
  if [[ "${list_rc}" -ne 0 ]]; then
    echo "active_first_pass_drain_jobs: unknown (could not list Jobs)"
    return 0
  fi
  if [[ -z "${jobs}" ]]; then
    return 0
  fi
  local first
  first="$(printf '%s\n' "${jobs}" | head -n 1)"
  echo "in-flight drain Job ${first} still running (≤180s); wait, then re-run verify"
}

set_first_pass_suspend() {
  local suspend_bool="$1"
  require_confirm
  if [[ "${suspend_bool}" == "false" ]]; then
    refuse_enable_unless_ready
  fi
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

Rollback verification (read-only):
  1) ./scripts/tw-first-pass-cronjob-stage-d.sh verify k3s
     — no active First-Pass drain Job (or "unknown" → fix listing before enable)
  2) Confirm lease released: reserve_expires_at null or past (preflight SQL /
     read-only psql on tw_research_first_pass_jobs for the owned job_id)
  3) Confirm committed symbol row count unchanged vs pre-rollback baseline

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
    echo "Enabled Cron ticks only. research_run.schedule_enabled must already be armed"
    echo "for unattended progress. Do not enable the schedule CronJob. Do not start Phase 2."
    ;;
  pause|suspend)
    set_first_pass_suspend true
    echo
    echo "Future First-Pass Cron ticks paused. Existing Job/lease/symbol rows kept."
    note_inflight_after_pause
    print_rollback_app_steps
    ;;
  rollback)
    set_first_pass_suspend true
    echo
    echo "CronJob re-suspended. Complete app-level rollback separately:"
    note_inflight_after_pause
    print_rollback_app_steps
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    echo "Use: status|verify|enable|pause|suspend|resume|rollback" >&2
    exit 1
    ;;
esac
