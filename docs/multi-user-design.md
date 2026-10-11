# Multi-user design

**Short answer: yes, the app is multi-user internally, not just in the UI.** Every user-owned row carries a `user_id`, every user-owned route resolves the caller's id on the server, and every query filters by it. This holds in both modes: single-user mode is the same code path with one fixed user, not a bypass.

## The feature flag

| Setting | Login | Requests act as | Startup behavior |
| --- | --- | --- | --- |
| `AUTH_ENABLED=false` (default) | None | `default-user` | `seed_default_user()` inserts `users.id = "default-user"` if missing (`backend/app/db/init_db.py`). |
| `AUTH_ENABLED=true` + `LOCAL_AUTH_ENABLED=true` | Username/password, session cookie | The `users.id` stored in the session | Requires a non-placeholder `SESSION_SECRET` of 16+ characters. Placeholder bootstrap passwords and capture tokens are refused. If `LOCAL_AUTH_USERNAME` / `LOCAL_AUTH_PASSWORD` are set, creates that user or resets its password. |
| `AUTH_ENABLED=true` + `LOCAL_AUTH_ENABLED=false` | — | — | Startup fails: local auth is the only implemented login method. |

These checks are startup hooks in `backend/app/main.py` (`validate_auth_runtime_settings`). Local dev defaults to auth off; k8s enables auth via `k8s/backend.yaml` and a locally generated `k8s/backend-auth-secret.yaml` (see `.example`; never commit real secrets).

## How a request gets its user

`backend/app/api/deps.py`:

- `get_request_identity(request)`
  - auth off → always `default-user`;
  - auth on → `request.session["user_id"]`. If it is missing, or the user row no longer exists, the session is cleared and the request is anonymous.
- `get_effective_user_id(request)` returns that id or raises **401**. Every user-owned route depends on it.

The user id is **never** taken from the request body or query string; `test_forged_user_id_in_request_body_is_ignored` checks this.

Login endpoints (`backend/app/api/routes/auth.py`, under `/api/auth`):

| Endpoint | Behavior |
| --- | --- |
| `GET /status` | `authEnabled`, `loggedIn`, the user, and enabled providers. The UI uses it to decide whether to redirect to `/login`. |
| `POST /login/local` | Verifies a PBKDF2-SHA256 hash (120,000 iterations) and sets `session["user_id"]`. |
| `POST /register/local` | Creates a user and logs them in. Username 1–64 characters; password at least 8 characters (empty passwords rejected). Duplicate usernames return 400. |
| `POST /logout` | Clears the session. |

The session is Starlette's signed cookie (`SessionMiddleware`, `same_site="lax"`). `SESSION_COOKIE_SECURE` controls the Secure flag (default false for plain HTTP; set true behind HTTPS).

## User-scoped data

| Entity | Owner column | Ownership enforcement |
| --- | --- | --- |
| Watchlist | `watchlist_items.user_id` | unique `(user_id, stock_id)` |
| Portfolio positions | `portfolio_positions.user_id` | unique `(user_id, stock_id)` |
| Other assets | `portfolio_assets.user_id` | scoped CRUD |
| Portfolio value history | `portfolio_history_snapshots.user_id` | unique `(user_id, snapshot_hour, source_window)` |
| Realized trades | `realized_trades.user_id` | scoped CRUD (`/api/realized-trades`) |
| Alert rules and events | `alert_rules.user_id`, `alert_events.user_id` | scoped CRUD; the evaluator writes events for the rule's owner |
| AI preference | `user_ai_preferences.user_id` | one row per user |
| AI provider API keys | `user_api_keys.user_id` | unique `(user_id, provider)` |
| Stock-AI narrative cache | Redis key `stock_analysis:v1:{user_id}:...` | cache key includes the user |

User-owned tables use `UserOwnedRowMixin` (`backend/app/db/row_mixins.py`) or declare the same column: `user_id` is non-null, indexed, and a foreign key to `users.id` with `ON DELETE CASCADE`.

**Shared market data (session required when auth is on):** quotes, history, search, rankings, market snapshot, news, themes, WebSocket `/api/stream`, and Taiwan institutional / margin-short / ownership / profitability routes. The payload is the same for every user, but anonymous callers cannot spend the upstream rate-limit budget (#272). `GET /api/quotes` and `/api/stream` cap `ids` at `QUOTE_BATCH_IDS_MAX` (default 100) and accept only `US:` plus a ticker (optional `-` or `.` class suffix) or `TW:` plus 4–6 digits. With `AUTH_ENABLED=false`, these routes still resolve to the seeded `default-user` (single-user mode unchanged). The background quote refresher warms the **union** of all users' watchlist, portfolio, and alert symbols.

## Public vs protected routes

| Access | Routes |
| --- | --- |
| Always open | `/health`, `/api/auth/*` (status, login, logout, register) |
| Session required when `AUTH_ENABLED=true` | User-owned routes **and** shared market-data routes listed above |
| Capture token (not session) | `POST /api/portfolio/history/capture`, rankings snapshot/archive capture, Taiwan market-data capture |

## Enforcement layers

1. **Routes** — watchlist, portfolio (positions, assets, history), realized trades, alerts, AI settings, stock AI analysis, and shared market-data routes depend on `get_effective_user_id`.
2. **Services and repositories** — reads filter by `user_id`; updates and deletes match the resource id **and** the user, so a request cannot modify another user's row.
3. **Schema** — foreign keys, cascades, and per-user unique constraints.
4. **UI** — `AppChrome` redirects to `/login` when `authEnabled && !loggedIn`. This is a convenience; the backend's 401 is the real boundary.

Internal capture endpoints are the deliberate exception: they act for **all** users and are guarded by a shared header token instead of a session (constant-time compare). Examples are `POST /api/portfolio/history/capture` (called by the k8s CronJob against the backend Service, `PORTFOLIO_HISTORY_CAPTURE_TOKEN`) and the rankings / Taiwan market-data capture tokens. Each returns an error when its token is not configured. The Next.js `/api` rewrite does not proxy these capture paths.

## Tests that cover isolation

- `backend/tests/integration/api/test_multi_user_design.py` (Postgres): default-user bootstrap, anonymous 401, stale-session 401, per-session isolation of watchlist / portfolio / alerts, cross-user delete, forged `user_id` ignored, AI preference isolation.
- `frontend/tests/e2e/multi-user-isolation.spec.ts`: two browser sessions keep watchlist, portfolio, and alerts separate.

## Current limitations

- **Open registration.** With auth on, anyone who can reach the app can register. There are no roles, admin user, or invite flow.
- **No login rate limiting or lockout.**
- **Plaintext AI API keys.** `user_api_keys.api_key` is stored as plain text; responses only expose the last four characters.
- **Session cookie.** `SESSION_COOKIE_SECURE` defaults to false for HTTP; Starlette's default 14-day lifetime still applies. There is no server-side session store, so a single session cannot be revoked (rotating `SESSION_SECRET` logs everyone out).
- **Bootstrap password reset.** When `LOCAL_AUTH_USERNAME` / `LOCAL_AUTH_PASSWORD` are set, that user's password is reset to the configured value on every startup.
- **Switching modes does not migrate data.** Data created as `default-user` stays under that id after enabling auth, and `default-user` has no password, so nobody can log in as it.
- **Dormant OAuth table.** `oauth_identities` exists in the schema (migration `20260404_0004`), but no code reads or writes it.
- **Single replica assumed.** The alert evaluator uses in-process locks; see [architecture-overview.md](architecture-overview.md#architectural-tradeoffs).
- Saved layouts and dashboard settings are not persisted per user. New user-owned features must follow the same `user_id` + `get_effective_user_id` pattern.
