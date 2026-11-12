# First-pass TypeError fix + durable drain (#348 / #349)

## #348 Root cause (confirmed)

Live first-pass `_default_ownership` called:

```python
get_ownership_concentration(stock_id, range="3M")  # WRONG
```

The service only accepts `range_name=`. Every TDCC fetch raised `TypeError`, stored as `symbol_error:TypeError`, classified **transient**, and retried uselessly. Peer fields still collected, but the symbol was marked failed/retry.

### Fix

- `first_pass.py` + `enrichment.py`: use `range_name="3M"`.
- `TypeError` / `AttributeError` → `programming_error:…`.

### `programming_error` is distinct (not a permanent marker)

- `classify_field_reason` returns **`programming_error`** for reasons starting with `programming_error:`.
- It is **not** listed in `_PERMANENT_FIELD_REASON_MARKERS` and **not** in `_RETRYABLE_FIELD_CLASSES`.
- Field-only programming faults → symbol **`partial`** with `failureClass=programming_error` (peer fields kept).
- Symbol-level `record.error` programming faults → **`failed`**, no `nextRetryAt` (never auto-retried).
- Legacy `symbol_error:` stays **transient** so pre-fix rows retry after deploy.

### Systemic circuit breaker

When the same `(field, ExcClass)` programming_error hits **≥3 symbols and ≥50%** of finished symbols in a batch:

1. Persist the symbol rows.
2. Mark the job **`failed`** with `systemic_programming_error:<field>:<ExcClass>`.
3. Do **not** build or activate a snapshot.
4. Research-run reconcile maps the run to **`failed`**, sets `schedule_enabled=false`, and appends `first_pass_drain_disarmed_systemic_programming_error`.

## #349 Durable first-pass drain

Start Research already creates a durable job but required browser batch clicks. Now:

1. Attaching the run-owned job arms drain **only** when globally enabled **and** the user id matches `TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID`.
2. `POST /api/tw-research/first-pass/drain` (token auth) runs bounded `run_batch` loops.
3. `POST /api/tw-research/research-runs/first-pass-drain` pauses/resumes drain.
4. On snapshot complete, reconcile gates at `awaiting_analyst_candidate_selection` and clears `schedule_enabled`.
5. New CronJob `stock-ticker-tw-research-first-pass-drain` ships **`suspend: true`** with schedule `*/5 * * * *`.
6. Technical-scan CronJob (#330) remains suspended and separate.

### Per-batch budget + timing hierarchy

| Setting | Default | Role |
| --- | --- | --- |
| `timeBudgetSeconds` | **90** | Stop before starting the next symbol once elapsed ≥ budget; release unstarted ids (attempts unchanged) |
| `batchSize` | **5** | Symbols claimed per tick |
| curl `--max-time` | **150** | CronJob HTTP client ceiling |
| `activeDeadlineSeconds` | **180** | Kubernetes Job deadline |
| Batch lease | **300 s** | `BATCH_LEASE_SECONDS` |

Hierarchy: **budget 90 s < curl 150 s < lease 300 s**.

Drain and manual `POST /first-pass/jobs/{id}/batches` both default to the 90 s budget. Returned batch fields include `durationMs`, `budgetStopped`, `releasedUnstarted`, and `batchCounts` `{ok, partial, failed}`.

### Arm only when drain can run

`schedule_enabled` / `firstPassDrain.armed` is set **true** only when:

- `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=true`, and
- `user_id == TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID`.

Otherwise attach leaves it false and notes `first_pass_drain_unavailable_for_user` or `first_pass_drain_globally_disabled`. Resume as a non-owner (or while globally disabled) returns **409**.

### Config (all default off)

| Setting | Purpose |
| --- | --- |
| `TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN` | CronJob header token |
| `TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID` | Owner user id |
| `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED` | Global drain switch |

### Safety

- Caller-supplied `jobId` is accepted **only** when it equals the run's non-null authoritative `first_pass_job_id`. Resume without an attached job is refused (409).
- No Notion writes, no Phase-2 auto-start, no candidate auto-selection.
- Never auto-resumes the three historical pending jobs without Start Research attach + `schedule_enabled`.
- CronJob tick: `maxBatches=1`, `batchSize=5`, `timeBudgetSeconds=90`, curl `--max-time 150`, `activeDeadlineSeconds=180`.
- Cron schedule remains **`*/5 * * * *`** with **`suspend: true`** (do not unsuspend without explicit approval).

## Operator section

Replace `$API`, `$TOKEN`, and cookie/session auth as appropriate. Do **not** enable CronJobs from these curls.

### Pause / resume drain

```bash
# Pause
curl -sS -X POST "$API/api/tw-research/research-runs/first-pass-drain" \
  -H "Content-Type: application/json" \
  -H "Cookie: $SESSION" \
  -d '{"enabled":false}'

# Resume (requires attached authoritative job + matching drain owner + global enable)
curl -sS -X POST "$API/api/tw-research/research-runs/first-pass-drain" \
  -H "Content-Type: application/json" \
  -H "Cookie: $SESSION" \
  -d '{"enabled":true}'
```

### Dry-run drain tick

```bash
curl -sS -X POST "$API/api/tw-research/first-pass/drain" \
  -H "Content-Type: application/json" \
  -H "X-Tw-Research-First-Pass-Drain-Token: $TOKEN" \
  -d '{"dryRun":true,"maxBatches":1,"batchSize":5,"timeBudgetSeconds":90}'
```

### Reading `firstPassProgress`

After Start Research / drain ticks, `GET` the research run (or inspect the UI payload) for:

```json
{
  "scheduleEnabled": true,
  "firstPassDrain": {
    "armed": true,
    "available": true,
    "reason": null,
    "lastTickAt": "...",
    "lastOutcome": "batch_progress"
  },
  "dimensions": {
    "firstPassProgress": {
      "status": "running",
      "processed": 15,
      "total": 73,
      "okCount": 12,
      "partialCount": 2,
      "failedCount": 1,
      "nextRetryAt": null,
      "lastError": null,
      "lastErrorClass": null,
      "updatedAt": "..."
    }
  }
}
```

- `scheduleEnabled` / `firstPassDrain.armed` = first-pass drain armed (not the #330 technical schedule).
- On systemic breaker: `status=failed`, `lastError=systemic_programming_error:…`, `scheduleEnabled=false`.

## Historical pending jobs

These three durable jobs existed before the drain authorization model. **Leave them untouched** in this PR (no migration, no auto-archive script).

| Job id |
| --- |
| `fpj_rr_3c34b16ed64949799503` |
| `fpj_d5558699ae4d46aa8d27ecd2` |
| `fpj_22d0a7e0ded9446097dde418` |

### READ-ONLY: which one is `research_run.first_pass_job_id`?

```sql
-- Identify the authoritative run-owned job (if any).
SELECT user_id, research_run_id, first_pass_job_id, schedule_enabled, phase, status
FROM tw_research_research_runs
WHERE first_pass_job_id IN (
  'fpj_rr_3c34b16ed64949799503',
  'fpj_d5558699ae4d46aa8d27ecd2',
  'fpj_22d0a7e0ded9446097dde418'
);

-- Inventory the three rows without mutating them.
SELECT job_id, user_id, status, cursor, universe_size, active_snapshot_id, updated_at
FROM tw_research_first_pass_jobs
WHERE job_id IN (
  'fpj_rr_3c34b16ed64949799503',
  'fpj_d5558699ae4d46aa8d27ecd2',
  'fpj_22d0a7e0ded9446097dde418'
);
```

### Leave-untouched plan

1. Do **not** drain, archive, or DELETE these jobs from this change set.
2. Only a run whose `first_pass_job_id` is attached via Start Research **and** `schedule_enabled` can be drained.
3. Optionally, later: an explicit operator command may mark non-run-owned orphans as archived — **not** in this PR.

## Real-node batch timing

**Still unmeasured.** Fake-clock unit/integration tests cover budget stop / release behavior. Do not treat 90 s as a measured live-provider SLA until a real-node batch timing pass is recorded separately.

## Tests

```bash
cd backend && .venv/bin/python3.11 -m pytest \
  tests/unit/services/tw_research/test_first_pass_typeerror_348.py \
  tests/unit/services/tw_research/test_first_pass_drain_349.py \
  tests/unit/services/tw_research/test_first_pass_budget_circuit_348_349.py \
  tests/unit/routes/test_tw_research_first_pass_drain_349.py \
  tests/unit/infra/test_tw_research_first_pass_drain_cronjob.py -q
```

PostgreSQL integration (skipped when `DATABASE_URL` is unset):

```bash
cd backend && .venv/bin/python3.11 -m pytest \
  tests/integration/services/test_tw_research_first_pass_drain_348_349.py -q
```

## Ops enablement (explicit approval required)

1. Deploy code with #348 fix first.
2. Put drain token + user id in `stock-ticker-backend-auth`.
3. Set `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=true` on the API.
4. Unsuspend **only** `stock-ticker-tw-research-first-pass-drain` (leave technical schedule CronJob suspended). Cron remains `*/5 * * * *` with `suspend: true` until that approval.
5. Start Research once → drain ticks advance the owned job until the analyst gate.
