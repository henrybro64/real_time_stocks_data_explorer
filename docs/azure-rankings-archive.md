# Azure SQL rankings archive

Optional **public market ranking archive** path, additive to PostgreSQL-backed app data.

## Separation of concerns

| Store | Env var | Contents |
| --- | --- | --- |
| Local / cluster Postgres | `DATABASE_URL` | Operational app data + current rankings snapshots |
| Azure SQL Free offer | `RANKINGS_ARCHIVE_DATABASE_URL` | Public EOD ranking archives only |

Azure must never receive personal data (users, OAuth, watchlists, portfolios,
alerts, API keys, AI preferences).

Unset `RANKINGS_ARCHIVE_DATABASE_URL` and the app behaves exactly as before.
Azure outages must not block FastAPI startup or `GET /api/rankings`.

## Provisioning

See [`infra/azure-rankings-archive/README.md`](../infra/azure-rankings-archive/README.md)
for Terraform apply/destroy, Free-offer portal checks, and cost-safety review.

## Schema + repository

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

## EOD capture

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

The sampler is **market-aware**: it only opens Azure SQL inside a
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

## Snapshot retention

Azure SQL Free Offer storage is finite. Default retention keeps about **two
years** of EOD archives:

```bash
RANKINGS_ARCHIVE_RETENTION_DAYS=730
```

- `730` (default): delete rows with `trading_date` older than UTC today − 730 days
- `0`: disable purge explicitly (unlimited growth — not recommended)
- Valid range: `0`–`3650`

Purge runs **opportunistically** at the end of a capture cycle that already
touched Azure SQL (sampler or manual capture). It does **not** add a separate
poller or wake Azure solely because the purge interval elapsed. In-process, purge
runs at most about once every 7 days **per backend process**.

With multiple replicas (e.g. k3s), each pod that completes a capture cycle may
purge independently — still only while Azure is already awake, but fleet-wide
purge SQL can occur more often than weekly. That is acceptable for this PoC; use
a single archive capture leader or external coordination only if you need a
strict global weekly cap.

History read APIs never run retention purge — only capture cycles do.

Structured logs use `event=rankings_archive.retention.completed` with
`retention_days`, `cutoff_date`, `snapshots_deleted`, `items_deleted`, and
`purge_duration_ms` (no secrets / row contents).

Read-only preview (**operator shell only** — opens the archive DB and may wake
a paused serverless database; never call from HTTP handlers or CI). Returns
``None`` when retention is disabled or the archive is unavailable (do not treat
``None`` as “zero rows to purge”):

```python
from app.services.rankings_archive_service import rankings_archive_service
print(rankings_archive_service.preview_retention_purge())
```

After purge, `/api/rankings/history/dates` omits deleted dates and
`/api/rankings/history` for a purged date returns the normal missing-archive
`404`. Current rankings / Postgres are unchanged.

## Historical read APIs

These endpoints read Azure SQL only. They never fall back to current rankings.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/rankings/history/dates?market=&universe=&type=&limit=5` | Archived trading dates for that ranking type, newest first |
| `GET /api/rankings/history?market=&universe=&type=&date=YYYY-MM-DD` | One EOD ranking list for that date |

Responses include `tradingDate` and `captureScope` so the UI can label historical
data. Missing archives → `404`; archive DB unavailable or mid-query failure → `503`.
`GET /api/rankings` is unchanged and independent of Azure.

The Rankings UI loads `/history/dates` only after the user clicks **Load
historical dates** for the current market/universe/ranking type (Current view
must not wake Azure). Changing tab, market, or universe clears the opt-in so
Azure is not woken again until the user asks.

## Free Offer live verification

**Mandatory after provisioning or intentional Azure SQL changes.** Terraform
source / tags / SKU labels are **not** proof of Free Offer enrollment.

### Provider mismatch (fail-closed)

Released AzureRM used by `infra/azure-rankings-archive` **cannot** express Free
Offer on `azurerm_mssql_database`. This module therefore:

- does **not** create `azurerm_mssql_database` (that would provision paid GP serverless),
- references the external Free Offer database `rankings_archive_free` (name via
  variable; not an `azurerm_mssql_database` resource),
- refuses the deleted paid name `rankings_archive` via variable validation.

Create Free Offer enrollment outside Terraform (Portal, or a future supported
AzAPI/AzureRM path). Do not treat `cost-tier=azure-sql-free-offer` or
“GP serverless” as Free Offer. `useFreeLimit: null` means Free Offer is **not**
active.

The paid `rankings_archive` database was intentionally destroyed; keep
`rankings_archive_free` and do not recreate the paid name.

### Management-plane verification (does not wake SQL data plane)

```bash
python3 scripts/verify_azure_rankings_archive_free_offer.py \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --server "$AZURE_SQL_SERVER_NAME" \
  --database rankings_archive_free

# az sql db show -g "$AZURE_RESOURCE_GROUP" -s "$AZURE_SQL_SERVER_NAME" -n rankings_archive_free \
#   --query "{useFreeLimit:useFreeLimit,freeLimitExhaustionBehavior:freeLimitExhaustionBehavior,sku:currentSku,autoPauseDelay:autoPauseDelay,minCapacity:minCapacity,maxSizeBytes:maxSizeBytes}"
```

Expected live fields:

```json
{
  "useFreeLimit": true,
  "freeLimitExhaustionBehavior": "AutoPause"
}
```

This check is for **explicit** operator verification. Normal PR/push CI does
**not** run it and does **not** connect to Azure SQL.

### Operator cutover notes

1. Confirm `rankings_archive_free` with the verify script (`useFreeLimit: true`,
   `AutoPause`).
2. Point `RANKINGS_ARCHIVE_DATABASE_URL` (local / k8s / GitHub secret) at that DB.
3. Redeploy the backend; confirm history + sampler.
4. Do not recreate `rankings_archive`.

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

## Live Azure SQL smoke — manual only

Job `azure-rankings-archive-smoke` in `.github/workflows/test-and-smoke.yml` is
**manual `workflow_dispatch` only** on `main` / `master`.

It does **not** run on:

- pull requests
- pushes to `main` / `master`
- any schedule / cron

Routine PR/push CI uses local Postgres, unit/integration tests, Playwright, and
k3s smoke only — **zero** live Azure SQL data-plane connections and **no**
temporary Azure SQL firewall rules.

The manual job clearly warns that it:

- connects to the live archive over ODBC/SQLAlchemy,
- may wake a paused serverless Free Offer database,
- consumes Free Offer compute quota,
- should be used only for deliberate connectivity verification.

Required GitHub Actions secrets (values never logged):

| Secret | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | Service principal app id |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_TENANT_ID` | Entra tenant |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription |
| `AZURE_RESOURCE_GROUP` | Resource group of the SQL server |
| `AZURE_SQL_SERVER_NAME` | SQL server name (not FQDN) |
| `AZURE_SQL_DATABASE_NAME` | Optional; must match URL DB name if set |
| `RANKINGS_ARCHIVE_DATABASE_URL` | `mssql+pyodbc://…` archive URL (`rankings_archive_free`) |

After Azure login the manual job runs management-plane Free Offer verification
(`az sql db show`, no SQL), then may open a **temporary single-IP** firewall
rule, build the backend image, run `scripts/azure_rankings_archive_smoke.py`,
and always deletes the firewall rule.

Manual run:

```bash
gh workflow run test-and-smoke.yml
# Actions UI: Test And Smoke → Run workflow (main/master only for the Azure job)
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
