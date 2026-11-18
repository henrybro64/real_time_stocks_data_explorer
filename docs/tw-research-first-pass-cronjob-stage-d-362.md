# First-Pass CronJob Stage D — activation preparation (#362)

**Status:** code + deploy preparation only. Do **not** enable production CronJobs,
write Secrets, restart Pods, or run live drains until a separate explicit
authorization for each 🔒 step.

Companion: `docs/tw-research-first-pass-executor-362-366.md`,
`docs/tw-first-pass-production-smoke-plan-2026-11-17.md`,
`scripts/tw-first-pass-preflight.sh`,
`scripts/tw-first-pass-cronjob-stage-d.sh`,
`scripts/deploy-k3s.sh`.

## Goal

Ship a **default-disabled** First-Pass drain CronJob configuration where:

1. Git remains `suspend: true` (safe for new clusters and accidental apply).
2. An operator-approved live `suspend: false` **survives** `./scripts/deploy-k3s.sh`
   redeploys (no silent re-suspend).
3. The unrelated Taiwan Research **schedule** CronJob stays suspended.
4. First-Pass Job ownership, lease fencing, and PostgreSQL symbol rows are never
   deleted by these helpers.
5. Enable / pause / resume / rollback / verify steps are explicit and gated.

## What Stage D does **not** do

- Does not create a second executor or always-on Worker scheduler.
- Does not unsuspend `stock-ticker-tw-research-schedule`.
- Does not write drain Secret values or print tokens / user ids.
- Does not start Research Runs, adopt jobs, or run batches.
- Does not close Issue #362.

## Default-disabled packaging

| Object | Git default | Redeploy behavior |
|--------|-------------|-------------------|
| `stock-ticker-tw-research-first-pass-drain` | `suspend: true` | When pre-apply **live** `suspend` was `false`, deploy **renders** `suspend: false` into apply manifests (preferred) and still reconciles annotation / safety patch afterward. Annotation alone never re-enables. Stale `desired=false` with live `true` → warn, leave suspended, reconcile annotation to `true`. `FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1` forces `suspend=true`. Failed `kubectl apply -k` still restores live `false` when captured. Unreadable live suspend (API/RBAC/network) **refuses deploy** before apply. |
| `stock-ticker-tw-research-schedule` | `suspend: true` | Always forced back to `true` if found enabled; unreadable schedule suspend fails the deploy |

Operator annotation (set by Stage D script / deploy restore):

`stock-ticker.ai/first-pass-drain-desired-suspend=true|false`

Empty / `<no value>` annotation reads are treated as unset.

## Procedures

### Verify (read-only) 👁

```bash
./scripts/tw-first-pass-preflight.sh k3s
./scripts/tw-first-pass-cronjob-stage-d.sh verify k3s
```

Expect: schedule CronJob `suspend=true`; First-Pass CronJob present; report
whether First-Pass is suspended or operator-active; list any active drain Jobs.

**Activation readiness (authoritative run):** preflight prints a read-only
`readiness_hint` join of Research Run ↔ attached First-Pass Job. Before enable,
confirm the **intended** run shows `review_as_eligible` (or equivalent):  
`schedule_enabled=true`, `phase=phase1_first_pass`, attached `first_pass_job_id`
present, job not `failed`/`archived`/`orphaned`, no `deletion_state`. Do not
bypass app auth or write PostgreSQL for this check.

### Enable (separate approval) 🔒

`enable` / `resume` refuse (exit 3) unless:

1. Secret `stock-ticker-backend-auth` has keys (names only):  
   `TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN`, `TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID`,
   `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED`
2. Backend process env: `ENABLED=true`, `USER_ID` set, `TOKEN` set (presence only)
3. No active Job owned by the First-Pass drain CronJob
4. Schedule CronJob suspended or absent (unreadable schedule state refuses enable)
5. `CONFIRM=YES`

```bash
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh enable k3s
```

Cron ticks alone are not enough: the authoritative managed Research Run must
already be armed (`schedule_enabled=true`) with an eligible attached First-Pass
Job (verified via preflight).

### Pause future ticks (keep progress) 🔒

```bash
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh pause k3s
```

If an in-flight drain Job remains, the script notes  
`in-flight drain Job <name> still running (≤180s); wait, then re-run verify`.

Also pause app scheduling when stopping claims:

```text
POST /api/tw-research/research-runs/first-pass-drain  {"enabled":false}
```

Existing Job / lease / symbol rows remain.

### Resume 🔒

```bash
# Re-arm app scheduling for the current phase, then:
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh resume k3s
```

### Rollback 🔒

```bash
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh rollback k3s
# Then POST first-pass-drain enabled=false
# Optional: TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=false + backend restart
```

To force git-disabled First-Pass CronJob on the next deploy (even if live was
active):

```bash
FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1 ./scripts/deploy-k3s.sh k3s
```

### Redeploy after activation

Normal `./scripts/deploy-k3s.sh k3s` **preserves** First-Pass `suspend=false`
only when the **live** pre-apply suspend was `false` (plain `kubectl patch`
enable without annotation still works). Preferred path: rewrite the rendered
CronJob manifest to `suspend: false` **before** `kubectl apply -k`, then
reconcile annotation (safety patch if still suspended). It does **not** enable
the schedule CronJob. If live suspend cannot be read, deploy aborts before apply.

### Observe first unattended ticks 👁

`lastSuccessfulTime` alone is **not** progress: token-unset skips and
`idle_no_work` ticks exit 0 and still count as CronJob success.

Confirm DB / UI progress advancing from the verified baseline (e.g. 3/73) using
read-only SQL / preflight — cursor, `lastTickAt`, and `lastDataProgressAt` must
move. Enabling may start a Job almost immediately (`startingDeadlineSeconds:
300`).

**Pace:** smoke measured ~16.7 s/symbol. The 60 s budget is checked **before**
each symbol starts, so expect ~4 symbols per tick ≈ **18 ticks (~90 min)** for
the remaining 70 under Forbid — more with retries / partials.

**Stop → rollback** if: two ticks with no new symbol rows while pending>0;
`programming_error`; failed >20%; lease or DB errors.

**Done** when phase = `awaiting_analyst_candidate_selection`. The First-Pass
drain stops at that gate. Once `candidateSelection` is approved and a technical
scan is attached, the **same** CronJob runs Phase-2 technical batches. Pause the
CronJob at the gate (`pause` / `rollback`) if Phase-2 should not start
unattended.

**Rollback verification:** `verify` shows no active drain Job; lease released
(`reserve_expires_at` null or past via read-only SQL); committed symbol row
count unchanged vs the pre-rollback baseline.

## Current verified production baseline (operator notes)

- Original Research Run and Job remain authoritative.
- First-Pass collection progress persists in PostgreSQL / UI (3/73 verified).
- Backend drain settings may already be configured.
- Both Taiwan Research CronJobs remain suspended until Stage D enable is
  separately approved.

## Acceptance for this preparation PR

- [x] Git First-Pass CronJob stays `suspend: true`
- [x] deploy-k3s preserves live unsuspend only; force flag re-disables
- [x] Schedule CronJob stays / is forced suspended
- [x] Stage D script covers status/verify/enable/pause/resume/rollback with `CONFIRM=YES`
- [x] Focused unit tests for initial / redeploy / enabled / disabled / rollback /
  annotation-stale / apply-fail / enable prechecks
- [ ] Production enable remains a later, separate operator authorization
