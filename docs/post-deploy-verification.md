# Post-deploy verification (#287)

Checklist after deploying #278–#283 (and the #284–#286 outage fixes) on the home k3s cluster. Tick every item, or open a follow-up issue and link it here.

## Intended changes (not regressions)

- Market-data routes require a signed-in session (#272).
- Background jobs run in `stock-ticker-backend-worker` (health on `:8001`) (#276).
- Per-user caps and rate limits exist (#273).
- Default / placeholder `SESSION_SECRET` still refuses to start; placeholder capture tokens disable capture only (#267 / #284).
- Bootstrap passwords are create-only unless `LOCAL_AUTH_RESET_PASSWORD=true` (#286).
- Daily price history and related migrations through head have run (#269 / #264 / #275).

## Cluster

```bash
kubectl -n stock-ticker get pods
kubectl -n stock-ticker get deploy
kubectl -n stock-ticker logs deploy/stock-ticker-backend -c backend --tail=80
kubectl -n stock-ticker logs deploy/stock-ticker-backend-worker -c worker --tail=80
kubectl -n stock-ticker exec deploy/stock-ticker-backend -c backend -- \
  alembic current
curl -fsS http://127.0.0.1:8001/health   # via port-forward to the worker if needed
```

- [ ] Pods `stock-ticker-backend`, `stock-ticker-backend-worker`, `stock-ticker-frontend`, `stock-ticker-postgres`, `stock-ticker-redis` are `Running` and Ready; restart counts are not climbing.
- [ ] `stock-ticker-aws-health-heartbeat` CronJob: confirm whether any `Error` job is from the outage window or a separate AWS/credentials issue ([aws-health-monitor.md](aws-health-monitor.md)).
- [ ] Worker `/health` and logs: any `Quote Redis cache failure` has a clear category and is not ongoing.
- [ ] `alembic current` is at head; watchlist / portfolio row counts match the pre-upgrade backup after the canonical stock-id migration.

Helper (read-only). It reports pod readiness, `/health`, `/api/auth/status`, whether alembic is at the image's own head, row counts, and whether the worker log still contains `Quote Redis cache failure`. It does not sign in or exercise the UI. Run it on the cluster after this image is deployed; leave the boxes below unticked until that pass.

```bash
./scripts/verify-k3s-post-deploy.sh
```

## Accounts and data

- [ ] Existing users can sign in with their existing passwords.
- [ ] Sign out appears in the header and works.
- [ ] Watchlist, portfolio positions, assets, realized trades, alert rules, and alert history match the pre-upgrade contents.
- [ ] Portfolio history chart still shows earlier points.

## Features

- [ ] Dashboard: market snapshot tiles, market status, watchlist quotes and sparklines load.
- [ ] Stock detail for one Taiwan and one U.S. stock: quote, chart ranges, key statistics, news, Taiwan tabs.
- [ ] Rankings for both markets, and manual refresh.
- [ ] Alerts evaluate (create a rule that should fire).
- [ ] Per-user caps do not block existing watchlists/portfolios that exceed new defaults.
- [ ] After a U.S. close, quotes show closed (#262).
- [ ] With the backend stopped, the UI shows “Cannot reach the server” (not an empty dashboard) (#285).

## Outcome

- [ ] Every item is ticked, or has its own issue linked from here.
- [ ] If a forward fix is not quick, record the rollback decision to `1f39bee`, including whether that image can run against migrations `0016`–head.
