# Taiwan research pilot & capacity benchmark (#315 / #321)

Manual, one-time Top-10 vs Top-20 benchmark on the home Ubuntu/k3s node. Stock Ticker code and unit tests do **not** write Notion and do **not** claim live node-safe performance without measurements.

## Current measurement status

| Field | Value |
| --- | --- |
| Status | **`ubuntu_measurement_pending`** |
| Reason | No SSH / kubectl access to the home Ubuntu private node from the coding agent session |
| Safe for unattended daily snapshots? | **Unknown** until the table below is filled |
| Build revision to measure | Record `git rev-parse HEAD` on the node at pilot time |
| Related handoff contract | [tw-research-agent-handoff.md](tw-research-agent-handoff.md) (#322) |

Do **not** mark #321 acceptance “actual measurements complete” while this status remains `ubuntu_measurement_pending`.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `frontend_export_ready` | Authenticated UI/JSON/CSV export is available from Stock Ticker |
| `stock_ticker_export_verified` | Manifest + chunk digests verified (#322) |
| `external_notion_published` / `notion_published_verified` | Optional step owned by an **external** browser Agent |
| `provisional_unit_benchmark` | Automated tests with mocks; **not** a substitute for Ubuntu measurements |
| `ubuntu_measurement_pending` | No cluster access yet; fill during the pilot |

## Prerequisites checklist

- [ ] Hardening #318–#320 migration applied on the node (`alembic upgrade head` includes `20261109_0021`)
- [ ] Authenticated session works for `/research/tw` and `/api/tw-research/*`
- [ ] Note other workloads on the node (dashboard, charts, alerts, spending tracker)
- [ ] Record start time (Asia/Taipei) and `kubectl get pods -A` / node allocatable CPU+memory

## Baseline headroom (fill on node)

| Metric | Before pilot | During peak batch | Notes |
| --- | --- | --- | --- |
| Node CPU % | | | `kubectl top node` / `mpstat` |
| Peak RSS (API pod) | | | `kubectl top pod` / `ps` |
| DB latency p50/p95 | | | simple `SELECT 1` + scan GET |
| Redis memory | | | if used |
| Disk I/O wait | | | |
| Upstream HTTP / 429 | | | TW provider only on POST batches |
| Pod restarts / OOM | | | must stay 0 |

## Workload matrix

Run warm and **cold** history separately. Use bounded batches (`batchSize=10`).

| Run | Universe | targetCount | Cold/Warm | Wall clock | Peak RSS | Upstream months | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 10 symbols | 10 | cold | | | | |
| B | 10 symbols | 10 | warm | | | | |
| C | 20 symbols | 10 | cold | | | | |
| D | 20 symbols | 20 | cold | | | | same universe as C |
| E | 40 symbols | 10 | cold | | | | |
| F | starter preset | 10 | cold | | | | multi-batch |
| G | starter + `TW:2727`,`TW:2618`,`TW:2330` | 10 | cold | | | | 餐飲/航空/unclassified |
| H | synthetic >80 via mocks | n/a | n/a | n/a | n/a | n/a | covered by unit tests |

### Same-universe Top-10 vs Top-20 (required)

1. Resolve one configurable universe (starter **or** explicit IDs including 餐飲/航空/unclassified).
2. Create two scans with the **same** `universe` / `tradingDay`, `targetCount=10` then `targetCount=20`.
3. Run scan batches to `completed_processing` / `verified_ready` (or honest `budget_exhausted` / `partial`).
4. Run enrichment batches only for dual-pass symbols; stop at `maxEnrichmentsPerRun`.
5. Quantify incremental enrichment/export cost (JSON size, wall clock). Never fabricate candidates to fill 10/20.

Expectation: scan cost for Top-10 vs Top-20 on the **same universe** should be similar; cost rises when the universe expands or cold history dominates.

## Field availability checklist

For a sample of day+month pass symbols, record as-of / missing:

| Field | Available? | As-of / gap |
| --- | --- | --- |
| Daily MACD/KD | | |
| Monthly MACD/KD | | |
| SMA20/60/120/240 | | |
| 52-week position | | |
| Institutionals 5d | | |
| Margin | | |
| TDCC weekly | | |
| Official PE / EPS / PEG | | |
| Reference levels | | |

## Export / GET safety (required)

Commands (authenticated browser or `curl` with session cookie):

```bash
# Replace SNAPSHOT / UV / DAY after a verified finalize
curl -sS -b "$COOKIE" \
  "$BASE/api/tw-research/exports/latest/manifest?universeVersion=$UV&tradingDay=$DAY"
curl -sS -b "$COOKIE" \
  "$BASE/api/tw-research/exports/snapshots/$SNAPSHOT/chunk?chunkIndex=0&kind=ledger"
```

Confirm during GET:

- Upstream TW HTTP count does **not** increase
- DB reads stay modest (manifest + snapshot rows only)
- Dashboard / charts / alerts / spending tracker stay responsive

## Suggested stop / rollback policy

Stop the pilot (do not raise budgets) if any of:

- API pod RSS grows > **+500 MiB** sustained vs baseline during batches
- Node CPU > **85%** sustained > 2 minutes while other workloads lag
- Upstream 429 storm or provider errors > **10%** of month fetches
- Any OOMKill / CrashLoop on stock-ticker or Postgres
- UI navigation > **3s** p95 for core pages during the run

Rollback: stop POST `/scans/batches` and `/enrichment-batches`; GET/export remains safe (read-only). No cron enablement in this issue.

## Decision block (fill after real measurements)

| Question | Answer |
| --- | --- |
| Safe for daily verified snapshots? | pending |
| Recommended batch size | 10 (code max) |
| Recommended batch frequency | pending |
| Max upstream month fetches / run | pending (default 40) |
| Max enrichments / run | pending (default 20) |
| Warnings / source gaps | pending |

## Notion (external only)

See [tw-research-agent-handoff.md](tw-research-agent-handoff.md). Stock Ticker marks export verification only; Notion verification is optional and external.

## Related issues

#269 #271 #272 #274 #287 #310–#320 #321 #322.
