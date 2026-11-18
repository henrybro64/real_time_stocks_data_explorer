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
| `stock-ticker-tw-research-first-pass-drain` | `suspend: true` | Preserve live `false` (or annotation `desired-suspend=false`) unless `FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1` |
| `stock-ticker-tw-research-schedule` | `suspend: true` | Always forced back to `true` if found enabled |

Operator annotation (set by Stage D script / deploy restore):

`stock-ticker.ai/first-pass-drain-desired-suspend=true|false`

## Procedures

### Verify (read-only) 👁

```bash
./scripts/tw-first-pass-preflight.sh k3s
./scripts/tw-first-pass-cronjob-stage-d.sh verify k3s
```

Expect: schedule CronJob `suspend=true`; First-Pass CronJob present; report
whether First-Pass is suspended or operator-active.

### Enable (separate approval) 🔒

Prerequisites (all required for honest “executor available”):

1. Secret keys present (names only): `TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN`,
   `TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID`, `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=true`
2. Backend process sees those env keys (`preflight` “set”)
3. Managed Research Run has `schedule_enabled=true` for the owned First-Pass Job
4. Then:

```bash
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh enable k3s
```

### Pause future ticks (keep progress) 🔒

```bash
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh pause k3s
```

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

Normal `./scripts/deploy-k3s.sh k3s` **preserves** an operator-approved First-Pass
`suspend=false`. It does **not** enable the schedule CronJob.

## Current verified production baseline (operator notes)

- Original Research Run and Job remain authoritative.
- First-Pass collection progress persists in PostgreSQL / UI.
- Backend drain settings may already be configured.
- Both Taiwan Research CronJobs remain suspended until Stage D enable is
  separately approved.

## Acceptance for this preparation PR

- [x] Git First-Pass CronJob stays `suspend: true`
- [x] deploy-k3s preserves operator-approved unsuspend; force flag re-disables
- [x] Schedule CronJob stays / is forced suspended
- [x] Stage D script covers status/verify/enable/pause/resume/rollback with `CONFIRM=YES`
- [x] Focused unit tests for initial / redeploy / enabled / disabled / rollback paths
- [ ] Production enable remains a later, separate operator authorization
