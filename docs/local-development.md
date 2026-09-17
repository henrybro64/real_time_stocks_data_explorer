# Local development

Setup, env vars, and endpoint notes for running the backend and frontend on your machine (outside Kubernetes). Product intent, workflows, and boundaries: [product-scope.md](product-scope.md).

## Database (Postgres)

```bash
cd backend
export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks"
alembic upgrade head
uvicorn app.main:app --reload
```

Repair or ad-hoc re-seed of `default-user`: `python -m scripts.seed_default_user`.

## Redis

```bash
cd backend
docker compose -f docker-compose.redis.yml up -d   # stop: ... down
```

Set `REDIS_URL=redis://127.0.0.1:6379/0` in `backend/.env`.

## Logging

Structured logs cover API requests, provider flows, and background jobs. Key env vars:

```text
LOG_LEVEL=INFO
LOG_FORMAT=console   # or json
REQUEST_ID_HEADER=X-Request-ID
```

Each response includes `X-Request-ID` when possible. See [backend-logging.md](backend-logging.md).

## AI stock analysis

Stock detail: `GET /api/stocks/{stock_id}/analysis`. Technicals prefer the same candles the chart plots; yfinance is the fallback and still supplies company info. An optional AI provider then writes a narrative from that backend JSON.

`computedData` carries the context blocks the narrative must cite: `technicalAnalysis` (chart-aligned moving averages, momentum, and backend-derived `keyPriceLevels` on completed sessions only), `institutionalAnalysis` (Taiwan 法人 net lots), `fundamentalOutlook`, and `recentNewsContext`. Anything unavailable is reported with a classified reason rather than being filled in by the model.

`?language=en|zh-TW` selects the narrative language (default `zh-TW`) and is part of the Redis cache key, so the two languages never overwrite each other. `?refresh=true` regenerates the narrative and is the only path that fetches news; plain reads restore the news snapshot stored with the cached narrative.

```text
AI_PROVIDER=openai        # implemented: openai | perplexity
AI_MODEL=gpt-4o-mini
OPENAI_API_KEY=...
# PERPLEXITY_API_KEY=...  when AI_PROVIDER=perplexity
```

`anthropic`, `gemini`, and `ollama` are reserved names but not implemented yet (`AIProviderConfigurationError`).

Without a provider key, metrics still return with warnings. AI language, Redis narrative cache, and “not advice” limits: [product-scope.md](product-scope.md#market-data-and-ai-sources).

## Data freshness

Quote views use `quote.timestamp` and `quote.isMarketOpen` for badges (`live`, `delayed`, `stale`, `closed`). Failed refreshes with cached data show stale notices via `RequestStateNotice` (`stale`, `unavailable`, `error`, `auth`).

Rankings expose `timestamp`, `sourceType`, `dataFreshnessLabel`, and related fields; free sources may be delayed or unavailable.

## Market snapshot

Home dashboard calls:

```text
GET /api/markets/indices
GET /api/markets/fx/usd-twd
GET /api/markets/commodities/wti-crude-oil
```

Market indices plus BTC/ETH (USD) and gold futures (USD/oz) use the fixed catalog in `packages/shared-contracts/marketIndexCatalog.json`; crypto trades continuously and refreshes with the live dashboard cadence. FX/commodity quotes soft-cache ~60s and may serve retained values as stale after provider failure.

## Market rankings

```text
GET /api/rankings?market=TW|US&type=...&limit=50
```

Optional `twBoard` (`TWSE` | `TPEx`) for Taiwan. Postgres stores intraday/daily snapshots; optional Azure SQL EOD archive is separate — see [azure-rankings-archive.md](azure-rankings-archive.md).

Key settings: `RANKINGS_SNAPSHOT_ENABLED`, `RANKINGS_REFRESH_SECONDS`, `RANKINGS_UNIVERSE_US`, `RANKINGS_UNIVERSE_TW`, `RANKINGS_CAPTURE_TOKEN`.

## Taiwan market data

Scheduled job persists TWSE/TPEx institutional and 資券 daily rows. Detail API reads from store when fresh; otherwise probes a recent weekday window.

On-demand Taiwan stock-detail tabs (lazy-loaded in the UI):

```text
GET /api/stocks/{stock_id}/tw-ownership-concentration?range=3M|6M|1Y
GET /api/stocks/{stock_id}/tw-profitability?quarters=8|12|20
```

Ownership uses TDCC concentration plus TWSE `MI_QFIIS` / TPEx QFII foreign % and quote-history closes. Profitability derives quarterly margins from yfinance income statements.

- Lot size: use `shares_to_lots` / `taiwan_shares_per_lot` (default 1000 shares/張; `K`/`C` ETF heuristic → 100).
- **SonarCloud:** backend PRs in this area must clear the quality gate — [taiwan-market-data-sonar-checklist.md](taiwan-market-data-sonar-checklist.md).
- Cold start: run `POST /api/taiwan-market-data/capture?days=120` (token) before expecting full institutional charts.

Settings: `TAIWAN_MARKET_DATA_REFRESH_*`, `TAIWAN_MARKET_DATA_CAPTURE_TOKEN`. Endpoint contracts: [taiwan-margin-short-endpoints.md](taiwan-margin-short-endpoints.md).

## Threshold alerts

User-scoped rules (`price_above`, `price_below`) with durable events. Routes under `/api/alerts/*`; UI at `/alerts`. In-process evaluator (`ALERT_EVALUATOR_ENABLED`); transition-based de-duplication (no email/push in V1). Clear recent event history with `DELETE /api/alerts/events` (rules and `lastConditionMet` state are preserved).

## Realized P/L (#194 / #195 / #204)

User-scoped manual trade records in `realized_trades` (TWD or USD only, no FX conversion). Routes under `/api/realized-trades`; UI at `/realized-gains`. Optional `cumulativeDividends` (native currency cash total, default `0`) is included in derived `realizedGain` / `realizedReturnPercent` on read; `priceGain` is the price-only component. Period summaries and the 12-month chart aggregate client-side by `sellDate` using `realizedGain` once.

## LAN access

1. Bind Next to all interfaces: `npm run dev:lan` or `./scripts/dev-lan.sh` (`0.0.0.0:3000`).
2. Backend: `--host 0.0.0.0` only if browsers call the API directly; default `127.0.0.1` is fine with the Next proxy.
3. Open firewall port 3000 if `ufw` is enabled.
4. With `AUTH_ENABLED=true`, set `LOCAL_AUTH_ENABLED=true` for local signup at `/login`.
5. **k8s:** use `http://<node-ip>:30080`, not `:3000` — see [kubernetes.md](kubernetes.md).

Verify on the host: `curl -sI -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000` and the same with your LAN IP.

## API URL shape

**Preferred:** one origin — UI at `/`, API at `/api` via Next rewrites (`BACKEND_PROXY_URL`, default `http://127.0.0.1:8000`).

Direct API calls: `NEXT_PUBLIC_API_BASE_URL` + matching `CORS_ORIGINS` on the backend (`API_PREFIX` / `NEXT_PUBLIC_API_PREFIX`, default `/api`).

## Optional Finnhub fundamentals

Key statistics (market cap, beta, P/E, etc.) merge Finnhub when `FINNHUB_API_KEY` is set on the backend.

## Where calculations run

- **Backend:** chart math, key-statistics ranges, quote/history aggregation.
- **Frontend:** rendering, chart hit-testing, locale formatting, i18n.
