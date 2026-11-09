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
4. If equal → `skip_all` / `unchanged_skip`. Stop. Do **not** GET chunks. Do **not** rewrite Notion.
5. If `contentDigest` changed but every `chunkDigests` entry is unchanged → `manifest_only`. Update the consumer's snapshot pointer and metadata. Do **not** GET chunks.
6. If chunk digests differ → `fetch_changed`:
   - `GET /api/tw-research/exports/snapshots/{snapshotId}/chunk?chunkIndex={i}&kind=ledger` only for indexes in `chunkIndexes` (added or modified).
   - Drop `removedChunkIndexes` from the consumer's staged dataset. Do **not** GET those indexes; the new snapshot does not publish them.
   - Recalculate with `stable_chunk_checksum` (rows + paging fields only; excludes `scanId` / `generatedAt`) and match `manifest.chunkDigests[i]` and `chunk.checksumSha256`.
7. If there is no previous manifest, or either side lacks a usable `chunkDigests` map → `fetch_all` for the current indexes. An empty `chunkDigests` map means zero chunks.
8. Optionally repeat for `kind=qualified` / `kind=enriched` if the consumer needs those views (ledger is enough for full rows).
9. Mark `stock_ticker_export_verified` when the manifest and any fetched chunks check out. Notion remains `notion_publish_not_performed` unless the external Agent separately verifies Notion.

`plan_chunk_transfer` is a pure function. It does not record Agent reads, acknowledgements, checkpoints, or Notion delivery. The external Agent keeps its own previous manifest.

Helpers: `backend/app/services/tw_research/agent_handoff.py` (`plan_chunk_transfer`, `verify_ledger_chunk`, `build_agent_transfer_example`).

### Transfer-plan matrix

| Previous vs current | Action | Chunk GETs | Consumer follow-up |
| --- | --- | --- | --- |
| Same overall `contentDigest` | `skip_all` | none | Keep the existing snapshot |
| New overall digest, identical `chunkDigests` | `manifest_only` | none | Update snapshot pointer / metadata |
| One changed chunk | `fetch_changed` | that index | Verify its checksum |
| New chunk index | `fetch_changed` | the new index | Verify its checksum |
| Removed chunk index | `fetch_changed` with `removedChunkIndexes` | none for the removed index | Delete it locally |
| Changed chunk and a removed index | `fetch_changed` | changed indexes only | Verify fetches; delete removed indexes |
| Current `chunkDigests` empty | `fetch_changed` or `fetch_all` | none | Drop every previous index |
| No usable previous manifest | `fetch_all` | every current index | Replace staged chunks |

## Auth / retry

| HTTP | Action |
| --- | --- |
| 401 / 403 | `auth_required` — user must sign in again; never SSH/DB/bypass. Keep the last verified consumer state. |
| 404 | `incomplete_snapshot` — wait until a verified snapshot exists. Do not clear staged chunks. |
| 409 | `digest_mismatch` — re-GET that chunk once; abort if still wrong. Do not replace a previously verified chunk. |
| 5xx / failed read | Retry that GET. Keep the last verified chunk until a replacement checksum matches. |

Partial transfer: the external Agent persists successfully verified chunk indexes and resumes remaining indexes only. Stock Ticker does not store that progress.

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
