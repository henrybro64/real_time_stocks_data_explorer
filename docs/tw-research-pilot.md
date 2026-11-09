# Taiwan research pilot & Agent handoff (#315)

This worksheet supports a **manual, one-time** Top-10 vs Top-20 benchmark on the home Ubuntu/k3s node. Stock Ticker code and tests do **not** write Notion and do not claim live node-safe performance without measurements.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `frontend_export_ready` | Authenticated UI/JSON/CSV export is available from Stock Ticker |
| `external_notion_published` | Optional step owned by an **external** browser Agent; verified/blocked outside this repo |
| `provisional_unit_benchmark` | Automated tests with mocks; not a substitute for Ubuntu measurements |
| `ubuntu_measurement_pending` | No SSH/cluster access in the coding session; fill during the pilot |

## Same-universe Top-10 vs Top-20

1. Resolve one configurable universe (starter preset **or** explicit IDs including 餐飲/航空/unclassified).
2. Create two scans with the **same** `universe` / `tradingDay`, `targetCount=10` then `targetCount=20`.
3. Run scan batches to completion (or honest `budget_exhausted` / `incomplete`).
4. Run enrichment batches only for dual-pass symbols; stop cleanly at `maxEnrichmentsPerRun`.
5. Record wall clock, peak RSS, CPU, DB/Redis notes, upstream HTTP/429, JSON/CSV sizes, UI load.

Expectation: scanning cost for Top-10 vs Top-20 on the **same universe** should be similar. Cost rises mainly when the universe expands or cold history backfill dominates.

## Required extensibility checks

- Off-list symbols: e.g. `TW:2727` (餐飲), `TW:2618` (航空), `TW:2330` if unclassified vs preset.
- Synthetic >80-symbol universe via mocks (unit tests already cover export chunking).
- Confirm no hidden 80/82 caps in API validation, UI pagination, export, or checkpoints.

## Notion (external only)

1. Open `/research/tw` while signed in.
2. Load **Agent JSON** (`GET /api/tw-research/scans/{id}/export/browser-text`).
3. External Agent alone may post a one-time batch to the existing Notion Data Hub.
4. Document result as `verified` or `blocked`. Never add Notion credentials/SDK to Stock Ticker.

## Related issues

#269 #271 #272 #274 #287 #310–#314.
