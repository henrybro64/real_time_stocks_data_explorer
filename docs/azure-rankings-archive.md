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

Each sampler cycle looks at the newest settled trading days (bounded window)
and fills the newest missing archive per ranking query when provider dates
match. Failures are logged and never raised into ranking request paths.

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
reconstructs a disposable EOD archive snapshot (cleaned up afterward).

## Live Azure SQL CI smoke (#179)

Job `azure-rankings-archive-smoke` in `.github/workflows/test-and-smoke.yml`
runs on:

- `workflow_dispatch` (manual)
- push to `main` / `master`
- weekly schedule (Monday 06:00 UTC)

It does **not** run on pull requests (including forks). Required GitHub Actions
secrets (values never logged):

| Secret | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | Service principal app id |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_TENANT_ID` | Entra tenant |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription |
| `AZURE_RESOURCE_GROUP` | Resource group of the SQL server |
| `AZURE_SQL_SERVER_NAME` | SQL server name (not FQDN) |
| `RANKINGS_ARCHIVE_DATABASE_URL` | `mssql+pyodbc://…` archive URL |

The job authenticates with the service principal, opens a **temporary single-IP**
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
  tests/unit/repositories/test_rankings_archive_repository.py \
  tests/unit/services/test_rankings_archive_service.py \
  tests/unit/routes/test_rankings_archive_route.py \
  tests/unit/routes/test_rankings_history_route.py \
  tests/unit/jobs/test_jobs_runtime.py \
  tests/unit/db/test_archive_session.py -q
```
