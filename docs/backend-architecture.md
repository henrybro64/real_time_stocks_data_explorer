# Backend architecture

FastAPI backend layout for reviewers and new contributors.

## Repository map

| Path | Role |
| --- | --- |
| `backend/app/api/routes/` | HTTP handlers |
| `backend/app/services/` | Business logic |
| `backend/app/repositories/` | SQLAlchemy persistence |
| `backend/app/db/` | Models, sessions, migrations |
| `backend/app/schemas/` | API Pydantic types |
| `backend/app/core/` | Config, logging |
| `backend/app/jobs/` | Background worker startup (`runtime.py`) |

Shared TypeScript contracts: `packages/shared-contracts/`. Deploy manifests: `k8s/`.

## External integrations

| Area | Module |
| --- | --- |
| Quotes / search | `services/providers/` (`us_provider`, `tw_provider`) |
| AI analysis | `services/ai_providers/` |
| Rankings adapters | `services/ranking_data_providers.py` |

Routes call services; services call adapters — not raw provider HTTP from handlers.

## Background jobs

`jobs/runtime.py` starts and stops:

- portfolio history snapshots
- alert evaluation
- rankings snapshot capture
- Taiwan market-data refresh
- quote cache refresher (watchlist/portfolio warm path)

Implementations live in service modules; only orchestration is centralized.

**Quote refresher:** in-process with optional Redis leader lock when `replicas > 1`. With `QUOTE_REFRESHER_OWNS_UPSTREAM=true` and a live sampler, read paths prefer cache and request warm-up instead of per-request provider calls. Settings: `QUOTE_REFRESHER_*`, `QUOTE_REDIS_*`. Health exposes `quote_cache` diagnostics.

## Configuration

`backend/app/core/config.py` is shared by the app, Alembic, logging, and services.

## Persistence boundaries

User-owned data flows through repositories (`watchlist`, `portfolio`, `alert`, `ranking_snapshot`, `user`, `api_key`, etc.) so routes stay thin.

Taiwan market-data shares `taiwan_market_data.py` and `taiwan_history_scan.py`. 資券 upstream contracts: [taiwan-margin-short-endpoints.md](taiwan-margin-short-endpoints.md). Backend PRs in this area must also pass **SonarCloud** — see [taiwan-market-data-sonar-checklist.md](taiwan-market-data-sonar-checklist.md).

## Test session overrides

`alert_repository` and `ranking_snapshot_repository` support `set_session_factory_override()` for unit tests that monkeypatch `SessionLocal`. Other repositories use the shared `SessionLocal` from `repositories/session.py`.
