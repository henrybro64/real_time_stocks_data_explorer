# Stock Ticker

Self-hosted bilingual dashboard for researching **U.S. and Taiwan** stocks and keeping a personal portfolio. The UI language is user-selectable (**English** or **Traditional Chinese**); stock names follow that choice when bilingual metadata exists. Next.js frontend, FastAPI backend.

Product intent, workflows, and boundaries: [docs/product-scope.md](docs/product-scope.md).

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js, TypeScript (Node **20+** — see `frontend/package.json`) |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Cache | Redis (optional locally; in-cluster in k8s) |
| Database | PostgreSQL |
| Deploy | `k8s/` manifests for local k3d/k3s |

## Repository layout

```text
backend/              FastAPI app, tests, migrations
frontend/             Next.js UI
k8s/                  Kubernetes manifests
packages/shared-contracts/  Shared TypeScript API types
scripts/              Deploy, backup, and dev helpers
docs/                 Architecture, ops, and feature guides
infra/                Optional Azure archive + AWS heartbeat monitor (Terraform)
```

Backend layering is summarized in [docs/backend-architecture.md](docs/backend-architecture.md).

## Quick start (local)

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

Copy `backend/.env.example` and `frontend/.env.example` for other settings.

## Tests

Install `backend/requirements-dev.txt` and `frontend/package.json` dependencies first.

```bash
pytest backend/tests/unit backend/tests/integration
npm test --prefix frontend
npm run build --prefix frontend
```

**Allure** (optional merged report): `npm run test:backend:allure`, `npm run test:frontend:allure`, `npm run test:e2e:allure`, then `npm run allure:report`.

**E2E / k3s smoke** need a browser cluster respectively — CI runs these in `.github/workflows/test-and-smoke.yml`.

## Kubernetes

Local no-registry flow: build images → import into k3d/k3s → `kubectl apply -k k8s`.

```bash
./scripts/deploy-k3s.sh k3d   # macOS
./scripts/deploy-k3s.sh k3s   # Ubuntu
```

Full steps, backup/restore, troubleshooting, LAN NodePort (`:30080`), and optional GHCR image pulls: [docs/kubernetes.md](docs/kubernetes.md).

Optional Prometheus/Grafana overlay: [docs/observability.md](docs/observability.md).

## LAN access

Other machines on your network should use `http://<host-ip>:3000` when Next binds `0.0.0.0` (`npm run dev:lan` or `./scripts/dev-lan.sh`). For k8s, use NodePort **`http://<node-ip>:30080`**. Details: [docs/local-development.md](docs/local-development.md#lan-access).

## Documentation

| Topic | Document |
| --- | --- |
| Product scope (retail investor) | [docs/product-scope.md](docs/product-scope.md) |
| Local setup, features, env vars | [docs/local-development.md](docs/local-development.md) |
| Kubernetes deploy, GHCR images, troubleshooting | [docs/kubernetes.md](docs/kubernetes.md) |
| Backend architecture | [docs/backend-architecture.md](docs/backend-architecture.md) |
| Structured logging | [docs/backend-logging.md](docs/backend-logging.md) |
| Multi-user / auth model | [docs/multi-user-design.md](docs/multi-user-design.md) |
| Azure EOD rankings archive (optional) | [docs/azure-rankings-archive.md](docs/azure-rankings-archive.md) |
| AWS outbound heartbeat monitor (optional) | [docs/aws-health-monitor.md](docs/aws-health-monitor.md) |
| k8s workload monitoring | [docs/observability.md](docs/observability.md) |
| Taiwan 資券 upstream endpoints | [docs/taiwan-margin-short-endpoints.md](docs/taiwan-margin-short-endpoints.md) |
| Taiwan market-data SonarCloud | [docs/taiwan-market-data-sonar-checklist.md](docs/taiwan-market-data-sonar-checklist.md) |

Taiwan market-data backend PRs must pass **SonarCloud** in addition to pytest — use the Sonar checklist above.
