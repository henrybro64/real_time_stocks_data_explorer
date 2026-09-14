# Multi-user design

The backend enforces ownership in the data model and on every user-owned route, in both single-user and session auth modes.

## Identity

| Mode | Setting | Resolved user |
| --- | --- | --- |
| Single-user | `AUTH_ENABLED=false` | Seeded `default-user` (`backend/app/db/init_ids.py`; startup `seed_default_user()`) |
| Multi-user | `AUTH_ENABLED=true` | Session cookie → `users.id` via `get_effective_user_id()` in `backend/app/api/deps.py` |

Compatibility mode still uses the same `user_id` columns and scoped queries — auth off does **not** bypass ownership checks.

Stale sessions: if `request.session["user_id"]` is missing or the user row was deleted, the session is cleared and the request gets `401`.

## User-owned entities

| Entity | Owner column | Enforcement |
| --- | --- | --- |
| Watchlist items | `watchlist_items.user_id` | FK + unique `(user_id, stock_id)` |
| Portfolio positions/assets | `portfolio_positions.user_id`, `portfolio_assets.user_id` | scoped CRUD |
| Portfolio history | `portfolio_history_snapshots.user_id` | scoped queries |
| Realized trades | `realized_trades.user_id` | scoped CRUD (`/api/realized-trades`) |
| Alerts / events | `alert_rules.user_id`, `alert_events.user_id` | scoped CRUD + evaluation |
| AI prefs / API keys | `user_ai_preferences.user_id`, `user_api_keys.user_id` | scoped get/upsert |
| OAuth link | `oauth_identities.user_id` | unique `(provider, subject)` |

Saved layouts/dashboard settings are not persisted today. New features must follow the same explicit `user_id` pattern.

## Enforcement layers

1. **Routes** — watchlist, portfolio, alerts, realized trades, AI depend on `get_effective_user_id()`.
2. **Services** — all queries/mutations filter by `user_id` (updates match resource id **and** user).
3. **Schema** — FKs to `users.id`, `ON DELETE CASCADE`, user-scoped uniqueness.

## Feature flags

| Setting | Login | Acts as |
| --- | --- | --- |
| `AUTH_ENABLED=false` | No | `default-user` |
| `AUTH_ENABLED=true` + `LOCAL_AUTH_ENABLED=true` | Yes (`/api/auth/login/local`, register) | Session user |
