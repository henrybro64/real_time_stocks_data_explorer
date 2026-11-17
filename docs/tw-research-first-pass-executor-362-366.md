# First-Pass executor readiness (#362) and Start re-arm (#366)

## What the code does now

- Managed `POST /tw-research/research-runs/start` re-arms an eligible Phase-1 run that already owns a First-Pass Job when the executor becomes available (`schedule_enabled=true` on the **same** run/job).
- Job re-arm is fail-closed: missing/foreign jobs, store errors, `deletion_state` set, archived, or non-eligible status block re-arm.
- `first_pass_drain_availability` requires: flag enabled, matching owner user id, **and** a configured drain token (value never logged).
- UI keeps the ordinary Start/Resume clickable when a job exists but drain is not armed, with truthful executor hints.
- `POST /api/tw-research/first-pass/drain` authenticates with header
  `X-Tw-Research-First-Pass-Drain-Token` (same as the CronJob / route `Header`).
- `dryRun=true` returns a read-only preflight (availability booleans, run/job ids,
  counts, lease flag, batch size, last tick). It does not lease or call providers.
- k3s CronJob remains `suspend: true` in git; activation is operator-only.
- Helper: `scripts/tw-first-pass-preflight.sh` (read-only kubectl + optional read-only psql).

## Timing hierarchy (unchanged)

| Layer | Budget |
|---|---|
| Soft per-symbol deadline | 45s |
| Batch wall-clock | 60s |
| CronJob curl `--max-time` | 150s |
| Job `activeDeadlineSeconds` | 180s |
| Batch lease | 300s |

`concurrencyPolicy: Forbid` → at most one drain Job; expected throughput ≤ batch size per 5-minute tick until measured otherwise.

Until the first successful tick, UI execution state is `executor_not_ticking` (authorized but not collecting).

## Recommended Secret layout

Put all three keys in Secret `stock-ticker-backend-auth`:

- `TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN`
- `TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID`
- `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED`

Activation is **not** a full deploy: patch the Secret, then `kubectl rollout restart deploy/stock-ticker-backend` so pods reload env.

## Safe rollout order (separate explicit approval required)

**Do not run production Secret edits, restarts, live drains, or CronJob unsuspend
without a separate operator authorization message.** Read-only preflight is allowed.

1. **Read-only preflight** on the Ubuntu k3s node:
   ```bash
   sudo bash scripts/tw-first-pass-preflight.sh k3s
   ```
   Confirm CronJob `suspend=true`, backend env keys unset/set (names only), and
   the intended Research Run / First-Pass Job ids (for example the managed
   `rr_…` / `fpj_rr_…` pair). A Backend Worker Pod in `Running` is **not** the
   First-Pass executor.
2. Verify PostgreSQL migration / backup readiness and capacity (CPU, memory,
   provider budgets). Never print or commit Secret values.
3. After explicit approval, configure Secret `stock-ticker-backend-auth` keys
   (`TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED`, `_USER_ID`, `_TOKEN`) — presence only
   in logs.
4. Restart Backend **only if required** to reload env:
   ```bash
   sudo k3s kubectl -n stock-ticker rollout restart deploy/stock-ticker-backend
   sudo k3s kubectl -n stock-ticker rollout status deploy/stock-ticker-backend
   ```
5. Re-run read-only preflight; confirm effective env shows ENABLED/USER_ID/TOKEN
   as `set` (values still never printed).
6. On `/research/tw`, Start/Resume the **authoritative** managed run so
   `schedule_enabled=true` on that run (do not create/adopt a different 73-stock
   job unexpectedly). UI must distinguish new Job created vs existing Job resumed.
7. Authenticated **dry-run** from **inside** the backend pod (token stays in the
   container env — never paste it onto your shell command line). Port/path match
   `k8s/backend.yaml` (`containerPort: 8000`) and the CronJob URL
   `http://stock-ticker-backend:8000/api/tw-research/first-pass/drain`:
   ```bash
   sudo k3s kubectl -n stock-ticker exec -i deploy/stock-ticker-backend -c backend -- python - <<'PY'
   import json, os, urllib.error, urllib.request
   req = urllib.request.Request(
       "http://127.0.0.1:8000/api/tw-research/first-pass/drain",
       data=json.dumps({"dryRun": True}).encode(),
       method="POST",
       headers={
           "Content-Type": "application/json",
           "X-Tw-Research-First-Pass-Drain-Token": os.environ[
               "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN"
           ],
       },
   )
   try:
       print(urllib.request.urlopen(req, timeout=150).read().decode())
   except urllib.error.HTTPError as e:
       print("HTTP", e.code, e.read().decode())
       raise SystemExit(1)
   PY
   ```
8. **One** authorized 3-stock smoke (still with CronJob **suspended** so there is
   no concurrent Cron drain). Confirm suspend first:
   ```bash
   sudo k3s kubectl -n stock-ticker get cronjob stock-ticker-tw-research-first-pass-drain \
     -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend
   ```
   Then the same in-pod call with a live body (replace `<authoritative-fpj-id>`):
   ```bash
   sudo k3s kubectl -n stock-ticker exec -i deploy/stock-ticker-backend -c backend -- python - <<'PY'
   import json, os, urllib.error, urllib.request
   body = {
       "dryRun": False,
       "maxBatches": 1,
       "batchSize": 3,
       "timeBudgetSeconds": 60,
       "jobId": "<authoritative-fpj-id>",
   }
   req = urllib.request.Request(
       "http://127.0.0.1:8000/api/tw-research/first-pass/drain",
       data=json.dumps(body).encode(),
       method="POST",
       headers={
           "Content-Type": "application/json",
           "X-Tw-Research-First-Pass-Drain-Token": os.environ[
               "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN"
           ],
       },
   )
   try:
       print(urllib.request.urlopen(req, timeout=150).read().decode())
   except urllib.error.HTTPError as e:
       print("HTTP", e.code, e.read().decode())
       raise SystemExit(1)
   PY
   ```
   Verify with read-only SQL (≤3 new symbol rows, statuses, provenance, and run
   `lastTickAt` / dimensions). Example:
   ```bash
   sudo k3s kubectl -n stock-ticker exec deploy/stock-ticker-postgres -c postgres -- \
     env PGOPTIONS='-c default_transaction_read_only=on' \
     psql -U postgres -d stocks -v ON_ERROR_STOP=1 -c "
   SELECT job_id, status, COUNT(*) AS symbol_rows
   FROM tw_research_first_pass_symbol_results
   WHERE job_id = '<authoritative-fpj-id>'
   GROUP BY job_id, status;
   "
   ```
9. After smoke evidence review + explicit approval, unsuspend **only**
   `stock-ticker-tw-research-first-pass-drain` (`*/5 * * * *`, `Forbid`).
10. Confirm first successful tick, batch throughput, remaining count, and
    unattended progress to the **analyst candidate selection** gate.
    Do **not** auto-start Phase 2. Do not enable unrelated research CronJobs.

Until CronJob ticks exist, UI must show a nontechnical message such as
「研究尚未開始：背景處理服務尚未啟用」— never “actively collecting” merely
because Research Run `status=running` with 0 symbols.

## Read-only timestamp investigation (D1 / D2)

Compare Research Run birth/update times with the attached First-Pass Job without
printing full user ids (12-char sha256 prefix only):

```bash
sudo k3s kubectl -n stock-ticker exec -i deploy/stock-ticker-postgres -c postgres -- \
  env PGOPTIONS='-c default_transaction_read_only=on' \
  psql -U postgres -d stocks -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  left(encode(sha256(convert_to(r.user_id, 'UTF8')), 'hex'), 12) AS user_hash12,
  r.research_run_id,
  r.status AS run_status,
  r.created_at AS run_created_at,
  r.created_at AT TIME ZONE 'Asia/Taipei' AS run_created_taipei,
  r.updated_at AS run_updated_at,
  r.updated_at AT TIME ZONE 'Asia/Taipei' AS run_updated_taipei,
  r.first_pass_job_id,
  j.job_id,
  j.status AS job_status,
  j.created_at AS job_created_at,
  j.created_at AT TIME ZONE 'Asia/Taipei' AS job_created_taipei,
  j.updated_at AS job_updated_at,
  j.updated_at AT TIME ZONE 'Asia/Taipei' AS job_updated_taipei
FROM tw_research_research_runs r
LEFT JOIN tw_research_first_pass_jobs j
  ON j.job_id = r.first_pass_job_id
ORDER BY r.updated_at DESC
LIMIT 10;
SQL
```

## Rollback

1. Set CronJob `suspend: true`.
2. `POST /tw-research/research-runs/first-pass-drain` with `enabled=false`.
   This clears Phase-1 `schedule_enabled` **and** Phase-2 `dimensions.phase2Drain.enabled`
   so neither managed drain path claims new work.
3. Optionally set `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=false` in Secret + restart backend.
4. Keep Research Run / First-Pass Job / technical scan / symbol rows.
5. To resume later: set Secret/flag back if needed, then
   `POST /tw-research/research-runs/first-pass-drain` with `enabled=true`
   (re-arms the current phase only — Phase-1 needs an attached job; Phase-2 needs
   an approved run-owned technical scan).

## Historical standalone job

`fpj_8a41bc24cac84d9bb1fff806` must not be auto-adopted. Adoption remains an explicit user action that preserves committed rows.

## Performance note

#360 already added caches, store-first margin, and request-budget logging. This work does not claim a measured 73-stock wall-clock. Expected behavior: one drain tick processes at most one leased batch under Forbid concurrency; no second scheduler.
