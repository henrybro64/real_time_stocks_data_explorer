# First-pass TypeError fix + durable drain (#348 / #349)

## #348 Root cause (confirmed)

Live first-pass `_default_ownership` called:

```python
get_ownership_concentration(stock_id, range="3M")  # WRONG
```

The service only accepts `range_name=`. Every TDCC fetch raised `TypeError`, stored as `symbol_error:TypeError`, classified **transient**, and retried uselessly. Peer fields still collected, but the symbol was marked failed/retry.

### Fix

- `first_pass.py` + `enrichment.py`: use `range_name="3M"`.
- `TypeError` / `AttributeError` → `programming_error:…` → **permanent** (no infinite transient retries).

## #349 Durable first-pass drain

Start Research already creates a durable job but required browser batch clicks. Now:

1. Attaching the run-owned job sets `research_run.schedule_enabled=true` (opt-in for that job only).
2. `POST /api/tw-research/first-pass/drain` (token auth) runs bounded `run_batch` loops.
3. `POST /api/tw-research/research-runs/first-pass-drain` pauses/resumes drain.
4. On snapshot complete, reconcile gates at `awaiting_analyst_candidate_selection` and clears `schedule_enabled`.
5. New CronJob `stock-ticker-tw-research-first-pass-drain` ships **`suspend: true`**.
6. Technical-scan CronJob (#330) remains suspended and separate.

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
- CronJob tick: `maxBatches=1`, `batchSize=10`, curl `--max-time 150`, `activeDeadlineSeconds=180`.

## Tests

```bash
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_first_pass_typeerror_348.py \
  tests/unit/services/tw_research/test_first_pass_drain_349.py \
  tests/unit/routes/test_tw_research_first_pass_drain_349.py \
  tests/unit/infra/test_tw_research_first_pass_drain_cronjob.py -q
```

## Ops enablement (explicit approval required)

1. Deploy code with #348 fix first.
2. Put drain token + user id in `stock-ticker-backend-auth`.
3. Set `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=true` on the API.
4. Unsuspend **only** `stock-ticker-tw-research-first-pass-drain` (leave schedule CronJob suspended).
5. Start Research once → drain ticks advance the owned job until the analyst gate.
