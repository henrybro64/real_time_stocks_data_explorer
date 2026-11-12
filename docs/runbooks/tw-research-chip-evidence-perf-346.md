# TW research chip-evidence performance probe (#346)

Measure, do not guess. Every cell in the results table starts as **not yet measured**.
Fill values only after running the steps below against private k3s.

## Prereqs

1. Port-forward the backend Service (name from `k8s/backend.yaml`: `stock-ticker-backend`):

   ```bash
   sudo k3s kubectl -n stock-ticker port-forward svc/stock-ticker-backend 8000:8000
   ```

2. Auth header if the cluster requires it (omit locally when auth is disabled):

   ```bash
   export AUTH_HEADER="Authorization: Bearer <token>"
   ```

3. Obtain a real `SNAPSHOT_ID` (read-only):

   ```bash
   # List first-pass jobs / snapshots for your user, then pick a completed snapshot id.
   curl -sS ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
     http://localhost:8000/api/tw-research/first-pass/jobs | jq .
   # Or use a known snapshot from the research UI Evidence tab.
   export SNAPSHOT_ID=fps_...
   export STOCK_ID=TW:2330
   ```

4. Offline micro-benchmark (no cluster; optional sanity check):

   ```bash
   cd backend && python scripts/bench_chip_evidence.py --n 73 300 1000 --iterations 20
   ```

## Baseline → probe → after

1. Before the probe:

   ```bash
   sudo k3s kubectl -n stock-ticker top pod | grep stock-ticker-backend || true
   ```

2. Run the live READ-ONLY probe (never starts jobs/scans/batches):

   ```bash
   BASE_URL=http://localhost:8000 \
   AUTH_HEADER="$AUTH_HEADER" \
   SNAPSHOT_ID="$SNAPSHOT_ID" \
   STOCK_ID="${STOCK_ID:-TW:2330}" \
   ITERATIONS=20 \
     ./scripts/bench-tw-research-endpoints.sh
   ```

3. During / after:

   ```bash
   sudo k3s kubectl -n stock-ticker top pod | grep stock-ticker-backend || true
   ```

4. Collect timing log lines (marker `endpoint.timing.completed`):

   ```bash
   sudo k3s kubectl -n stock-ticker logs deploy/stock-ticker-backend --since=10m \
     | grep endpoint.timing.completed
   ```

## No extra upstream fan-out

Over the probe window, grep backend logs for provider / TWSE / TPEx / Yahoo request
lines using the real logger names:

| Logger | Notes |
|--------|--------|
| `app.services.daily_price_history_service` | stored-history reads / rare warnings |
| `app.services.market_clock` | holiday-calendar fetch warnings |
| `app.services.providers.tw_provider` | TWSE/TPEx history |
| `app.services.http_client` | `external_http.request.completed` |
| `app.services.tw_margin_short_service` | margin upstream |
| `app.services.tw_ownership_concentration_service` | TDCC upstream |

**Expected:** zero upstream fetches, except at most **one** holiday-calendar preload of
≤4 fetches on a cold pod (`ensure_tw_calendars_bounded` / `preload_tw_holiday_calendars`).

```bash
sudo k3s kubectl -n stock-ticker logs deploy/stock-ticker-backend --since=10m \
  | grep -E 'external_http\.request|tw\.history|holiday|preload|tw_provider|daily_price_history' || true
```

## Results table

Leave every value cell as `not yet measured` until you run the probe. Never invent numbers.

| endpoint | records | p50 ms | p95 ms | response KB | pod CPU Δ | pod mem Δ | upstream calls | measured? (yes/no) | date/node |
|----------|---------|--------|--------|-------------|-----------|-----------|----------------|--------------------|-----------|
| GET /api/stocks/{id}/technical-indicators | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | no | not yet measured |
| GET .../manifest | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | no | not yet measured |
| GET .../chunk | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | no | not yet measured |
| GET .../chip-evidence | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | no | not yet measured |
| POST .../report-evidence (first_pass_only) | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | no | not yet measured |

## Pass criteria (proposals — evaluate after measuring)

These thresholds are **proposals**, not proven SLOs:

| Check | Proposed default |
|-------|------------------|
| chip-evidence page p95 | **< 300 ms** at ~300 records |
| report-evidence p95 | **< 1 s** at ~300 records / 40 candidates |
| Upstream calls | **none**, except bounded calendar preload (≤4 fetches) on a cold pod |
| Stability | **no OOM / no restarts** during the probe window |

Update this section only after real k3s numbers exist.
