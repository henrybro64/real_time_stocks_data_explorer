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
| 6 | Provider budget/diagnostics not durable | **Confirmed** (log-only). Symbol payloads store **per-symbol deltas**; batch summary lives on `firstPassDrain.providerBatchSummary`. |
| 7 | Nested `firstPassProgress` can go stale | **Confirmed** when only counts changed while phase stayed `running`. Reconcile now persists on progress delta. |
| 8 | `revalidatePartial` reclaim loop | **Confirmed.** Flag is one-shot; `revalidationAttempts >= 1` blocks further reclaim via the flag. |

## OK vs partial semantics

- **`ok`:** required first-pass evidence present, including a usable **official PE** when the
  instrument is expected to publish one.
- **`partial`:** durable incomplete evidence where the **only completeness gap that forces
  partial (vs ok) for valuation** is an **official PE** gap (blank/missing PE on a published
  day row, or `official_pe_no_recent_snapshot`). Missing **PB** / **dividend yield** is
  **optional** — those fields alone do **not** demote an otherwise-complete symbol from
  `ok` to `partial`.
- **`failed`:** retryable provider/transient failures (timeout / 429 / 5xx / network /
  invalid day JSON / `provider_validation:*`) with bounded `nextRetryAt` (max 4 attempts).

## Semantics after this fix

- **Transient provider failure** (timeout / 429 / 5xx / network / invalid day JSON):
  `valuationWarning` uses `transient_*` / `rate_limited:*` / `provider_validation:*` →
  field retryable → symbol **`failed`** with bounded `nextRetryAt` (max 4 attempts).
  PB/yield reasons become `official_*_not_retrieved` (not “not present in snapshot”).
- **Authoritative missing / blank PE** on a published day row: non-retryable
  warning including `peStatus=…` → symbol **`partial`**, no auto-retry.
- **All recent weekdays no-data** (no transient): reason `official_pe_no_recent_snapshot`
  (non-retryable). No-data cache entries expire after ~30 minutes so the latest weekday
  can be rechecked.
- **429 / rate limit:** weekday lookback stops immediately; a short process-local negative
  cache (≈90s) per `(board, day)` prevents batch mates from re-requesting the same day.
  A cache hit reuses the original durable reason with a `:negative_cache` suffix
  (e.g. `transient_http:503:HttpFetchError:negative_cache`), so batch mates stay
  retryable and keep any `retry_after=N` hint.
- **429 classification:** a known HTTP status is authoritative. The bare `"429"` text
  heuristic applies only when no status is attached, because error text embeds the
  request URL (e.g. TWSE `date=20260429`). Raw URLs are never persisted in reasons.
- **OK fields** are preserved across retries (`_merge_preserved_ok_fields`).
- **`revalidatePartial`:** one-shot reclaim. `_enrich_symbol_payload` always pops the flag,
  increments `revalidationAttempts`, and records `revalidatedAt` +
  `revalidationOutcome` (`ok`|`partial`|`failed`). `needs_work` ignores the flag once
  `revalidationAttempts >= 1`.
- **Duplicates / leases:** unchanged unique `(job_id, stock_id)` upsert + lease
  generation fencing; reclaim never invents market data.

## Safe post-merge recovery for existing 13 rows (operator)

Do **not** run until this PR is approved/merged and Stage D2 is separately authorized.

**Precondition (all required):**

- Job `reserve_expires_at IS NULL` **or** `reserve_expires_at < now()` (no live lease).
- Both First-Pass CronJobs still `suspend=true` (confirm via preflight).

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

3. **Mark reclaim without discarding OK fields** (explicit CONFIRM).

   `payload_json` is PostgreSQL `json` (Alembic `20261111_0024`), **not** `jsonb`.
   Cast before `||`, then cast back to `json`:

   ```sql
   BEGIN;

   -- Preview (read-only)
   SELECT stock_id, status,
          payload_json->'fields'->'official_pe'->>'reason' AS pe_reason,
          payload_json->>'revalidatePartial' AS flagged
   FROM tw_research_first_pass_symbol_results
   WHERE job_id = '<authoritative-fpj-id>'
     AND status = 'partial'
     AND payload_json->'fields'->'official_pe'->>'reason' = 'official_pe_unavailable';

   -- Apply one-shot reclaim flag (edit stock_id list if needed)
   UPDATE tw_research_first_pass_symbol_results
   SET payload_json = (
         payload_json::jsonb || jsonb_build_object(
           'revalidatePartial', true,
           'nextRetryAt', NULL,
           'retryExhausted', false,
           'revalidateRequestedAt', now()::text,
           'revalidateReason', 'post_d1_unknown_official_pe_unavailable'
         )
       )::json,
       updated_at = now()
   WHERE job_id = '<authoritative-fpj-id>'
     AND status = 'partial'
     AND payload_json->'fields'->'official_pe'->>'reason' = 'official_pe_unavailable'
     AND stock_id = ANY(ARRAY['TW:6202','TW:6451','TW:8358']::text[])  -- edit list
     -- Enforce the no-live-lease precondition in the statement itself.
     AND EXISTS (
       SELECT 1 FROM tw_research_first_pass_jobs j
       WHERE j.job_id = tw_research_first_pass_symbol_results.job_id
         AND (j.reserve_expires_at IS NULL OR j.reserve_expires_at < now())
     );
   -- Expect "UPDATE 3". "UPDATE 0" with a live lease → ROLLBACK and wait.

   -- Verify
   SELECT stock_id,
          payload_json->>'revalidatePartial' AS flagged,
          payload_json->>'revalidateReason' AS reason
   FROM tw_research_first_pass_symbol_results
   WHERE job_id = '<authoritative-fpj-id>'
     AND stock_id = ANY(ARRAY['TW:6202','TW:6451','TW:8358']::text[]);

   COMMIT;
   ```

   Leave true blank-PE partials (e.g. TW:3363-class) unmarked. The cast pattern is
   covered by a unit SQL-compat assertion in
   `tests/unit/services/tw_research/test_first_pass_post_d1_data_quality_362.py`
   (and can be exercised against Postgres in CI when a disposable DB is available).

4. **Resume collection** only with separate Stage D2 authorization:
   - Keep schedule CronJob suspended.
   - Prefer a single authorized drain tick / smoke — not unattended D2 until
     partials reclassify truthfully.
   - Expect at most **one** reclaim pass per flagged row (`revalidationAttempts`).

5. **Verify** after one bounded batch:
   - Transient cases move to `failed`→retry→`ok`/`partial` with honest reasons.
   - Flagged rows that stay blank-PE become `partial` with `revalidationAttempts >= 1`
     and are **not** reclaimed again via the flag.
   - `firstPassProgress` counts match `count_first_pass_symbols`.
   - No duplicate symbol rows; lease released when idle.
   - Still stop at `awaiting_analyst_candidate_selection` (no Phase-2).

## Acceptance

Transient provider failures **cannot** silently become permanently non-retryable
`partial` records with `official_pe_unavailable` and no `nextRetryAt`.
`revalidatePartial` must not create an endless reclaim loop.
