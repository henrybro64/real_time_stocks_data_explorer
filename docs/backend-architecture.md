# Backend Architecture

This repository now separates backend concerns so a reviewer can find the main layers quickly.

## Top-Level Map

- `frontend/`: Next.js UI
- `backend/`: FastAPI app, backend tests, migrations, scripts, and Redis dev config
- `docs/`: project and backend documentation
- `k8s/`: deployment manifests and local cluster infra
- `packages/shared-contracts/`: shared TypeScript API contracts

Equivalent locations for the original issue's target structure:

- `jobs/` equivalent: `backend/app/jobs/`
- `db/` equivalent: `backend/app/db/`
- `tests/` equivalent: `backend/tests/` and `frontend/tests/`
- `infra/` equivalent: `k8s/` and `.github/workflows/`

## Backend Layering

- `backend/app/api/routes/`: thin HTTP route handlers and request/response shaping
- `backend/app/services/`: business logic and orchestration
- `backend/app/repositories/`: SQLAlchemy data access and persistence boundaries
- `backend/app/db/`: ORM models, engine/session setup, and DB bootstrap helpers
- `backend/app/schemas/`: API-facing Pydantic schemas
- `backend/app/models/`: internal domain/record models used between services
- `backend/app/core/`: shared config, diagnostics, and logging
- `backend/app/middleware/`: cross-cutting HTTP middleware

## External Integrations

External provider calls are isolated behind adapter-style modules:

- stock/quote/search providers: `backend/app/services/providers/`
- AI providers: `backend/app/services/ai_providers/`
- rankings provider registry/adapters: `backend/app/services/ranking_data_providers.py`

Services consume those adapters instead of embedding provider-specific HTTP code in routes.

## Background Execution

Background worker startup and shutdown live in `backend/app/jobs/runtime.py`.

The runtime currently manages:

- portfolio history snapshot sampling
- alert rule evaluation
- rankings snapshot capture
- Taiwan market-data refresh
- quote cache refresher (watchlist/portfolio warm path)

The worker implementations remain in their corresponding service modules, while startup orchestration lives in one defined jobs location.

The quote refresher runs inside the backend process (same pattern as the other samplers). With `replicas: 1` this is sufficient; when Redis is configured, a per-cycle Redis leader lock (fail-closed if Redis is down) ensures only one replica executes each refresh cycle. Configure cadence via `QUOTE_REFRESHER_*` settings. Prefer provider safety: open-market defaults are tens of seconds, not sub-second polling.

When `QUOTE_REFRESHER_OWNS_UPSTREAM=true` (default) **and the sampler thread is alive**, watchlist batch, portfolio, and stock-detail read paths serve Redis/memory cache and do not call providers directly; they request an immediate refresher warm-up on the shared bounded pool instead. If the sampler is not running, request paths fall back to on-demand fetches. Rankings (`get_quotes` with `allow_stale=False`) may still fetch on demand. Closed-market symbols continue to refresh on the closed cadence even while another market is open. Per-provider spacing (`QUOTE_REFRESHER_MIN_INTERVAL_SECONDS`) and a cycle deadline (`QUOTE_REFRESHER_CYCLE_TIMEOUT_SECONDS`) keep free-tier providers from being stampeded.

### Quote Redis cache resilience

`backend/app/services/quote_redis_cache.py` keeps Redis optional. When `REDIS_URL` is unset, quotes use the in-process memory cache. When Redis is configured, connect and runtime connection/timeout failures invalidate the client and schedule a reconnect after exponential backoff with jitter (`QUOTE_REDIS_RETRY_BASE_SECONDS`, `QUOTE_REDIS_RETRY_MAX_SECONDS`, `QUOTE_REDIS_RETRY_JITTER_SECONDS`). Concurrent reconnect attempts singleflight inside each process. Soft TTL marks freshness; hard TTL retains stale payloads for SWR. `/health` exposes a nested `quote_cache` diagnostic object (configured/connected/retry state, last ping/read/write success times, last error category) without Redis credentials or the URL.

## Configuration

Application configuration is centralized in `backend/app/core/config.py`.

That module is reused by:

- FastAPI app startup in `backend/app/main.py`
- logging setup in `backend/app/core/logging.py`
- Alembic migration config in `backend/alembic/env.py`
- provider and background-job configuration throughout services

## Current Repository Boundaries

The DB-backed service domains now route persistence through repositories such as:

- `watchlist_repository.py`
- `portfolio_repository.py`
- `alert_repository.py`
- `portfolio_history_repository.py`
- `ranking_snapshot_repository.py`
- `user_repository.py`
- `api_key_repository.py`
- `user_ai_preference_repository.py`

This keeps route handlers thin, keeps business logic in services, and keeps SQL/ORM access in one layer.

Taiwan market-data services (institutional, 資券, daily refresh/store) share helpers under `taiwan_market_data.py` and `taiwan_history_scan.py`. Official 資券 endpoint contracts and failure classification are documented in [taiwan-margin-short-endpoints.md](taiwan-margin-short-endpoints.md). PRs in that area must also clear SonarCloud — see [taiwan-market-data-sonar-checklist.md](taiwan-market-data-sonar-checklist.md).

## Repository Session Wiring

Most repositories resolve the SQLAlchemy session factory through `backend/app/repositories/session.py`, which reads `SessionLocal` from `backend/app/db/session.py`.

Two repositories also support an optional `set_session_factory_override()` hook for unit tests that monkeypatch `SessionLocal` on the service module:

- `alert_repository.py`
- `ranking_snapshot_repository.py`

Those services call `_sync_repository_sessionlocal()` before repository access so the override tracks the same in-memory session factory used by existing service tests. Other repositories (`watchlist`, `portfolio`, `user`, and so on) rely on the shared `SessionLocal` binding and are covered by service or integration tests.

## Intentionally Unchanged Areas

This refactor focused on DB-backed persistence boundaries. The following areas were already adapter-based or out of scope for the first pass:

- quote/search provider adapters in `backend/app/services/providers/`
- rankings external fetch logic in `backend/app/services/rankings_service.py` and `ranking_data_providers.py`
- background worker implementations (still in their service modules; only startup orchestration moved to `jobs/runtime.py`)

