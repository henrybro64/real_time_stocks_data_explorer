# First-pass job lifecycle (#355)

## Actions

| Action | Semantics | DB |
| --- | --- | --- |
| Cancel | `status=cancelled`, bump `lease_generation`, clear lease, disarm run drain if owned | Rows retained |
| Archive / Restore | set/clear `archived_at` | Soft-hide; reversible |
| Permanent delete (standalone) | transactional hard delete of job + CASCADE symbol rows (+ owned snapshot if unreferenced) | Audit tombstone in `tw_research_deletion_audit` |
| Running delete | `deletion_state=requested` + cancel/fence → HTTP 202; hard delete after quiescence | Lazy GC on list |
| Full research delete | `DELETE /research-runs/current` with preview token — removes run + uniquely owned jobs/symbols/snapshots/candidates/scans/exports | Audit `entity_type=research_run` |

## API

- `GET /api/tw-research/first-pass/jobs?includeArchived=`
- `POST …/jobs/{id}/cancel|archive|unarchive`
- `GET …/jobs/{id}/deletion-preview` → `previewToken`, blockers, closure counts
- `DELETE …/jobs/{id}` + `X-Deletion-Preview-Token` → 200 deleted / 202 requested / 409 blocked / 404
- `GET …/jobs/{id}/deletion` → 200 lifecycle / 410 tombstone for owner
- `GET /api/tw-research/research-runs/current/deletion-preview`
- `DELETE /api/tw-research/research-runs/current` + `X-Deletion-Preview-Token`

## Schema inventory (models @ main + migration `20261114_0028`)

| Table | Link | Delete behaviour |
| --- | --- | --- |
| `tw_research_first_pass_symbol_results` | `job_id` FK **ON DELETE CASCADE** | Job-local; deleted with job |
| `tw_research_first_pass_snapshots` | No `job_id`; unique `(user_id, content_digest)` | Delete only when unreferenced by other jobs / candidate sets / run |
| `tw_research_candidate_sets` | `first_pass_snapshot_id` FK **ON DELETE RESTRICT** | Delete candidate before snapshot in full-run path |
| `tw_research_research_runs` | Plain string pointers (no FKs) to job/snapshot/candidate/scan | Cleared or row deleted in full-run path |
| `tw_research_scans` | Provenance often in JSON; `user_id` nullable (legacy) | Owned scans deleted in full-run; legacy unowned → `legacy_unowned` block |
| `tw_research_symbol_results` | FK → scans CASCADE | Scan-local |
| `tw_research_export_snapshots` | `scan_id` FK **ON DELETE SET NULL** | Full-run deletes exports explicitly before scans |
| `tw_research_deletion_audit` | No FKs to deleted entities | Tombstone only |

Capture live `pg_constraint` on a disposable DB when available:

```sql
SELECT conname, conrelid::regclass, confrelid::regclass, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid::regclass::text LIKE 'tw_research%'
ORDER BY 1;
```

## Safety

- Cross-user → 404 (never 403).
- Run-owned jobs require full-run delete path (preview `requiresFullRunDelete`).
- Shared snapshots (digest uniqueness) are never deleted while referenced.
- Migration `20261114_0028` is additive; touches zero existing jobs.
- No CronJob enablement; no production job cleanup without approval.
