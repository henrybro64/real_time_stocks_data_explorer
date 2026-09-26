# Recurring maintenance cleanup

Cadence for [issue #236](https://github.com/henrybro64/real_time_stocks_data_explorer/issues/236): every **6–8 weeks**, or after a cluster of major feature PRs. Each pass should stay small, preserve behavior, and only remove clearly unused or obsolete code/docs.

## Latest pass

| Field | Value |
| --- | --- |
| Date | 2026-09-26 |
| Branch | `feature/tw-limit-up-down-rankings` |
| Focus | Remove unused helpers left after the limit-ranking work and earlier features; sync rankings docs |
| Next suggested trigger | **~2026-11-15**, or sooner if another large feature cluster lands |

### Removed in this pass

| Item | Evidence |
| --- | --- |
| Module wrappers `fetch_twse_text` / `fetch_tpex_text` | Callers use `fetch_*_data` or `TaiwanMarketDataClient` methods |
| `TimedOperation` | No callers; `elapsed_ms` remains |
| Archive session-factory override and `reset_rankings_archive_for_tests` | Never called; tests use repository `set_session_factory_override` |
| `StockThemeService.get_themes_for_stock` | Only unit tests called it; routes use `list_themes` / `get_stocks_for_theme` |
| `isRankingsCopyKey`, stock-id price-tone helpers, legacy watchlist poll constants, unused e2e `searchFromPortfolio` | No production callers |

Docs now mention live-only Taiwan limit-up/limit-down rankings in product scope, local development, architecture, and the Sonar checklist.

**Verification (local, 2026-09-26):** `pytest backend/tests/unit` — 1211 passed. Frontend: price-tone, watchlist timing, and rankings page tests — 79 passed. Full frontend suite and production build were not re-run.

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

Do not delete these without a separate, justified API-removal change.

## PR checklist (each cleanup)

- What was removed / merged / shortened, with evidence it was unused or obsolete
- Questionable items kept and why (see table above when still accurate)
- Tests / builds / checks run
- Follow-ups deferred
- Update **Next suggested trigger** in this file
