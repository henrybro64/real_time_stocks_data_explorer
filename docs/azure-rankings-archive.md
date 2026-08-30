# Azure SQL rankings archive (issues #165–#167)

This document covers the optional **public market ranking archive** path. It is
additive to the existing PostgreSQL-backed Stock Ticker app.

## Separation of concerns

| Store | Env var | Contents |
| --- | --- | --- |
| Local / cluster Postgres | `DATABASE_URL` | Operational app data + current rankings snapshots |
| Azure SQL Free offer | `RANKINGS_ARCHIVE_DATABASE_URL` | Public EOD ranking archives only |

Azure must never receive personal data (users, OAuth, watchlists, portfolios,
alerts, API keys, AI preferences).

Unset `RANKINGS_ARCHIVE_DATABASE_URL` and the app behaves exactly as before.
Azure outages must not block FastAPI startup or `GET /api/rankings`.

## Provisioning (#165)

See [`infra/azure-rankings-archive/README.md`](../infra/azure-rankings-archive/README.md)
for Terraform apply/destroy, Free-offer portal checks, and cost-safety review.

## Schema + repository (#166)

Tables (created lazily via SQLAlchemy `create_all` on first archive use):

- `ranking_snapshots_archive`
- `ranking_snapshot_items_archive`

Uniqueness: `(market, universe, ranking_type, trading_date)` — one EOD row per
combination. Duplicate writes keep the existing snapshot.

`RankingsArchiveRepository.to_ranking_response` / `ranking_response_from_archive`
rebuilds a `RankingResponse` in archived rank order (persists each item's
`exchange` and `valuationWarning`; stock currency/timezone still follow market).
Text columns use SQLAlchemy `Unicode` / `UnicodeText` so Taiwanese names survive
SQL Server Latin1 collations.

Archive DB connects lazily. A failed connect (including Free-offer AutoPause
cold start) uses **bounded in-request retries with backoff** (2s / 5s / 8s
between attempts; sleeps outside the process lock), then a short process
cooldown before the next initialization wave (#177). Mid-query connection
failures invalidate the cached engine so the next request re-enters cold-start.
History routes return `503` with `Retry-After: 10` so the Rankings UI can wake
the archive without blocking live `GET /api/rankings`.

EOD capture inspects a bounded window of recent settled trading days and may
**backfill a missed prior day only when the provider payload proves that date**
(item/`tradingDate` timestamps). It never stamps today's quotes under
yesterday's archive key (#178).

Code:

- `backend/app/db/archive_models.py`
- `backend/app/db/archive_session.py`
- `backend/app/repositories/rankings_archive_repository.py`

## EOD capture (#167)

Supported combinations match the Rankings UI:

- US: DJIA, NASDAQ100 × volume, valuation_low_pe, change_up, change_down
- TW: TWSE, TPEx × those four plus net_buy / net_sell

Scheduling uses market-local timezones (`America/New_York`, `Asia/Taipei`),
skips non-trading days, and waits for settle delay (US quotes +45m after close;
TW quotes +30m; TW institutional after 18:00 Taipei).

### Manual control

```bash
curl -X POST "$API/api/rankings/archive/capture" \
  -H "X-Rankings-Archive-Capture-Token: $RANKINGS_ARCHIVE_CAPTURE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"market":"TW","force":true}'
```

`force: true` skips the post-close settle-time gate so operators can exercise
the capture path in tests. It still skips non-trading dates and **never**
bypasses provider trading-date validation. Explicit `tradingDate` performs a
safe backfill when the provider response matches that date.

Hardcoded Taiwan quote fallback responses are never archived
(`allow_hardcoded_fallback=false` + `twRankingsScope=fallback` skip).

### Automatic sampler

```bash
RANKINGS_ARCHIVE_EOD_ENABLED=true
RANKINGS_ARCHIVE_EOD_POLL_SECONDS=900
```

The sampler is **market-aware** (#187): it only opens Azure SQL inside a
**same-day capture window** (settle time → settle + ~90 minutes). Outside that
window — including weekends, mid-session idle time, and weekend process
restarts — it sleeps until the next settle and makes **zero** Azure connections.
Missed archives are recovered on the next settle via the bounded backfill
window (not by continuous probing).

`RANKINGS_ARCHIVE_EOD_POLL_SECONDS` is the interrupt granularity while idle and
the spacing for same-day retries when provider data is still lagging. It is
**not** a fixed “wake Azure every N seconds” interval.

After a successful cycle with nothing left missing, the sampler sleeps until the
next settle event so AutoPause can engage (idle longer than 60 minutes).

Failures are logged and never raised into ranking request paths. AutoPause cold
starts remain expected on the first probe after a long idle.

## Historical read APIs (#168)

These endpoints read Azure SQL only. They never fall back to current rankings.
They require the archive path from issues #165–#167 (PR landing those changes).

| Endpoint | Behavior |
| --- | --- |
| `GET /api/rankings/history/dates?market=&universe=&type=&limit=5` | Archived trading dates for that ranking type, newest first |
| `GET /api/rankings/history?market=&universe=&type=&date=YYYY-MM-DD` | One EOD ranking list for that date |

Responses include `tradingDate` and `captureScope` so the UI can label historical
data. Missing archives → `404`; archive DB unavailable or mid-query failure → `503`.
`GET /api/rankings` is unchanged and independent of Azure.

The Rankings UI loads `/history/dates` only after the user clicks **Load
historical dates** for the current market/universe/ranking type (Current view
must not wake Azure). Changing tab/market/universe clears the opt-in so Azure is
not woken again until the user asks (#187).

## Free Offer live verification (#186)

**Mandatory after every `terraform apply` (and already wired into the live Azure
smoke job).** Terraform source / tags are not proof of enrollment.

Why the paid DB happened: Azure SQL Free Offer is typically enrolled only at
**database creation**. Updating `use_free_limit = true` on an existing General
Purpose Serverless database often leaves live `useFreeLimit: null` (billable)
while Terraform still shows the intended flags. `azurerm` `>= 4.30` exposes the
attributes; it cannot convert a paid DB in place. Do not treat
`cost-tier=azure-sql-free-offer` or “GP serverless” as Free Offer.

Do **not** auto-delete `rankings_archive` or `rankings_archive_free`. Cut over
`RANKINGS_ARCHIVE_DATABASE_URL` only after live verification; deleting the old
paid database is a manual last step.

### Operator cutover checklist (paid → Free Offer)

1. Create / confirm a Free Offer database (often a **new** DB such as
   `rankings_archive_free`) — Free Offer is typically create-time only.
2. Run `scripts/verify_azure_rankings_archive_free_offer.py` against the **new**
   database and confirm `useFreeLimit: true` + `AutoPause`.
3. Copy/verify historical archive rows into the Free Offer DB (row counts /
   snapshot keys) before pointing the app at it. Do **not** overwrite or delete
   the paid DB as part of this step.
4. Update `RANKINGS_ARCHIVE_DATABASE_URL` (local `.env`, k8s secret, and GitHub
   Actions secret) to the Free Offer connection string.
5. Set optional `AZURE_SQL_DATABASE_NAME` to the Free Offer DB name so CI’s Free
   Offer check targets the same resource.
6. Redeploy / restart the backend and confirm history + sampler against the new
   URL.
7. Only after a quiet period, **manually** delete the old paid database if you
   still want to reclaim it.

```bash
python3 scripts/verify_azure_rankings_archive_free_offer.py \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --server "$AZURE_SQL_SERVER_NAME" \
  --database rankings_archive

# equivalent live properties (must include these; null = NOT free):
# az sql db show -g "$AZURE_RESOURCE_GROUP" -s "$AZURE_SQL_SERVER_NAME" -n rankings_archive \
#   --query "{useFreeLimit:useFreeLimit,freeLimitExhaustionBehavior:freeLimitExhaustionBehavior,sku:currentSku,autoPauseDelay:autoPauseDelay,minCapacity:minCapacity,maxSizeBytes:maxSizeBytes}"
```

Expected live fields:

```json
{
  "useFreeLimit": true,
  "freeLimitExhaustionBehavior": "AutoPause"
}
```

If `useFreeLimit` is `null` or `false`, the database may be billable. Create a
new Free Offer database rather than assuming an in-place setting change.

## Backend container / k3s hookup

The backend image (`backend/Dockerfile`) installs **Microsoft ODBC Driver 18** and
`pyodbc` so pods can open `mssql+pyodbc://` URLs.

1. Provision Azure SQL via `infra/azure-rankings-archive` and note the URL from
   `terraform output rankings_archive_database_url_example` (URL-encode the password).
2. Allow the machine/cluster egress IP on the Azure SQL firewall (or use the
   Terraform `allowed_client_ipv4` input while testing from a single host).
3. Copy the k8s example secret and fill in values:

```bash
cp k8s/backend-rankings-archive-secret.yaml.example k8s/backend-rankings-archive-secret.yaml
# edit RANKINGS_ARCHIVE_* values, then:
kubectl apply -f k8s/backend-rankings-archive-secret.yaml -n stock-ticker
```

`k8s/backend.yaml` mounts `stock-ticker-backend-rankings-archive` with
`optional: true`, so the cluster still starts when the secret is absent.

4. Rebuild and restart the backend so the image includes ODBC + pyodbc:

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
kubectl rollout restart deployment/stock-ticker-backend -n stock-ticker
```

5. Smoke from inside the cluster (or from a host with the same firewall IP):

```bash
kubectl exec -n stock-ticker deploy/stock-ticker-backend -- \
  python -c "import pyodbc; print(pyodbc.drivers())"
# Expect a driver name containing "ODBC Driver 18 for SQL Server"
```

Then use `POST /api/rankings/archive/capture` or enable
`RANKINGS_ARCHIVE_EOD_ENABLED=true` in the secret.

## Connectivity + archive write/read smoke test

```bash
export RANKINGS_ARCHIVE_DATABASE_URL='mssql+pyodbc://...'
python3 scripts/azure_rankings_archive_smoke.py
# or with GitHub Actions summary append:
python3 scripts/azure_rankings_archive_smoke.py --ci-summary
```

The script connects through SQLAlchemy + pyodbc, checks ODBC Driver 18, exercises
a disposable `_poc_smoke` table, verifies archive schema + history-dates query,
optionally reads one real archive when present, then upserts / loads /
reconstructs a disposable EOD archive snapshot. Cleanup is verified; the smoke
**fails** if the disposable row cannot be removed.

## Live Azure SQL CI smoke (#179)

Job `azure-rankings-archive-smoke` in `.github/workflows/test-and-smoke.yml`
runs on:

- `workflow_dispatch` on `main` / `master` only (manual)
- push to `main` / `master`
- weekly schedule (Monday 06:00 UTC)

It does **not** run on pull requests (including forks), and manual dispatch from
feature branches is rejected so branch-modified scripts cannot consume repository
Azure secrets. Required GitHub Actions secrets (values never logged):

| Secret | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | Service principal app id |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_TENANT_ID` | Entra tenant |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription |
| `AZURE_RESOURCE_GROUP` | Resource group of the SQL server |
| `AZURE_SQL_SERVER_NAME` | SQL server name (not FQDN) |
| `AZURE_SQL_DATABASE_NAME` | Optional; defaults to `rankings_archive` for Free Offer check |
| `RANKINGS_ARCHIVE_DATABASE_URL` | `mssql+pyodbc://…` archive URL |

After Azure login the job runs `scripts/verify_azure_rankings_archive_free_offer.py`
(read-only `az sql db show`) and **fails** if live `useFreeLimit` is not true
(#186). Then it opens a **temporary single-IP**
firewall rule for the runner, builds the backend Docker image (ODBC Driver 18 +
`pyodbc` parity), runs `scripts/azure_rankings_archive_smoke.py --ci-summary`,
and always deletes the firewall rule. It will not enable broad public access if
the server has public networking disabled.

Manual run:

```bash
gh workflow run test-and-smoke.yml
# or from the Actions UI: Test And Smoke → Run workflow
```

## Unit tests

```bash
cd backend
.venv313/bin/pytest \
  tests/unit/infra/test_azure_rankings_archive_terraform.py \
  tests/unit/infra/test_verify_azure_free_offer.py \
  tests/unit/repositories/test_rankings_archive_repository.py \
  tests/unit/services/test_rankings_archive_service.py \
  tests/unit/routes/test_rankings_archive_route.py \
  tests/unit/routes/test_rankings_history_route.py \
  tests/unit/jobs/test_jobs_runtime.py \
  tests/unit/db/test_archive_session.py -q
```
