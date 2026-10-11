# Recurring maintenance cleanup

Cadence for [issue #236](https://github.com/henrybro64/real_time_stocks_data_explorer/issues/236): every **6–8 weeks**, or after a cluster of major feature PRs. Each pass should stay small, preserve behavior, and only remove clearly unused or obsolete code/docs.

## Latest pass

| Field | Value |
| --- | --- |
| Date | 2026-10-07 |
| Branch | `maintenance-cleanup-2026-10` |
| Focus | Remove dead backend helpers and frontend exports found by `vulture` and `knip`, each confirmed by a repo-wide search |
| Next suggested trigger | **~2026-12-01**, or sooner if another large feature cluster lands |

### Removed in this pass

| Item | Evidence |
| --- | --- |
| `TaiwanMarketDataClient.fetch_twse_text` / `fetch_tpex_text` and the client's text-response path | No production callers; only tests used them. Every caller uses `fetch_*_data` (JSON). The cache-key test now covers the JSON path |
| `RankingsArchiveService.any_settle_window_open` | Deprecated alias for `any_active_capture_window`; no callers |
| `ArchiveCaptureTarget`, `RankingsArchiveService._provider_date_matches_target` | No references anywhere |
| `RankingsExternalSource._read_cache` | No callers; `_read_cache_entry` remains |
| `parse_tdcc_shareholding_rows` and its private row parsers | Production parses TDCC CSV and the QryStock HTML page only; the row parser was test-only |
| `trim_moving_averages` | Test-only; `history_chart_service` aligns averages to the visible candles with `align_moving_averages_to_candles`. The warm-up assertion is kept against `build_moving_averages` |
| `NewsService.select_for_analysis`, `fx_service._fetch_usd_twd_rate`, `tw_profitability_service._map_row_label` | One-line wrappers with no production callers; tests call the underlying functions |
| Frontend `formatDateOnly` and the `isNullQuoteWarming` / `resolveWarmingTimeoutState` re-exports from `watchlistQuoteQuery.ts` | Unused per `knip`; callers import from `services/market-data/quoteWarming` directly |

No docs were removed: every doc is linked, and their file paths and settings were checked against the code.

**Verification (local, 2026-10-07):** `pytest backend/tests/unit` — 1330 passed (3 tests removed with the dead code); frontend `vitest` 425 passed; `tsc --noEmit` and `npm run build` passed. Integration tests skipped without `DATABASE_URL`.

### Previous pass (2026-09-26)

Removed module wrappers `fetch_twse_text` / `fetch_tpex_text`, `TimedOperation`, an unused archive session-factory override, `StockThemeService.get_themes_for_stock`, and unused frontend price-tone / watchlist helpers. Verification: unit pytest 1211 passed; targeted frontend tests 79 passed.

### Previous pass (#236, 2026-09-17)

Removed unused `updatePortfolioAsset` / `fetchAiInsight` wrappers and dead `SearchResult` / `QuotesResponse` aliases. Synced ownership, profitability, and key-price-level docs.

**Verification (local, 2026-09-17):** unit pytest passed; integration skipped without `DATABASE_URL`; frontend **382** tests and `npm run build` passed.


## Intentionally kept (look unused in UI)

These remain part of the public backend/API surface even though the current UI does not call them:

| Surface | Why keep |
| --- | --- |
| `PATCH /api/portfolio/assets/{id}` (+ `PortfolioAssetUpdate` contracts) | Live update API; assets UI is create/delete-only today |
| `GET /api/ai/insights/{stock_id}` (+ `AiInsightResponse` contracts) | Live insight endpoint; stock detail uses `/analysis` instead |
| Shared-contract response subtypes used only as nested fields | Compose imported response types; not dead |
| `oauth_identities` table / `OAuthIdentity` model | Used by Entra External ID (#240); keep |
| `/api/stream` WebSocket | Unused by the frontend (it polls), but documented in product scope as a cache-first push API for other clients |
| Test seams: `ready_trading_date`, `get_rankings_archive_engine`, `get_cached_quote`, `*_for_tests` resets | Production uses the underlying logic; these keep that logic directly testable |
| `RankingsArchiveService.preview_retention_purge` | Documented operator-shell tool (#188), not called by the app by design |
| `allure`, `allure-playwright`, `allure-vitest` dev dependencies | `knip` reports them unused, but Vitest/Playwright reporter config and the CI Allure job load them |

Do not delete these without a separate, justified API-removal change.

## PR checklist (each cleanup)

- What was removed / merged / shortened, with evidence it was unused or obsolete
- Questionable items kept and why (see table above when still accurate)
- Tests / builds / checks run
- Follow-ups deferred
- Update **Next suggested trigger** in this file
