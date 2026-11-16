# First-Pass executor readiness (#362) and Start re-arm (#366)

## What the code does now

- Managed `POST /tw-research/research-runs/start` re-arms an eligible Phase-1 run that already owns a First-Pass Job when the executor becomes available (`schedule_enabled=true` on the **same** run/job).
- Job re-arm is fail-closed: missing/foreign jobs, store errors, `deletion_state` set, archived, or non-eligible status block re-arm.
- `first_pass_drain_availability` requires: flag enabled, matching owner user id, **and** a configured drain token (value never logged).
- UI keeps the ordinary Start/Resume clickable when a job exists but drain is not armed, with truthful executor hints.
- `POST /tw-research/first-pass/drain` with `dryRun=true` returns a read-only preflight (availability booleans, run/job ids, counts, lease flag, batch size, last tick). It does not lease or call providers.
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

1. Configure Secret keys (never print values) + confirm with `scripts/tw-first-pass-preflight.sh`.
2. `kubectl rollout restart deploy/stock-ticker-backend`.
3. User Start/Resume on the managed run (re-arms `schedule_enabled=true`).
4. Preflight dry-run: `POST /api/tw-research/first-pass/drain` with `{"dryRun":true}` and drain token header.
5. One manual tick: `kubectl create job --from=cronjob/stock-ticker-tw-research-first-pass-drain ...`.
6. Verify symbol counts rise in PostgreSQL / UI.
7. Only then unsuspend the CronJob.

## Rollback

1. Set CronJob `suspend: true`.
2. `POST /tw-research/research-runs/first-pass-drain` with `enabled=false`.
3. Optionally set `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=false` in Secret + restart backend.
4. Keep Research Run / First-Pass Job / symbol rows.

## Historical standalone job

`fpj_8a41bc24cac84d9bb1fff806` must not be auto-adopted. Adoption remains an explicit user action that preserves committed rows.

## Performance note

#360 already added caches, store-first margin, and request-budget logging. This work does not claim a measured 73-stock wall-clock. Expected behavior: one drain tick processes at most one leased batch under Forbid concurrency; no second scheduler.
