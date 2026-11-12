# Taiwan research next (#338–#341)

Manual fundamental-first second pass, same-page research center tabs, analyst
evidence export, and gated orchestration. No CronJob enablement, no Notion SDK,
no production deploy in this slice.

## Issues

| Issue | Outcome (fixed 2026-11-12) |
| --- | --- |
| #338 Monthly-first MACD/KD for candidates | `not_run_by_design`, overrides, `technical_stage`, **73→12 and 300→40 admission fixtures** |
| #339 `/research/tw` three tabs | Tabs + Phase 1–4 banner + expandable rows + Playwright tabs spec |
| #340 Notion-ready evidence (Stock Ticker only) | Pack + ZH-TW fixture + UI export/example + two-decision unit proof |
| #341 Manual full-research first | **Postgres `tw_research_research_runs`** (fail-closed); start creates first-pass job |

See `docs/AI_HANDOFF.md` and `docs/ai-handoff-338-341.md`. Migrations: `20261112_0025`,
`20261112_0026` (research-run `revision` OCC), `20261112_0027` (job/scan claim fencing).

## Key modules

- `backend/app/services/tw_research/scan.py` — monthly-first path + `_not_run_daily_payload`
- `backend/app/services/tw_research/evaluation.py` — `dailyNotRunByDesign*` / watchlist counters; `map_recoverable_status`
- `backend/app/services/tw_research/research_run.py` — server `reconcile()`, OCC revision, gates
- `backend/app/services/tw_research/report_evidence.py` — analyst evidence pack + `contentDigest`
- `backend/app/services/tw_research/report_evidence_fixture_zh_tw.json` — labeled example only
- `frontend/src/views/research/TwResearchPage.tsx` — three Carbon tabs

## APIs added

| Method | Path |
| --- | --- |
| GET | `/api/tw-research/research-runs/current` |
| POST | `/api/tw-research/research-runs/start` |
| POST | `/api/tw-research/research-runs/gate` |
| POST | `/api/tw-research/report-evidence` |
| GET | `/api/tw-research/report-evidence/example-zh-tw` |

Candidate set / technical-scan requests accept `dailyOverrideIds`.

## 409 / idempotency semantics (review blockers)

### Report evidence

- Builds **all** technical rows from `state.records` (sorted by `stockId`); never
  truncates to export chunk 0.
- Loads the candidate set by the scan’s
  `candidateProvenance.candidateSetId/version` (not `latest_candidate_set`).
- Requires scan / first-pass / candidate-set snapshot pins to agree. On mismatch
  returns **409** `{ "error": "evidence_snapshot_mismatch", ... }`.
- First-pass or technical `partial` / `deferred` / `budget_exhausted` →
  publishability `incomplete_or_partial_evidence`.
- Pack includes `firstPass` `{ snapshotId, contentDigest, stockCount, manifestPath }`
  and `contentDigest` = SHA-256 of canonical JSON excluding `generatedAt`.

### Research runs (server-authoritative)

- `GET /research-runs/current` and `POST /research-runs/gate` call `reconcile()`;
  client status/snapshot query params on gate are **ignored**.
- `POST /research-runs/start` is cross-pod idempotent via DB-fenced job claims
  (`job_claim_token` / `pending_first_pass_job_id`). Only the claim owner creates
  and attaches the authoritative first-pass job; lost-race jobs are marked orphaned.
- Phase-2 technical scans use the same claim pattern (`phase2_claim_*` /
  `pending_technical_scan_id`) so retries reuse the matching scan.
- Upserts use `revision` optimistic concurrency (`WHERE user_id AND revision`);
  0 rows → **409** `research_run_revision_conflict`.
- `attach_candidate_set` requires phase
  `awaiting_analyst_candidate_selection` and matching
  `first_pass_snapshot_id` → else **409**.
- When a research run exists, `POST /candidate-sets` and
  `POST /candidate-sets/technical-scan` do **not** swallow
  `ResearchRunGateError` / `ResearchRunStoreUnavailable` / revision conflict
  (**409** / **503**). Legacy (no run row) keeps prior behaviour.
- Technical-scan create is idempotent on
  `(user_id, candidateSetId, version, tradingDay, calculationVersion, sorted dailyOverrideIds)`.

## Safety / open work (NOT done)

- `k8s/backend-tw-research-schedule-cronjob.yaml` remains `suspend: true`
- Stock Ticker does not write Notion or rank final picks
- Hard caps never silently bypassed
- **k3s benchmarks are NOT done** — performance/soak evidence on cluster remains open
- **Schedule-enablement checklist is NOT done** — CronJob must stay suspended;
  issues for enablement remain open until explicitly closed after ops sign-off

## Tests

```bash
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_monthly_first_338_341.py \
  tests/unit/routes/test_tw_research_route.py -q

cd frontend && npm test -- --run src/views/research/TwResearchPage.test.tsx
```
