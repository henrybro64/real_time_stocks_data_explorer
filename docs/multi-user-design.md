# Multi-User Design

## Goal

The backend should enforce ownership in the data model and in every user-owned access path, regardless of whether the application is running in:

- single-user mode (`AUTH_ENABLED=false`)
- session-based multi-user mode (`AUTH_ENABLED=true`)

This document describes the current ownership model, how request identity is resolved, and what the feature flag changes.

## Identity Model

### `AUTH_ENABLED=false`

Single-user mode does not use login sessions. Protected routes resolve to one explicit system principal:

- user id: `default-user`
- source of truth: `backend/app/db/init_ids.py`
- bootstrap path: backend startup calls `seed_default_user()` when a database session is available

This is an intentional compatibility mode for local development and demos. The important design choice is that the backend still goes through the same ownership columns and user-scoped queries used in multi-user mode. The app does not bypass ownership checks just because auth is off.

### `AUTH_ENABLED=true`

Multi-user mode uses the session cookie stored by the `/api/auth/login/local` and `/api/auth/register/local` endpoints.

Request identity is resolved in `backend/app/api/deps.py`:

1. Read `request.session["user_id"]`
2. Re-check that the referenced `users.id` row still exists
3. Clear the session and reject the request with `401` if the session is missing or stale

This prevents a deleted or invalid user id from continuing to access user-owned routes through an old cookie.

## Ownership Model

The application currently stores the following user-owned entities.

| Entity | Storage model | Explicit owner | Enforcement |
| --- | --- | --- | --- |
| User | `users` | primary key `users.id` | root identity record |
| Watchlist | conceptual collection | `watchlist_items.user_id` on every row | route dependency + user-scoped queries |
| Watchlist item | `watchlist_items` | `user_id -> users.id` FK | unique `(user_id, stock_id)`, user-scoped insert/delete/list |
| Portfolio | conceptual collection | `portfolio_positions.user_id` and `portfolio_assets.user_id` | route dependency + user-scoped queries |
| Holding / position | `portfolio_positions` | `user_id -> users.id` FK | unique `(user_id, stock_id)`, user-scoped CRUD |
| Portfolio asset | `portfolio_assets` | `user_id -> users.id` FK | user-scoped CRUD |
| Portfolio history snapshot | `portfolio_history_snapshots` | `user_id -> users.id` FK | unique `(user_id, snapshot_hour, source_window)`, user-scoped history queries |
| AI provider API key | `user_api_keys` | `user_id -> users.id` FK | unique `(user_id, provider)`, user-scoped get/upsert |
| OAuth identity mapping | `oauth_identities` | `user_id -> users.id` FK | unique `(provider, subject)` |

## Access Control Enforcement

Ownership is enforced at three layers.

### 1. Route dependency

All user-owned routes depend on `get_effective_user_id()`:

- `backend/app/api/routes/watchlist.py`
- `backend/app/api/routes/portfolio.py`
- `backend/app/api/routes/ai.py`

That keeps the request identity decision in one place instead of repeating auth logic per route.

### 2. User-scoped queries and mutations

All service methods that operate on user-owned data filter by `user_id`:

- `WatchlistService`
- `PortfolioService`
- `ApiKeyService`

Updates and deletes use both the resource id and `user_id`, so one user cannot mutate another user's record by guessing an integer id.

### 3. Database constraints

The schema also enforces ownership structurally:

- foreign keys from child rows to `users.id`
- `ON DELETE CASCADE` so user-owned rows are removed with the owning user
- uniqueness constraints scoped by `user_id` where needed

This means the application is not relying only on frontend behavior or API conventions.

## Feature Flag Behavior

| Setting | Identity source | Login required | User-owned routes run as | Notes |
| --- | --- | --- | --- | --- |
| `AUTH_ENABLED=false` | seeded system principal | no | `default-user` | single-user compatibility mode |
| `AUTH_ENABLED=true` and `LOCAL_AUTH_ENABLED=true` | session cookie -> `users.id` | yes | authenticated session user | stale sessions are rejected |

## Summary

The current backend is multi-user by data model and by access path for the user-owned entities that exist today. Single-user mode is still supported, but it now uses an explicit and documented system principal instead of a hidden setup assumption.
