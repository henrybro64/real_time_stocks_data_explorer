# Testing strategy

What is tested today, where, how to run it, and where the gaps are. Sizes are approximate as of October 2026 and grow as tests are added; re-check them during the [maintenance cleanup](maintenance-cleanup.md) pass.

## Layers at a glance

| Layer | Tool | Location | Size | Runs in CI job |
| --- | --- | --- | --- | --- |
| Backend unit | pytest | `backend/tests/unit/` | ~1,300 cases | `backend-pytest` |
| Backend integration | pytest + FastAPI `TestClient` + Postgres | `backend/tests/integration/` | ~80 cases | `backend-pytest` (Postgres service) |
| Frontend unit | Vitest + jsdom + Testing Library | `frontend/src/**/*.test.ts(x)`, `frontend/tests/unit/` | ~420 cases in ~44 files | `frontend-playwright` |
| End-to-end | Playwright (Chromium) | `frontend/tests/e2e/` | 12 specs, ~25 tests | `frontend-playwright` |
| Deployment smoke | pytest wrapper around `scripts/ci-k3s-smoke.sh` | `backend/tests/smoke/` | 1 scenario | `k3s-smoke` |
| Archive smoke (manual) | `scripts/azure_rankings_archive_smoke.py` | — | live Azure SQL | `azure-rankings-archive-smoke` (manual dispatch on `main` only) |
| Static analysis | SonarCloud quality gate | `sonar-project.properties` | — | SonarCloud check |

## Backend

**Unit tests** (`backend/tests/unit/`) are grouped like the app: `services/` (most of the suite), `routes/`, `repositories/`, `db/`, `jobs/`, `core/`, `api/`, plus three groups that guard non-code assets:

- `docs/` — README and docs links must resolve; product-scope and multi-user docs must keep key statements.
- `infra/` — Terraform, the GHCR publish workflow, and the AWS heartbeat.
- `observability/` — the k8s monitoring stack.

Provider HTTP is never called. Tests monkeypatch the fetch functions (`_load_json`, `fetch_json`, ...) and feed synthetic or recorded payloads, including real TWSE MIS rows captured during a session (`test_tw_quote_integrity.py`). Clocks are frozen by patching `datetime` in the module under test, and suites that depend on market hours stub the TWSE holiday calendar.

**Integration tests** (`backend/tests/integration/api/`) run the real FastAPI app against Postgres:

- The schema is created from SQLAlchemy metadata once per session and reset before each test.
- Background samplers are disabled.
- The whole package is skipped when `DATABASE_URL` is unset.
- Covered: watchlist, portfolio, assets, portfolio history, alerts, realized trades, rankings, search/quotes, stock detail, Taiwan market data / 資券, themes, health, and multi-user isolation (`test_multi_user_design.py`).

## Frontend

**Unit tests** (Vitest, jsdom) cover components (stock card, quote summary, charts, search, layout), page views and their state hooks, API client helpers, formatting, freshness labels, and price-tone conventions. Most sit next to the code; a few suites live in `frontend/tests/unit/`.

**E2E tests** (Playwright, `frontend/playwright.config.ts`):

- Playwright starts the real backend (`alembic upgrade head`, seed `default-user`, `uvicorn`) and `next dev`, then runs one worker. CI uses 1 retry and longer timeouts.
- Every spec uses the fixture in `tests/e2e/setup.ts`, which intercepts `/api/*` with deterministic in-memory mocks (`e2eApiMocks.ts`): search, quotes, markets, watchlist, portfolio, alerts, and auth status. Unmatched requests continue to the real backend.
- So E2E checks real UI wiring and user flows with stable data, not live market data. `PLAYWRIGHT_MOCKS_ONLY=1` skips starting the backend; `PLAYWRIGHT_SKIP_WEBSERVER=1` uses servers you already started.
- Flows covered: watchlist add/remove/persist and polling cadence; search; stock detail (quote, every chart range, key stats, news fallback, Taiwan insights, 法人 / 資券 loading, empty, and error states, bookmarkable route); rankings current vs. historical date; portfolio add/edit/remove and holdings layout; portfolio AI "not configured" guidance; assets; alerts with triggered events; Taiwan vs. U.S. price colors; market snapshot layout; two-user isolation.

## Deployment smoke

`k3s-smoke` installs k3s on the runner and deploys with `scripts/deploy-k3s.sh k3s`. It then checks that:

- the pods are Ready and the migration init container completed;
- the backend `/health` and `/api/markets` respond;
- the frontend reaches the backend through the Next.js rewrite and service DNS.

Evidence (kubectl snapshots, logs, responses) is uploaded as an artifact.

## Allure reporting

Each layer can write Allure results (`allure-pytest`, `allure-vitest`, `allure-playwright`). The `allure-report` CI job downloads all of them, merges with `scripts/merge_allure_results.py`, and uploads an HTML report artifact. On `main`, it also publishes to GitHub Pages when the repository variable `ENABLE_ALLURE_PAGES=true`.

## How to run

```bash
# Backend (from repo root; install backend/requirements-dev.txt first)
pytest backend/tests/unit
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks_test \
  pytest backend/tests/integration

# Frontend
npm test --prefix frontend            # Vitest
npm run test:e2e --prefix frontend    # Playwright (starts backend + Next.js)
npm run build --prefix frontend       # type check + production build

# Allure (optional)
npm run test:backend:allure && npm run test:frontend:allure && npm run test:e2e:allure
npm run allure:report
```

The k3s smoke needs a Linux host with k3s: `scripts/ci-k3s-smoke.sh` after `scripts/deploy-k3s.sh k3s`.

## CI behavior

`.github/workflows/test-and-smoke.yml` runs on pull requests, pushes to `main`, and version tags:

- `backend-pytest` runs unit and integration tests together.
- `frontend-playwright` runs the Vitest and Playwright suites (each continues on error so both report), then fails the job if either failed.
- `k3s-smoke` runs after the test jobs succeed.
- There is no scheduled run, so the Azure archive is never woken by routine CI.

`publish-ghcr.yml` pushes images only after those jobs pass on `main` or a tag.

## Biggest gaps

- **No coverage measurement.** `pytest-cov` is installed in CI but not run, and no coverage report reaches SonarCloud (it shows 0% coverage on new code).
- **No contract check between frontend and backend.** `packages/shared-contracts` and the Pydantic schemas are kept in sync by hand. E2E mocks the API, so a backend field rename would not fail E2E.
- **No live-provider checks.** Upstream format changes are only noticed in use. For example, TWSE MIS leaving `z` blank between trades (#258) was found in the running app, not by tests.
- **Migrations in integration tests.** The schema comes from SQLAlchemy metadata, not Alembic. Migrations run only in the E2E backend start and the k3s smoke.
- **Thin frontend unit coverage** for the AI settings and assets views (assets is covered in E2E only).
- **Background jobs across replicas** (refresher leadership, alert evaluator) are unit-tested in-process only.
- **No accessibility, visual-regression, or load tests.**
