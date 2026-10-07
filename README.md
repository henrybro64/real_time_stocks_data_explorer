# Stock Ticker

Self-hosted, bilingual research dashboard for **U.S. and Taiwan** stocks, with a personal watchlist, portfolio, realized P/L, price alerts, market rankings, and optional AI explanations. The UI is user-selectable between **English** and **Traditional Chinese**. Next.js frontend, FastAPI backend, PostgreSQL, optional Redis, deployable to a home k3s/k3d cluster.

## Problem statement

A retail investor (散戶) who follows both Wall Street and the Taiwan exchanges usually juggles separate apps: a U.S. broker app, a Taiwan quote app, a spreadsheet for holdings, and news sites. None of them share a watchlist or portfolio, and free Taiwan data (法人 flows, 資券 margin/short, 籌碼 concentration, limit-up/down) is scattered across official sites.

Stock Ticker brings this into one self-hosted app: one watchlist and portfolio across both markets, Taiwan-specific datasets next to the chart, and honest freshness labels, because free data sources are delayed or unavailable at times. It is **not** a brokerage, trading engine, or source of financial advice. Full product boundaries: [docs/product-scope.md](docs/product-scope.md).

## What you can do

| Area | Highlights |
| --- | --- |
| Dashboard | Market snapshot (indices, USD/TWD, WTI, gold, BTC/ETH), stock search, watchlist grouped into Taiwan and U.S. with live/delayed/stale badges |
| Stock detail | Quote with Taiwan limit-up/down highlighting, 1D–1Y charts with SMA 10/20/60 and a forming daily candle, key statistics, news, Taiwan 法人 / 資券 / 籌碼集中度 / 財報三率, U.S. peer snapshot, stock AI analysis |
| Rankings | Volume, movers, valuation, Taiwan net-buy/net-sell and live limit-up/down, for DJIA, NASDAQ-100, TWSE, and TPEx; optional end-of-day history |
| Portfolio | Taiwan and U.S. holdings with unrealized P/L, other assets, USD/TWD totals, sampled value history, optional portfolio AI |
| Realized P/L | Manual closed trades in TWD or USD, dividends, period summaries |
| Alerts | `price_above` / `price_below` rules with in-app event history |

### Sample flows

1. **Track a Taiwan stock.** Search "2330" on the dashboard and add TSMC to the watchlist. The card appears in the Taiwan section with price, change, and a freshness badge, and its quote is refreshed by the backend every ~45 s while TWSE is open.
2. **Research it.** Open the card. The detail page shows the quote (red/green follow the Taiwan convention), a 1D–1Y chart with moving averages, key stats, and the Taiwan tabs for institutional flows and margin/short balances. The AI stock analysis panel asks the configured model to explain backend-computed metrics in the selected language.
3. **Record a position.** On Portfolio, add 1,000 shares at your average cost. Holdings split into Taiwan and U.S. tables, totals convert with USD/TWD, and a background job samples portfolio value for the history chart.
4. **Get notified.** Create a `price_above` alert. The backend evaluator checks it every 60 s and records an event you see on the Alerts page.
5. **Scan the market.** Rankings shows today's TWSE limit-up list from the exchange's own limit prices, or the top U.S. movers, each labeled with its data time and source.

### Screenshots

Captured on 2026-10-07 after the Taiwan close from a local run with live market data. Watchlist, holdings, and alert entries are sample data.

**Dashboard watchlist** — Taiwan and U.S. sections, freshness badges, 1-week sparklines (Taiwan colors: red up, green down).

![Dashboard watchlist grouped into Taiwan and U.S. stocks](docs/images/dashboard-watchlist.png)

**Latest market prices** — indices, crypto, and gold with session state; daily change is measured against the prior session's close.

![Index, crypto, and gold tiles on the dashboard](docs/images/dashboard-market-prices.png)

**Stock detail** — quote summary and a 6-month candlestick chart with SMA 10/20/60 overlays.

![TSMC stock detail with a six-month chart and moving averages](docs/images/stock-detail.png)

**Portfolio holdings** — Taiwan and U.S. positions in their own currency.

![Portfolio holdings split into Taiwan and U.S. tables](docs/images/portfolio-holdings.png)

**Alert rules** — price-above / price-below rules with the last observed price from the background evaluator.

![Alert rules table](docs/images/alerts.png)

## Architecture

```mermaid
flowchart LR
    Browser["Browser"] -->|"pages + /api"| Next["Next.js<br/>UI + /api proxy"]
    Next -->|"/api/*"| API["FastAPI<br/>routes → services"]
    Jobs["Background jobs<br/>quote refresher, alerts,<br/>portfolio + ranking snapshots"] --> API
    API --> PG[("PostgreSQL")]
    API --> Redis[("Redis<br/>optional cache")]
    API --> Up["Yahoo, TWSE MIS,<br/>TPEx, TDCC, SEC, news"]
    API --> AI["OpenAI / Perplexity<br/>(optional)"]
```

- The browser talks only to Next.js, which proxies `/api/*` to FastAPI (same origin, so cookies work without CORS).
- FastAPI routes stay thin. Services own business logic, caching, and market hours; provider adapters isolate each upstream.
- Postgres holds user data, ranking snapshots, and Taiwan daily datasets. Redis is optional: quotes fall back to an in-process cache.
- Background threads in the API process keep watchlist and portfolio quotes warm, so page loads are served from cache instead of waiting on upstream.

Details: [docs/architecture-overview.md](docs/architecture-overview.md).

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16, React 18, TypeScript, Carbon Design System, TanStack Query; hand-built SVG charts (Node **20+**) |
| Backend | Python 3.11, FastAPI, SQLAlchemy 2, Alembic, Pydantic settings |
| Data | PostgreSQL 16; Redis (optional) |
| Market data | Yahoo Finance / yfinance, TWSE MIS and OpenAPI, TPEx, TDCC, SEC ticker list, Stooq, Google News RSS, optional Finnhub |
| AI (optional) | OpenAI or Perplexity, server-wide or per-user key |
| Deploy | Docker images, kustomize manifests for k3d/k3s, optional GHCR images, optional Prometheus/Grafana |
| Quality | pytest, Vitest, Playwright, Allure, SonarCloud, GitHub Actions |

## Key design decisions

- **Free data, honest freshness.** No paid feed, so every quote carries the provider's market timestamp (quotes without one are rejected) plus cache metadata (`stale`, `refreshStatus`). The UI shows live / delayed / stale / closed / unavailable instead of pretending to be real-time. Last-known values are served only with a stale flag, and when no source has a trustworthy price the API returns *unavailable* rather than substituting one.
- **Cache-first reads, background refresh.** A market-aware refresher (45 s open, 15 min closed) with concurrency limits, jitter, circuit breakers, and a Redis leader lock protects rate-limited free providers, so quote polling from the UI never fans out to upstream. See [docs/quote-refresh-design.md](docs/quote-refresh-design.md).
- **Multi-user in the data model, not just the UI.** Every user-owned row has a `user_id`; routes resolve the user server-side; single-user mode is the same code with a seeded `default-user`.
- **Market math in the backend.** Change %, moving averages, chart layout, Taiwan limit status, and AI input metrics are calculated once in Python; the UI formats and renders them (realized-P/L period summaries are the main client-side calculation). AI explains these numbers; it is never a data source.
- **Modular monolith on a home cluster.** One FastAPI service plus in-process jobs is simple to run on a single k3s node. The cost is that scaling out needs Redis and more care around background jobs.
- **Market-specific rules where they matter.** Taiwan red-up/green-down colors, official limit prices from the exchange, TWSE vs. TPEx boards, and exchange holiday calendars are handled explicitly.

## Multi-user model

| Mode | Login | Who you are |
| --- | --- | --- |
| `AUTH_ENABLED=false` (local default) | none | the seeded `default-user` |
| `AUTH_ENABLED=true` + `LOCAL_AUTH_ENABLED=true` (k8s default) | username/password, session cookie | your own `users.id` |

Watchlists, portfolios, assets, history, realized trades, alerts, and AI keys/preferences are isolated per user in both modes. Market data is shared. Isolation is covered by Postgres integration tests and a two-session Playwright test. Details and limitations: [docs/multi-user-design.md](docs/multi-user-design.md).

## Testing strategy

| Layer | Tooling | Scope |
| --- | --- | --- |
| Backend unit | pytest | services, providers with recorded payloads, routes, repositories, docs/infra guardrails |
| Backend integration | pytest + Postgres | real API + database: CRUD, rankings, Taiwan data, multi-user isolation |
| Frontend unit | Vitest + Testing Library | components, page state, formatting, freshness labels |
| E2E | Playwright | user flows on the real Next.js app with a deterministic mocked API |
| Deployment smoke | k3s on the CI runner | pods Ready, migrations ran, `/health`, frontend → backend proxy |

Details, how to run each layer, and known gaps: [docs/testing-strategy.md](docs/testing-strategy.md).

## CI/CD

- **Test And Smoke** (`.github/workflows/test-and-smoke.yml`, on PRs, `main`, and tags): `backend-pytest` and `frontend-playwright` run in parallel with Postgres services; `k3s-smoke` deploys the real manifests to k3s; `allure-report` merges every layer's results into one report (optionally published to GitHub Pages).
- **Publish GHCR Images** (`publish-ghcr.yml`) builds backend and frontend images only after those jobs pass on `main` or a version tag.
- **SonarCloud** runs a quality gate on every PR; Taiwan market-data changes follow an extra checklist (see Documentation).
- Automated PR review and Dependabot security updates run on the repository; there is no scheduled CI, so optional cloud resources are never woken by routine runs.

## Getting started

### Local development

**Backend**

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks"
alembic upgrade head
uvicorn app.main:app --reload
```

With `AUTH_ENABLED=false`, startup seeds the single-user principal (`default-user`) for watchlist, portfolio, and AI routes.

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. The UI proxies `/api` to the backend (see [docs/local-development.md](docs/local-development.md#api-url-shape)).

**Redis (optional cache)**

```bash
cd backend && docker compose -f docker-compose.redis.yml up -d
# backend/.env: REDIS_URL=redis://127.0.0.1:6379/0
```

Copy `backend/.env.example` and `frontend/.env.example` for other settings (AI keys, Finnhub, auth).

### Run the tests

Install `backend/requirements-dev.txt` and the `frontend/package.json` dependencies first.

```bash
pytest backend/tests/unit backend/tests/integration   # integration needs DATABASE_URL
npm test --prefix frontend
npm run test:e2e --prefix frontend
npm run build --prefix frontend
```

**Allure** (optional merged report): `npm run test:backend:allure`, `npm run test:frontend:allure`, `npm run test:e2e:allure`, then `npm run allure:report`.

### Kubernetes (k3d / k3s)

Local no-registry flow: build images → import into k3d/k3s → `kubectl apply -k k8s`.

```bash
./scripts/deploy-k3s.sh k3d   # macOS
./scripts/deploy-k3s.sh k3s   # Ubuntu
```

Full steps, backup/restore, troubleshooting, LAN NodePort (`:30080`), and optional GHCR image pulls: [docs/kubernetes.md](docs/kubernetes.md). Optional Prometheus/Grafana overlay: [docs/observability.md](docs/observability.md).

### LAN access

Other machines on your network should use `http://<host-ip>:3000` when Next binds `0.0.0.0` (`npm run dev:lan` or `./scripts/dev-lan.sh`). For k8s, use NodePort **`http://<node-ip>:30080`**. Details: [docs/local-development.md](docs/local-development.md#lan-access).

## Repository layout

```text
backend/                    FastAPI app, tests, Alembic migrations
frontend/                   Next.js UI, Vitest and Playwright tests
packages/shared-contracts/  Shared TypeScript API types
k8s/                        Kubernetes manifests (kustomize)
scripts/                    Deploy, backup, CI smoke, and dev helpers
docs/                       Architecture, design, ops, and feature guides
infra/                      Optional Terraform: Azure SQL rankings archive, AWS heartbeat monitor
```

## Known limitations

- **Data is best-effort.** Free sources can be delayed (Yahoo is ~20 min behind for Taiwan), rate-limited, or change format without notice. The app flags stale or missing data instead of hiding it; see [docs/quote-refresh-design.md](docs/quote-refresh-design.md#current-limitations).
- **Single-node deployment.** Background jobs run inside the API process, and the k8s manifests run one backend replica with in-cluster Postgres.
- **Basic authentication.** Local username/password only: open self-registration, no rate limiting, and per-user AI keys stored unencrypted.
- **Manual records.** No broker sync, automatic FX conversion, or tax-lot accounting; alerts are in-app only (no email/push).
- **Partial localization.** Many API error messages stay in English, and portfolio AI does not follow the UI language.
- **Testing gaps.** No coverage reporting, no automated frontend/backend contract check, no live-provider canaries (see [docs/testing-strategy.md](docs/testing-strategy.md#biggest-gaps)).

## Future roadmap

Planned work tracked in GitHub issues:

- **Cloud deployment** — production deployment contract (#59), AWS ECS Fargate (#60, #61) and Azure Container Apps (#62, #63), with cloud smoke tests and teardown runbooks (#64).
- **Authentication** — Microsoft Entra External ID as an additional login provider (#240).
- **Networking** — local DNS hostname and k3s Ingress (#241).
- **Observability** — application metrics and dashboards (#28), on top of the existing workload monitoring.

Candidate improvements from the design docs (not yet filed): coverage reporting in SonarCloud, a contract test between `packages/shared-contracts` and the API schemas, encrypted API-key storage, login rate limiting, and a leader lock for the alert evaluator.

## Documentation

| Topic | Document |
| --- | --- |
| Product scope (retail investor) | [docs/product-scope.md](docs/product-scope.md) |
| Architecture overview | [docs/architecture-overview.md](docs/architecture-overview.md) |
| Multi-user / auth model | [docs/multi-user-design.md](docs/multi-user-design.md) |
| Testing strategy | [docs/testing-strategy.md](docs/testing-strategy.md) |
| Quote and ranking refresh design | [docs/quote-refresh-design.md](docs/quote-refresh-design.md) |
| Backend module map | [docs/backend-architecture.md](docs/backend-architecture.md) |
| Local setup, features, env vars | [docs/local-development.md](docs/local-development.md) |
| Kubernetes deploy, GHCR images, troubleshooting | [docs/kubernetes.md](docs/kubernetes.md) |
| Structured logging | [docs/backend-logging.md](docs/backend-logging.md) |
| k8s workload monitoring | [docs/observability.md](docs/observability.md) |
| Azure EOD rankings archive (optional) | [docs/azure-rankings-archive.md](docs/azure-rankings-archive.md) |
| AWS outbound heartbeat monitor (optional) | [docs/aws-health-monitor.md](docs/aws-health-monitor.md) |
| Taiwan 資券 upstream endpoints | [docs/taiwan-margin-short-endpoints.md](docs/taiwan-margin-short-endpoints.md) |
| Taiwan market-data SonarCloud checklist | [docs/taiwan-market-data-sonar-checklist.md](docs/taiwan-market-data-sonar-checklist.md) |
| Code and docs cleanup cadence | [docs/maintenance-cleanup.md](docs/maintenance-cleanup.md) |

Taiwan market-data backend PRs must pass **SonarCloud** in addition to pytest — use the checklist above.
