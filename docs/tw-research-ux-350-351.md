# TW Research UX — plain language + developer-copy cleanup (#350 / #351)

## Goals

- **#350:** Investor-first progress, honest status (not “running” when 0 processed), drain-aware next action, batch failure visibility, JSON collapsed under Advanced.
- **#351:** Remove Notion / GitHub issue numbers / CronJob / Agent-handoff jargon from default UI; keep capabilities and advanced diagnostics.

## Key UI pieces

| Piece | Path / test id |
| --- | --- |
| Status helpers | `frontend/src/views/research/researchStatusCopy.ts` |
| Error copy | `frontend/src/views/research/researchErrorCopy.ts` |
| Summary tile | `ResearchInvestorSummary` → `tw-research-investor-summary` |
| Advanced disclosure | `AdvancedDetailsDisclosure` → `tw-research-advanced-details` |
| Copy | `frontend/src/views/research/twResearchI18n.ts` |
| Page wiring | `TwResearchPage.tsx` |

## Honesty rules

1. **Live lease vs paused:** `collecting_now` only when `leaseActive` (and data is not stale). Unfinished jobs without a lease are `paused_auto` / `paused_auto_stale` / `paused_manual`.
2. **Drain-aware next action:** Promise automatic collection only when `firstPassDrain.armed && available` (legacy `scheduleEnabled` only if `firstPassDrain` is absent).
3. **All-failed is terminal only:** Cumulative `all_failed` only when the job is terminal with zero usable rows. Unfinished failure-only jobs stay in a paused/collecting bucket with retry copy.
4. **Per-batch banner:** Uses `firstPassBatchMutation.data` (`batchCounts` / `batchIds`) only — never cumulative job counts.
5. **Job binding:** Summary progress matches the **selected** job; browsing a historical job uses date labels (ids under Advanced).
6. **Technical ids / raw JSON** live under Advanced.

## Additive progress API fields (#350)

`GET /api/tw-research/first-pass/jobs/{id}/progress` (and `FirstPassJobService.progress()`) now also returns:

| Field | Meaning |
| --- | --- |
| `leaseActive` | `reserve_expires_at` is set and still in the future (UTC) |
| `leaseExpiresAt` | ISO lease expiry or null |
| `updatedAt` | Job `updated_at` ISO |
| `nextRetryAt` | Earliest symbol-row `nextRetryAt` still scheduled (ISO or null) |

Repository exposes `reserve_expires_at` on the job dict and `earliest_first_pass_next_retry_at(job_id)` (one aggregate JSON query). No export schema / digest changes.

## Before → after (wording matrix)

| Before | After (en / zh-TW) |
| --- | --- |
| Fundamental-first research (#335–#337) | Fundamental-first research / 基本面優先研究 |
| Stock Ticker never writes Notion | never auto-publishes a trading report |
| CronJob remains suspended… | Progress is saved so you can reload later |
| Agent handoff | Latest verified research snapshot |
| “Collecting” while lease gone | **Paused: no collection is running** / **目前暫停：沒有正在執行的收集** |
| “Running” with 0 processed | **Not started yet** / **尚未開始處理** |
| Single “collecting” bucket | **Collecting data (this batch in progress)** / **正在收集資料（本批進行中）** |
| — | **Waiting for the next automatic collection (last run {time})** / **等待下一次自動收集（上次執行 {time}）** |
| — | **Automatic collection is on, but there is no recent run record** / **自動收集已啟用，但最近沒有執行紀錄** |
| Raw ISO next retry | Taipei-local formatted time via `Intl.DateTimeFormat` |
| Quiet HTTP 200 all-failed batch | **本批 {n} 檔資料收集失敗…** + plain-language reasons |
| Job id in default summary | **目前研究（建立於 {date}）** / date-based browsing note |
| chipEvidenceMeta with snapshot id | **第 {from}–{to} 筆，共 {total} 筆** (id in Advanced) |
| candidateProvenanceLine with ids/digest | **已核准 {count} 檔進一步分析的股票** (ids/digest in Advanced) |
| Generic 503/409 copy | Mapped titles + next actions in `researchErrorCopy.ts` |
| Stale refetch silent | **以下為最後已知狀態（{time}），可能已過時** |
| Progress bar unlabeled | aria-label **第一輪資料收集進度 {pct}%** |

## Tests

```bash
cd frontend && npx tsc --noEmit \
  && npx vitest run src/views/research \
  && npm run build

cd backend && .venv/bin/python3.11 -m pytest \
  tests/unit/services/tw_research/test_first_pass_progress_lease_350_351.py -q
```

Scenario / copy / polling coverage: `TwResearchPageStatus.test.tsx`, `researchStatusCopy.test.ts`, `researchErrorCopy.test.ts`, `ResearchInvestorSummary.test.tsx`.

Optional Playwright (existing smoke only; new buckets are unit-covered):

```bash
PLAYWRIGHT_MOCKS_ONLY=1 npx playwright test \
  tests/e2e/tw-research-tabs.spec.ts \
  tests/e2e/tw-research-handoff.spec.ts
```

## Safety

- No export JSON schema / digest / raw JSON content changes
- Analyst gates unchanged
- CronJobs / k8s / production config untouched
- Raw JSON and technical ids still available under Advanced
