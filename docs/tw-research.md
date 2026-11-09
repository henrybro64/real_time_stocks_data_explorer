# Taiwan equity research (#310–#312)

Industry-agnostic Taiwan stock research on the **existing** monorepo. No new service, database, Notion integration, schedule, or production deploy in this slice.

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
| `backend/app/services/tw_research/scan.py` | Bounded Stage A/B scan + ephemeral checkpoints |
| `backend/app/api/routes/tw_research.py` | Authenticated contract / universe / scan APIs |

## Indicator conventions (#311)

- Completed daily candles only (`moving_average.completed_daily_candles`).
- EMA seed = SMA of the first `period` values.
- OSC = DIF − Signal (not ×2).
- KD zero-range (H9 == L9): RSV = 50; K/D seed = 50; smoothing weight `1/3`.
- Monthly bars: calendar-month OHLCV; **current month excluded**.
- `daily_price_history_service` `MAX=800` is **not** silently treated as enough for long monthly MACD warm-up; insufficient history returns `unknown` / `insufficient_history`.
- Pass (day or month independently): `DIF > 0 AND OSC > 0 AND K > 50 AND D > 50`.

## Scan and checkpoints (#312)

- Stage A: daily MACD/KD for each symbol in the batch.
- Stage B: monthly + SMA20/60/120/240 + 52-week position for **day-pass** symbols only.
- Upstream month budget is enforced **before** history backfill via `get_daily_history_within_budget`; counters track actual month fetches, not Stage B local math.
- Indicator `now` is anchored to the scan `tradingDay` (after TW cash close). Overlapping batch POSTs for one scan are serialized with a per-scan lock and atomic cursor reservation.
- Checkpoint key: `(trading_day, universe_version, calculation_version, symbol)`.
- Checkpoints are **ephemeral (in-memory)**. They do **not** survive process restart. Do not claim restart-safe resume without a Postgres store.

### API

All routes use `Depends(get_effective_user_id)`.

| Method | Path | Upstream fetches? |
| --- | --- | --- |
| GET | `/api/tw-research/contract` | No |
| GET | `/api/tw-research/universe/starter-preset` | No |
| POST | `/api/tw-research/universe/resolve` | Catalog only |
| POST | `/api/tw-research/scans` | No (creates ledger) |
| POST | `/api/tw-research/scans/batches` | Yes, bounded |
| GET | `/api/tw-research/scans/{scan_id}` | No |

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
