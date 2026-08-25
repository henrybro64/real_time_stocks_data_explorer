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
rebuilds a `RankingResponse` in archived rank order (stock exchange/currency/
timezone are reconstructed from market + universe; personal fields are never
stored).

Archive DB connects lazily. A failed connect (including Free-offer AutoPause
cold start) is retried after a short cooldown instead of sticking unavailable
until process restart.

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
the capture path in tests. It still skips non-trading dates. Do **not** use
`force` with an explicit `tradingDate` in production unless you accept that the
archived snapshot may be incomplete relative to a true end-of-day settle.

Hardcoded Taiwan quote fallback responses are never archived
(`allow_hardcoded_fallback=false` + `twRankingsScope=fallback` skip).

### Automatic sampler

```bash
RANKINGS_ARCHIVE_EOD_ENABLED=true
RANKINGS_ARCHIVE_EOD_POLL_SECONDS=900
```

Failures are logged and never raised into ranking request paths.

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

## Connectivity + archive write/read smoke test

```bash
export RANKINGS_ARCHIVE_DATABASE_URL='mssql+pyodbc://...'
python3 scripts/azure_rankings_archive_smoke.py
```

The script connects, exercises a disposable `_poc_smoke` table, then upserts /
loads / reconstructs a disposable EOD archive snapshot (cleaned up afterward).

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
