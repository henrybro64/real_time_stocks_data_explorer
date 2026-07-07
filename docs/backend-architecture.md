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

The worker implementations remain in their corresponding service modules, while startup orchestration lives in one defined jobs location.

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

