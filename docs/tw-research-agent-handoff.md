# Taiwan research Agent JSON handoff (#322)

Browser-only, low-token transfer contract for an **external** Agent that may optionally publish to Notion. **Stock Ticker never writes Notion**, never embeds Notion credentials, and never schedules publishes.

## Status vocabulary (do not conflate)

| Status | Owner | Meaning |
| --- | --- | --- |
| `stock_ticker_export_verified` | Stock Ticker | Manifest + selected chunk digests verified over HTTPS |
| `unchanged_skip` | Stock Ticker | Latest `contentDigest` matches last transfer; zero chunk GETs |
| `incomplete_snapshot` | Stock Ticker | No `verified_ready` snapshot / quality incomplete |
| `auth_required` | Stock Ticker | Session missing/expired (HTTP 401/403) — re-login in browser |
| `digest_mismatch` | Stock Ticker | Chunk checksum ≠ immutable manifest map (retry once, then abort) |
| `notion_published_verified` | **External Agent only** | Notion page read-back matches counts/chunk hashes |
| `notion_publish_not_performed` | Default | Stock Ticker handoff finished; Notion step not claimed |

## Manifest-first flow (execute literally)

1. Authenticated browser session (same user as `/research/tw`).
2. `GET /api/tw-research/exports/latest/manifest?universeVersion={uv}&tradingDay={day}`
3. Compare `contentDigest` to the Agent’s last stored digest for that `(user, universeVersion, tradingDay)`.
4. If equal → `unchanged_skip`. Stop. Do **not** GET chunks. Do **not** rewrite Notion.
5. If missing / unequal → for each needed `chunkIndex` in the plan:
   - `GET /api/tw-research/exports/snapshots/{snapshotId}/chunk?chunkIndex={i}&kind=ledger`
   - Recalculate with `stable_chunk_checksum` (rows + paging fields only; excludes `scanId` / `generatedAt`) and match `manifest.chunkDigests[i]` and `chunk.checksumSha256`.
6. Optionally repeat for `kind=qualified` / `kind=enriched` if the consumer needs those views (ledger is enough for full rows).
7. Mark `stock_ticker_export_verified`. Notion remains `notion_publish_not_performed` unless the external Agent separately verifies Notion.

Helpers: `backend/app/services/tw_research/agent_handoff.py` (`plan_chunk_transfer`, `verify_ledger_chunk`, `build_agent_transfer_example`).

## Auth / retry

| HTTP | Action |
| --- | --- |
| 401 / 403 | `auth_required` — user must sign in again; never SSH/DB/bypass |
| 404 | `incomplete_snapshot` — wait until a verified snapshot exists |
| 409 | `digest_mismatch` — re-GET that chunk once; abort if still wrong |

Partial transfer: persist successfully verified chunk indexes; resume remaining indexes only.

## Browser-only constraints

- Read JSON from the authenticated HTTPS API, or use **Latest verified snapshot** on `/research/tw` (Load latest verified manifest / Load snapshot chunk 0).
- No HTML scraping of individual stock pages.
- No direct Postgres/SSH.
- Payload stays bounded: manifest counts + digests + paginated chunks.

## Deterministic fixture

- Module example: `build_agent_transfer_example()`
- Frozen JSON: `backend/tests/fixtures/tw_research/agent_handoff_example.json`

## External Notion step (optional, out of repo)

1. Stage changed content in Notion from verified chunks only.
2. Read back counts / hashes.
3. Atomically switch the Notion “current” pointer only after read-back matches.
4. Record `notion_published_verified` **outside** Stock Ticker.

Do **not** implement Notion SDK/API code in this repository.
