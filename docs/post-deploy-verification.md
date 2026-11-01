# Post-deploy verification (#287)

Checklist after deploying #278–#302 on the home k3s cluster. Tick every item, or open a follow-up issue and link it here.

**Target:** `main` at `589878f` or later. Read-only helper: `scripts/verify-k3s-post-deploy.sh`.

Related: #284, #285, #286, #288, #293, #301, #303 (deploy restarts app pods after a local image import), #304 (login Create account button disabled in the UI), #308 (Postgres AppArmor workaround and startup checks, see [postgres-apparmor.md](postgres-apparmor.md)).

## What is included

| Area | PRs | Issues fixed |
| --- | --- | --- |
| Review fixes | #278–#283 | #262–#276, #240 |
| Outage fixes | #288 | #284, #285, #286 |
| Worker and startup resilience, data-source stance | #294 | #277, #289, #290 |
| Leader-locked job retries, Redis persistence | #295 | #291, #292, #293 |
| Entra token exchange, first sign-in | #300, #302 | #299, #301 |
| Dependency and CI | #296, #297, #298 | — |

No migrations have been added since `0020`, so the schema head is still `20261011_0020_canonicalize_user_stock_ids`.

## Intended changes (not regressions)

- Market-data routes require a signed-in session (#272).
- Background jobs run in a separate worker pod, `stock-ticker-backend-worker`, with health on port 8001 (#276).
- The worker's liveness probe no longer restarts it just because a job is behind; only dead or stuck jobs count (#289).
- Per-user caps and rate limits exist (#273).
- A placeholder `SESSION_SECRET` still stops startup. A placeholder capture token only disables capture, which returns 503 (#267, #284).
- The bootstrap local account is create-only. Its password is reset only when `LOCAL_AUTH_RESET_PASSWORD=true` (#286).
- When the backend cannot be reached, the UI shows "Cannot reach the server" (#285).
- Redis has persistence and an explicit `maxmemory` (#293).
- Microsoft Entra External ID is available as a sign-in option (#240).
- Daily price history is stored in Postgres, and migrations `0016`–`0020` have run (#269, #264, #275).

## Cluster

Images are tagged `:local` and carry no commit label. `deploy-k3s.sh` restarts backend, worker, and frontend after a local image import so the new code is what runs (#303). Confirm image IDs from the helper (or inspect the running code):

```bash
./scripts/verify-k3s-post-deploy.sh
```

The helper is read-only. It checks pods, `/health`, worker `/livez`, alembic head, row counts (including `oauth_identities`), Redis PVC/policy/evictions, and recent `Quote Redis cache failure` lines. It does **not** prove the running image is `589878f`, sign in, or exercise the UI.

- [ ] Backend, worker **and frontend** pods run code from `589878f` or later. After `deploy-k3s.sh`, confirm image IDs changed or inspect the running code (#303).
- [ ] `./scripts/verify-k3s-post-deploy.sh` passes. Paste the output on issue #287.
- [ ] `kubectl -n stock-ticker get pods`: backend, worker, frontend, postgres and redis are all `Running` and ready, and restart counts are not climbing.
- [ ] `stock-ticker-aws-health-heartbeat` CronJob: it was in `Error` during the 10-11 outage. Confirm that recent runs succeed. If they don't, open a separate issue (see [aws-health-monitor.md](aws-health-monitor.md)).
- [ ] `alembic current` in the backend pod is at head (`0020`). Watchlist and portfolio row counts match the pre-upgrade backup after the canonical stock-id migration.

## Confirm earlier fixes hold

These items were open questions when this issue was filed. They now have fixes, so this step only confirms them on the cluster.

- [ ] No ongoing `Quote Redis cache failure` in the worker log or worker `/health` (#293). PVC `stock-ticker-redis-data` is bound, and the quote cache survives a Redis pod restart. Redis runs `maxmemory 128mb` with `noeviction`, so the script's zero-evictions check matters.
- [ ] After a worker restart, quotes refresh within seconds, not after a full poll interval (#291, #292).
- [ ] A brief Postgres restart does not crash-loop the API (#290) or make the worker restart (#289).
- [ ] After a node or k3s restart, Postgres starts without `initdb`, keeps `appArmorProfile: Unconfined`, and the backend `migrate-db` logs `db.wait.ready` (#308). Steps and rollback: [postgres-apparmor.md](postgres-apparmor.md).

## Accounts and data

> Entra sign-in creates a **separate** user. Nothing links it to an existing local account, so a Microsoft login starts with an empty watchlist and portfolio. Run the data checks below while signed in with the existing **local** account.

- [ ] Existing users can sign in with their existing passwords.
- [x] **Entra:** a first-time Microsoft sign-in creates the user and lands signed in (#301, verified 2026-10-31). Signing in again with the same account reuses that user (**repeat reuse still unverified**).
- [ ] Sign out appears in the header and works, for both local and Entra sessions. `/api/auth/logout` clears only the app session. Microsoft's own session stays signed in, so the next "Sign in with Microsoft" may complete without a password prompt. That is expected.
- [ ] Watchlist, portfolio positions, assets, realized trades, alert rules and alert history all have the same contents as before.
- [ ] The portfolio history chart still shows earlier points, and new points keep being added. The worker's sampler records them every 5 minutes; the `stock-ticker-history-capture` CronJob is suspended on purpose (#275).

## Features

- [ ] Dashboard: market snapshot tiles, market status, watchlist quotes and sparklines load.
- [ ] Stock detail for one Taiwan and one U.S. stock: quote, each chart range, key statistics, news, and the Taiwan tabs.
- [ ] Rankings for both markets, and the manual refresh.
- [ ] Alerts are evaluated (create a rule that should fire).
- [ ] Per-user caps do not block existing watchlists or portfolios, which may already be larger than the new defaults.
- [ ] After a U.S. close, quotes show "closed" (#262).
- [ ] With the backend stopped, the UI shows "Cannot reach the server" rather than an empty dashboard (#285).

## Outcome

- [ ] Every item is ticked, or has its own issue linked here.
- [ ] Rollback: going back to `1f39bee` would now discard every change listed above, and that image has not been tested against schema `0020`. The default is to fix forward. If a rollback is still considered, record the decision here and include the schema check.
