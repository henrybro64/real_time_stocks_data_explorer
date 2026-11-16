# First-Pass executor readiness (#362) and Start re-arm (#366)

## What the code does now

- Managed `POST /tw-research/research-runs/start` re-arms an eligible Phase-1 run that already owns a First-Pass Job when the executor becomes available (`schedule_enabled=true` on the **same** run/job).
- `first_pass_drain_availability` requires: flag enabled, matching owner user id, **and** a configured drain token (value never logged).
- UI keeps the ordinary Start/Resume clickable when a job exists but drain is not armed, with truthful executor hints.
- k3s CronJob remains `suspend: true` in git; activation is operator-only.

## Timing hierarchy (unchanged)

| Layer | Budget |
|---|---|
| Soft per-symbol deadline | 45s |
| Batch wall-clock | 60s |
| CronJob curl `--max-time` | 150s |
| Job `activeDeadlineSeconds` | 180s |
| Batch lease | 300s |

`concurrencyPolicy: Forbid` prevents overlapping drain Jobs.

## Safe rollout (do not execute without separate approval)

1. Confirm disposable/preflight checks (health, image tag, Secret **key presence** only — never print values).
2. Set backend env: `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=true`, `TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID=<owner>`, token via Secret.
3. Deploy backend so availability checks see the new config.
4. Create a **disposable** managed Start (or re-arm an existing eligible managed run with Start). Confirm `schedule_enabled=true` in PostgreSQL.
5. Unsuspend **only** `stock-ticker-tw-research-first-pass-drain` after dry-run/smoke approval.
6. Watch tick telemetry (`firstPassDrain.lastTickAt`), per-symbol rows, 429s, CPU/RSS.
7. Confirm stop at `awaiting_analyst_candidate_selection` (no Phase-2 / Notion).

## Rollback

1. Set CronJob `suspend: true` (or pause via `POST /research-runs/first-pass-drain` with `enabled=false`).
2. Optionally set `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=false`.
3. Do **not** delete Research Run / First-Pass Job rows or symbol evidence.

## Historical standalone job

`fpj_8a41bc24cac84d9bb1fff806` must not be auto-adopted. Adoption remains an explicit user action that preserves committed rows.

## Performance note

#360 already added caches, store-first margin, and request-budget logging. This work does not claim a measured 73-stock wall-clock. Expected behavior: one drain tick processes at most one leased batch under Forbid concurrency; no second scheduler.
