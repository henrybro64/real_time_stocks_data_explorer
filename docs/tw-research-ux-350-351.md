# TW Research UX — plain language + developer-copy cleanup (#350 / #351)

## Goals

- **#350:** Investor-first progress, honest status (not “running” when 0 processed), drain-aware next action, batch failure visibility, JSON collapsed under Advanced.
- **#351:** Remove Notion / GitHub issue numbers / CronJob / Agent-handoff jargon from default UI; keep capabilities and advanced diagnostics.

## Key UI pieces

| Piece | Path / test id |
| --- | --- |
| Status helpers | `frontend/src/views/research/researchStatusCopy.ts` |
| Summary tile | `ResearchInvestorSummary` → `tw-research-investor-summary` |
| Advanced disclosure | `AdvancedDetailsDisclosure` → `tw-research-advanced-details` |
| Copy | `frontend/src/views/research/twResearchI18n.ts` |
| Page wiring | `TwResearchPage.tsx` |

## Honesty rules (post architect review)

1. **Drain-aware next action:** Promise “wait for automatic collection” only when `firstPassDrain.armed && available` or `scheduleEnabled`. Otherwise use `not_started_manual`.
2. **All-failed overrides status + next action** (`all_failed`), not just a banner.
3. **Job binding:** Summary progress always matches the **selected** job; browsing a historical job shows a clear note and does not mix run-owned nested progress.
4. **Technical ids / raw JSON** live under Advanced (phase run/job ids, scan manifest JSON, latest manifest TextArea).

## Before → after (representative)

| Before | After |
| --- | --- |
| Fundamental-first research (#335–#337) | Fundamental-first research / 基本面優先研究 |
| Stock Ticker never writes Notion | never auto-publishes a trading report |
| CronJob remains suspended… | Progress is saved so you can reload later |
| Agent handoff | Latest verified research snapshot |
| “Wait for automatic collection” always | Auto only if drain armed; else manual-fallback copy |
| Run first-pass batch (primary-looking) | Manual batch (advanced fallback) + disabled reason |
| pending 0/73 shown like “running” | **尚未開始處理 / Not started yet** |
| HTTP 200 + 10 fails quiet | **本批沒有可用的驗證資料…** + all_failed next action |

## Tests

```bash
cd frontend && npm test -- --run \
  src/views/research/researchStatusCopy.test.ts \
  src/views/research/ResearchInvestorSummary.test.tsx \
  src/views/research/TwResearchPage.test.tsx
```

Optional Playwright:

```bash
PLAYWRIGHT_MOCKS_ONLY=1 npx playwright test \
  tests/e2e/tw-research-tabs.spec.ts \
  tests/e2e/tw-research-handoff.spec.ts
```

## Safety

- No API schema / digest changes
- Analyst gates unchanged
- CronJobs remain untouched (UI consumes existing `firstPassDrain`)
- Raw JSON still available under Advanced
