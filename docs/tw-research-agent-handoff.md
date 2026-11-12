# Taiwan research Agent JSON handoff (#322 / #340)

Browser-only, low-token transfer contract for an **external** Agent that may optionally publish to Notion. **Stock Ticker never writes Notion**, never embeds Notion credentials, and never schedules publishes.

For the analyst-ready evidence pack (Phase-1 universe + Phase-2 candidate technicals), see `POST /api/tw-research/report-evidence` and the labeled Traditional Chinese fixture at `GET /api/tw-research/report-evidence/example-zh-tw` (`exampleOnly: true` — not a live recommendation). ChatGPT makes the two selection/report decisions; the external writer publishes only after explicit approval.

## Status vocabulary (do not conflate)

| Status | Owner | Meaning |
| --- | --- | --- |
| `verification_required` | Transfer plan | Plan only. Checksums are not finished, so the handoff is not complete |
| `stock_ticker_export_verified` | Consumer, after checks | Manifest and required chunk checksums matched. Not implied by creating a plan |
| `unchanged_skip` | Consumer | Latest `contentDigest` matches a snapshot the consumer already verified and retained. Zero chunk GETs |
| `incomplete_snapshot` | Stock Ticker | Quality is not explicitly `verified_ready` |
| `auth_required` | Stock Ticker | Session missing/expired (HTTP 401/403) — re-login in browser |
| `digest_mismatch` | Stock Ticker | Chunk checksum ≠ immutable manifest map (retry once, then abort) |
| `notion_published_verified` | **External Agent only** | Notion page read-back matches counts/chunk hashes |
| `notion_publish_not_performed` | Default | Stock Ticker handoff finished; Notion step not claimed |

## Manifest-first flow (execute literally)

1. Authenticated browser session (same user as `/research/tw`).
2. `GET /api/tw-research/exports/latest/manifest?universeVersion={uv}&tradingDay={day}`
3. Compare `contentDigest` to the Agent’s last stored digest for that `(user, universeVersion, tradingDay)`.
4. If equal → `skip_all` / `unchanged_skip`. Stop. Do **not** GET chunks. Do **not** rewrite Notion. This assumes the consumer still holds the previous snapshot from a successful verification.
5. If `contentDigest` changed but every `chunkDigests` entry is unchanged → `manifest_only` with `verification_required`. Confirm retained chunk checksums match `chunkDigests` (`verify_cached_chunk_digests`). Only then update the snapshot pointer. Do **not** GET chunks.
6. If chunk digests differ → `fetch_changed`:
   - `GET /api/tw-research/exports/snapshots/{snapshotId}/chunk?chunkIndex={i}&kind=ledger` only for indexes in `chunkIndexes` (added or modified).
   - Drop `removedChunkIndexes` from the consumer's staged dataset. Do **not** GET those indexes; the new snapshot does not publish them.
   - Recalculate with `stable_chunk_checksum` (rows + paging fields only; excludes `scanId` / `generatedAt`) and match `manifest.chunkDigests[i]` and `chunk.checksumSha256`.
7. If there is no previous manifest, or either side lacks a usable `chunkDigests` map → `fetch_all` for the current indexes. An empty `chunkDigests` map means zero chunks.
8. Optionally repeat for `kind=qualified` / `kind=enriched` if the consumer needs those views (ledger is enough for full rows).
9. The plan's `verification` stays `verification_required` until those checks pass. Record `stock_ticker_export_verified` only from `verify_ledger_chunk` or `verify_cached_chunk_digests`. Notion remains `notion_publish_not_performed` unless the external Agent separately verifies Notion.

A snapshot is eligible only when its effective `qualityStatus` is exactly `verified_ready`. Missing, blank, unknown, partial, and failed statuses abort with `incomplete_snapshot`. `snapshotId`, `contentDigest`, and chunk checksums do not imply verified quality. A verified snapshot with zero qualifying rows and an empty `chunkDigests` map is still valid.

Optional `quality_status` is caller context, such as the snapshot row column. A blank argument is omitted. It does not override the manifest field. If both are present and differ, the plan aborts with `quality_status_conflict`, even when one of them is `verified_ready`.

`plan_chunk_transfer` is a pure function. It does not record Agent reads, acknowledgements, checkpoints, or Notion delivery. The external Agent keeps its own previous manifest.

Helpers: `backend/app/services/tw_research/agent_handoff.py` (`plan_chunk_transfer`, `verify_ledger_chunk`, `verify_cached_chunk_digests`, `build_agent_transfer_example`).

### Transfer-plan matrix

| Previous vs current | Action | Chunk GETs | Consumer follow-up |
| --- | --- | --- | --- |
| Same overall `contentDigest` | `skip_all` | none | Keep the existing snapshot |
| New overall digest, identical `chunkDigests` | `manifest_only` | none | Match cached checksums to `chunkDigests`, then update the snapshot pointer |
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
