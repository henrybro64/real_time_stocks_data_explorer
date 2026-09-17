# Recurring maintenance cleanup

Cadence for [issue #236](https://github.com/henrybro64/real_time_stocks_data_explorer/issues/236): every **6–8 weeks**, or after a cluster of major feature PRs. Each pass should stay small, preserve behavior, and only remove clearly unused or obsolete code/docs.

## Latest pass

| Field | Value |
| --- | --- |
| Issue | #236 |
| Branch | `issue-236-recurring-cleanup` |
| Focus | Docs sync after TW ownership / profitability / key price levels; remove unused frontend client wrappers and dead shared-contract aliases; narrow module-private exports |
| Next suggested trigger | **~2026-11-01**, or sooner if another large feature cluster lands |

### Removed / shortened in this pass

| Item | Evidence |
| --- | --- |
| `updatePortfolioAsset`, `fetchAiInsight` in `frontend/src/services/api/client.ts` | No frontend callers |
| `SearchResult`, `QuotesResponse` in `packages/shared-contracts/api.ts` | Aliases unused; call sites use `Stock` / `QuoteResponse[]` |
| Unexported module-private helpers/constants in `client.ts`, `RankingsPage.tsx`, `twSignalsFormat.ts` | Still used in-file only |
| Docs updates for 籌碼 / 財報三率 / key price levels | Product, local-dev, architecture, Sonar checklist, PR template |

### Suggested PR summary

- Sync docs and Sonar checklist for ownership, profitability, and key price levels.
- Remove unused frontend wrappers and dead shared-contract aliases; narrow private exports.
- Record cadence / next trigger in this file.

**Verification (local, 2026-09-17):**

| Check | Result |
| --- | --- |
| `pytest backend/tests/unit` | Pass (news retry fixture uses relative `pubDate` so `window_days=1` stays valid across CI dates) |
| `pytest backend/tests/integration` | Skipped locally (no `DATABASE_URL`); covered in CI |
| `npm test --prefix frontend` | **382 passed** |
| `npm run build --prefix frontend` | Passed |


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
