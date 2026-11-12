#!/usr/bin/env bash
# Live READ-ONLY probe for TW research / Profile endpoints (#346).
# Never starts jobs, scans, or batch POSTs. report-evidence uses first_pass_only.
#
# Usage:
#   SNAPSHOT_ID=fps_... ./scripts/bench-tw-research-endpoints.sh
#   BASE_URL=http://localhost:8000 AUTH_HEADER="Authorization: Bearer ..." \
#     SNAPSHOT_ID=fps_... STOCK_ID=TW:2330 ITERATIONS=20 \
#     ./scripts/bench-tw-research-endpoints.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AUTH_HEADER="${AUTH_HEADER:-}"
SNAPSHOT_ID="${SNAPSHOT_ID:-}"
STOCK_ID="${STOCK_ID:-TW:2330}"
ITERATIONS="${ITERATIONS:-20}"

if [[ -z "${SNAPSHOT_ID}" ]]; then
  echo "ERROR: SNAPSHOT_ID is required (fail fast)." >&2
  echo "  Export SNAPSHOT_ID from a first-pass snapshot id (see runbook)." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required for p50/p95" >&2
  exit 1
fi

auth_args=()
if [[ -n "${AUTH_HEADER}" ]]; then
  auth_args=(-H "${AUTH_HEADER}")
fi

echo "bench-tw-research-endpoints.sh — READ-ONLY live probe"
echo "BASE_URL=${BASE_URL}"
echo "SNAPSHOT_ID=${SNAPSHOT_ID}"
echo "STOCK_ID=${STOCK_ID}"
echo "ITERATIONS=${ITERATIONS}"
echo

percentile() {
  local pct="$1"
  shift
  python3 - "$pct" "$@" <<'PY'
import sys
pct = float(sys.argv[1])
vals = sorted(float(x) for x in sys.argv[2:])
if not vals:
    print(0)
    raise SystemExit
if len(vals) == 1:
    print(vals[0])
    raise SystemExit
rank = (pct / 100.0) * (len(vals) - 1)
low = int(rank)
high = min(low + 1, len(vals) - 1)
frac = rank - low
print(vals[low] * (1 - frac) + vals[high] * frac)
PY
}

probe_get() {
  local label="$1"
  local url="$2"
  local times=()
  local sizes=()
  local i
  for ((i = 1; i <= ITERATIONS; i++)); do
    # shellcheck disable=SC2086
    local out
    out="$(curl -sS -o /tmp/bench_tw_body.$$ -w '%{time_total} %{size_download}' \
      "${auth_args[@]}" "${url}")"
    local t_total size_dl
    t_total="$(echo "${out}" | awk '{print $1}')"
    size_dl="$(echo "${out}" | awk '{print $2}')"
    times+=("${t_total}")
    sizes+=("${size_dl}")
  done
  local p50 p95 mx size0
  p50="$(percentile 50 "${times[@]}")"
  p95="$(percentile 95 "${times[@]}")"
  mx="$(python3 -c "import sys; print(max(float(x) for x in sys.argv[1:]))" "${times[@]}")"
  size0="${sizes[0]}"
  printf '%s\n' "--- ${label}"
  printf '  p50=%.4fs  p95=%.4fs  max=%.4fs  size_download=%s B (%.1f KiB)\n' \
    "${p50}" "${p95}" "${mx}" "${size0}" "$(python3 -c "print(${size0}/1024)")"
}

probe_post_json() {
  local label="$1"
  local url="$2"
  local body="$3"
  local times=()
  local sizes=()
  local i
  for ((i = 1; i <= ITERATIONS; i++)); do
    local out
    out="$(curl -sS -o /tmp/bench_tw_body.$$ -w '%{time_total} %{size_download}' \
      -H 'Content-Type: application/json' \
      "${auth_args[@]}" \
      -d "${body}" \
      "${url}")"
    local t_total size_dl
    t_total="$(echo "${out}" | awk '{print $1}')"
    size_dl="$(echo "${out}" | awk '{print $2}')"
    times+=("${t_total}")
    sizes+=("${size_dl}")
  done
  local p50 p95 mx size0
  p50="$(percentile 50 "${times[@]}")"
  p95="$(percentile 95 "${times[@]}")"
  mx="$(python3 -c "import sys; print(max(float(x) for x in sys.argv[1:]))" "${times[@]}")"
  size0="${sizes[0]}"
  printf '%s\n' "--- ${label}"
  printf '  p50=%.4fs  p95=%.4fs  max=%.4fs  size_download=%s B (%.1f KiB)\n' \
    "${p50}" "${p95}" "${mx}" "${size0}" "$(python3 -c "print(${size0}/1024)")"
}

cleanup() {
  rm -f /tmp/bench_tw_body.$$
}
trap cleanup EXIT

probe_get \
  "GET /api/stocks/{id}/technical-indicators" \
  "${BASE_URL}/api/stocks/${STOCK_ID}/technical-indicators"

probe_get \
  "GET .../first-pass/snapshots/{id}/manifest" \
  "${BASE_URL}/api/tw-research/first-pass/snapshots/${SNAPSHOT_ID}/manifest"

probe_get \
  "GET .../first-pass/snapshots/{id}/chunk?chunkIndex=0" \
  "${BASE_URL}/api/tw-research/first-pass/snapshots/${SNAPSHOT_ID}/chunk?chunkIndex=0"

probe_get \
  "GET .../first-pass/snapshots/{id}/chip-evidence" \
  "${BASE_URL}/api/tw-research/first-pass/snapshots/${SNAPSHOT_ID}/chip-evidence?limit=50"

# READ-ONLY: first_pass_only body — never starts a scan/job.
BODY="$(python3 -c "import json; print(json.dumps({'firstPassSnapshotId': '''${SNAPSHOT_ID}'''}))")"
probe_post_json \
  "POST /api/tw-research/report-evidence (first_pass_only)" \
  "${BASE_URL}/api/tw-research/report-evidence" \
  "${BODY}"

echo
echo "Done. Fill docs/runbooks/tw-research-chip-evidence-perf-346.md with measured values."
