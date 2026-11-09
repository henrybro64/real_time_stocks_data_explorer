# Taiwan equity research (#310–#320)

Industry-agnostic Taiwan stock research on the **existing** monorepo. No new service, Notion SDK, schedule, or production deploy in this slice. Persistence reuses the **existing** Stock Ticker PostgreSQL (#318).

## Status of related closed work

Confirm and reuse — do not reimplement:

| Issue | Already in tree |
| --- | --- |
| #269 | Persisted daily bars (`daily_price_history_service`, `daily_price_bars`) |
| #271 | TW MIS quote batching (`TWProvider.get_quotes_batch`, 20 symbols/chunk) |
| #272 | Market-data routes require session when auth is on; read paths must not unbounded-fetch |
| #274 | Related history/chart optimizations |

## Product rules

- Any catalog **TWSE/TPEx** symbol is eligible (餐飲, 航空, finance, unclassified, …).
- Theme/sector tags are **optional metadata**. Missing a theme is not an error and not a rejection reason.
- The 14-sector / ~80-stock list is an **optional starter preset** (`UNIVERSE_PRESET_VERSION`). It is not a hard universe cap and must not appear as an authorization limit.
- `target_count` 10|20 is an **output target only**. It never relaxes pass/fail rules to fill slots.
- Scan batch size **10** is independent of MIS quote chunk size **20**.

## Modules

| Path | Role |
| --- | --- |
| `backend/app/services/tw_research/preset.py` | Optional TW sector themes + assignments (extends `stock_theme_service`) |
| `backend/app/services/tw_research/universe.py` | Universe selector: starter preset / theme keys / explicit IDs |
| `backend/app/services/tw_research/contract.py` | Versioned field/source map |
| `backend/app/services/tw_research/indicators.py` | Daily + monthly MACD(12,26,9) and KD(9,3,3) |
| `backend/app/services/tw_research/scan.py` | Bounded Stage A/B scan + enrichment batches + durable checkpoints |
| `backend/app/services/tw_research/coverage.py` | OHLCV + TW calendar coverage / readiness (#320) |
| `backend/app/services/tw_research/persistence.py` | Scan/symbol/snapshot helpers over Postgres (#318/#319) |
| `backend/app/repositories/tw_research_repository.py` | SQLAlchemy repository for scans / symbols / snapshots |
| `backend/app/services/tw_research/enrichment.py` | Day+month qualifier enrichment with per-field provenance (#313) |
| `backend/app/services/tw_research/export.py` | Manifest / chunked JSON / CSV / browser text / digests (#314/#319) |
| `backend/app/api/routes/tw_research.py` | Authenticated contract / universe / scan / enrich / export APIs |
| `frontend/src/app/research/tw/page.tsx` | Private research UI (`/research/tw`) |
| `docs/tw-research-pilot.md` | #315 measurement + Agent handoff worksheet |
| Alembic `20261109_0021` | `tw_research_scans`, `tw_research_symbol_results`, `tw_research_export_snapshots` |

## Indicator conventions (#311)

- Completed daily candles only (`moving_average.completed_daily_candles`).
- EMA seed = SMA of the first `period` values.
- OSC = DIF − Signal (not ×2).
- KD zero-range (H9 == L9): RSV = 50; K/D seed = 50; smoothing weight `1/3`.
- Monthly bars: calendar-month OHLCV; **current month excluded**.
- `daily_price_history_service` `MAX=800` is **not** silently treated as enough for long monthly MACD warm-up; insufficient history returns `unknown` / `insufficient_history`.
- Pass (day or month independently): `DIF > 0 AND OSC > 0 AND K > 50 AND D > 50`.

## Scan, checkpoints, and readiness (#312 / #318 / #320)

- Stage A: daily MACD/KD for each symbol in the batch.
- Stage B: monthly + SMA20/60/120/240 + 52-week position for **day-pass** symbols only.
- Upstream month budget is enforced **before** history backfill via `get_daily_history_within_budget`; counters track actual month fetches, not Stage B local math.
- Enrichment levels use **stored-only** `peek_daily_history` by default (no cold backfill). Stage B `week52_position` is reused when the store peek is empty.
- Indicator `now` is anchored to the scan `tradingDay` (after TW cash close). Overlapping batch POSTs use a per-scan lock **and** durable leased reservations (`cursor` completed watermark + `reserve_cursor` / `reserve_expires_at`) under `SELECT … FOR UPDATE`. Stale `upsert_scan` writes never regress cursor/lease/progress counters.
- Checkpoint key: `(trading_day, universe_version, calculation_version, symbol, scan_id)`.
- Checkpoints and ledgers are **durable in PostgreSQL** when `DATABASE_URL` is configured (Alembic `20261109_0021`). In-memory maps are a process cache; workers hydrate from Postgres before each batch. Persist failures mark the scan `failed` when DB is configured.
- Scan lifecycle: `queued` → `running` → `partial` | `budget_exhausted` | `completed_processing` → `verified_ready` | `failed`.
- Per-symbol readiness: `processed` | `technically_qualified` | `source_incomplete` | `verified_ready` | `rejected`.
- OHLCV validation uses the Taiwan trading calendar. Each batch runs a bounded calendar preflight (`preload_tw_holiday_calendars`); unknown years fail closed as `trading_calendar_unavailable` (never invent weekday sessions). History must reach the scan trading day (`expect_through`).
- `completed_processing` means batches finished. Scan `quality_status=technically_qualified` means the ledger is clean enough to finalize (including **zero** qualifying stocks). Scan `verified_ready` is set **only after** a successful immutable snapshot finalize.
- All scan and snapshot routes are **user-scoped** (no global latest-manifest fallback).
- Scan-scoped export GETs may serve a **live** ledger for the private UI. Agents should use `GET /exports/latest/manifest` (and snapshot chunk routes) for verified, digest-stable data.
- Retention helper: `persistence.retention_cleanup(retention_days=30)` deletes aged snapshots first, then aged scans. Snapshot `scan_id` uses `ON DELETE SET NULL` so verified exports survive scan cleanup. Rough storage: one scan row + N symbol JSON payloads + optional finalized snapshot (~tens of KB to a few MB per verified export depending on universe size).

## Deterministic universe + immutable exports (#319)

- Explicit / theme universes use SHA-256 digests of canonical sorted IDs (no Python `hash()`). Theme versions stay within `String(128)` as `themes:{count}:{digest16}`.
- Finalized `content_digest` and chunk checksums hash research content only (no timestamps or scan/snapshot UUIDs). Identical content → identical digests across reruns; uniqueness is per `(user_id, content_digest)`.
- Latest verified manifest: `GET /api/tw-research/exports/latest/manifest?universeVersion=&tradingDay=` (authenticated; **this user’s** snapshots only).

### API

All routes use `Depends(get_effective_user_id)`.

| Method | Path | Upstream fetches? |
| --- | --- | --- |
| GET | `/api/tw-research/contract` | No |
| GET | `/api/tw-research/universe/starter-preset` | No |
| POST | `/api/tw-research/universe/resolve` | Catalog only |
| POST | `/api/tw-research/scans` | No (creates ledger) |
| POST | `/api/tw-research/scans/batches` | Yes, bounded |
| POST | `/api/tw-research/scans/enrichment-batches` | Yes, bounded (#313) |
| GET | `/api/tw-research/scans/{scan_id}` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/manifest` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/chunk` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/csv` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/browser-text` | No |
| GET | `/api/tw-research/exports/latest/manifest` | No (#319) |
| GET | `/api/tw-research/exports/snapshots/{snapshot_id}/chunk` | No (#319) |

## Baseline / instrumentation plan (#310 → #315)

Code establishes the contract and budgets. **Actual Ubuntu/k3s headroom** (CPU, RSS, disk, DB/Redis, HTTP counts, latency) is measured during the manual pilot (#315), not blocked on unavailable host metrics here.

Suggested pilot thresholds to record on the home node:

1. Cold resolve of starter preset (unique count, rejected reasons).
2. Manual scan batches of 10 → 20 → 40 symbols, then full preset across multiple batches.
3. Peak RSS, wall time, upstream month fetches, UI responsiveness during batch POST.
4. Confirm GET snapshot does not increase upstream HTTP counts.
5. Include out-of-preset symbols: e.g. 餐飲 `TW:2727`, 航空 `TW:2618`, unclassified `TW:2330` (if not in preset).

## Extensibility acceptance

Selecting a valid TW symbol **outside** the starter 14 sectors / ~80 list must work with the same schema and no code change: use `mode=stock_ids` on resolve/create-scan.
