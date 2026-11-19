# First-Pass Post-D1 data quality (#362)

**Status:** code fix for independent review. Do **not** enable CronJobs, write
production PostgreSQL, or start Stage D2 / Phase-2 from this document alone.

Companion: Stage D packaging (`docs/tw-research-first-pass-cronjob-stage-d-362.md`),
executor notes (`docs/tw-research-first-pass-executor-362-366.md`).

## Findings (confirmed vs corrected)

| # | Claim | Verdict |
|---|--------|---------|
| 1 | `_fetch_official_pe_json` returns `None` on HTTP/429/timeout/JSON | **Corrected:** it raises `_PeFetchTransient`. Snapshot callers previously swallowed that into `None`. |
| 2 | `official_pe_unavailable` conflates absence with transient failure | **Confirmed** (pre-fix). |
| 3 | Partial symbols without `nextRetryAt` | **Confirmed** for non-retryable PE gaps; transient PE now becomes `failed` + `nextRetryAt`. |
| 4 | Blank official P/E cell (e.g. TW:3363 class) | **Confirmed** path: published row with `peStatus=missing` → authoritative gap, non-retryable partial. |
| 5 | TPEx omits P/B and dividend yield | **Confirmed.** Live `peQryDate` fields (2026-11-19): `殖利率(%)`, `股價淨值比` (no close). Parser now maps them. |
| 6 | Provider budget/diagnostics not durable | **Confirmed** (log-only). Symbol payloads now store bounded `providerDiagnostics`. |
| 7 | Nested `firstPassProgress` can go stale | **Confirmed** when only counts changed while phase stayed `running`. Reconcile now persists on progress delta. |

## Semantics after this fix

- **Transient provider failure** (timeout / 429 / 5xx / network / invalid day JSON):
  `valuationWarning` uses `transient_*` / `rate_limited:*` / `provider_validation:*` →
  field retryable → symbol **`failed`** with bounded `nextRetryAt` (max 4 attempts).
- **Authoritative missing / blank PE** on a published day row: non-retryable
  warning including `peStatus=…` → symbol **`partial`**, no auto-retry.
- **OK fields** are preserved across retries (`_merge_preserved_ok_fields`).
- **Duplicates / leases:** unchanged unique `(job_id, stock_id)` upsert + lease
  generation fencing; reclaim never invents market data.

## Safe post-merge recovery for existing 13 rows (operator)

Do **not** run until this PR is approved/merged and Stage D2 is separately authorized.

1. **Read-only baseline** (names/hashes only; never print tokens):
   ```bash
   ./scripts/tw-first-pass-preflight.sh k3s
   ```
   Confirm both CronJobs still `suspend=true`, counts 13/73 (9 ok / 4 partial / 0 failed).

2. **Classify the 4 partials** with read-only SQL (in-cluster postgres):
   - Keep as partial if `official_pe.reason` shows blank/missing publication.
   - Mark for revalidation only when root cause was unknown / likely transient
     (no `valuationAsOf`, reason exactly `official_pe_unavailable`, or operator
     judgment after reviewing `providerDiagnostics` if present).

3. **Mark reclaim without discarding OK fields** (explicit CONFIRM; example):
   ```sql
   -- REVIEW FIRST. Sets revalidatePartial so claim will re-collect.
   -- Does not delete rows or fabricate PE/PB/yield.
   UPDATE tw_research_first_pass_symbol_results
   SET payload_json = payload_json
     || jsonb_build_object(
          'revalidatePartial', true,
          'nextRetryAt', NULL,
          'retryExhausted', false
        ),
       updated_at = now()
   WHERE job_id = '<authoritative-fpj-id>'
     AND status = 'partial'
     AND stock_id = ANY(ARRAY['TW:6202','TW:6451','TW:8358']::text[]);  -- edit list
   ```
   Leave true blank-PE partials (e.g. TW:3363-class) unmarked.

4. **Resume collection** only with separate Stage D2 authorization:
   - Keep schedule CronJob suspended.
   - Prefer a single authorized drain tick / smoke — not unattended D2 until
     partials reclassify truthfully.

5. **Verify** after one bounded batch:
   - Transient cases move to `failed`→retry→`ok`/`partial` with honest reasons.
   - `firstPassProgress` counts match `count_first_pass_symbols`.
   - No duplicate symbol rows; lease released when idle.
   - Still stop at `awaiting_analyst_candidate_selection` (no Phase-2).

## Acceptance

Transient provider failures **cannot** silently become permanently non-retryable
`partial` records with `official_pe_unavailable` and no `nextRetryAt`.
