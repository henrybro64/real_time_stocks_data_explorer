# TW Research #361 — control inventory (Stage-4)

Visible controls on `/research/tw` **before** (`main@c623e509`) vs **after** (`feature/361-362-tw-research-e2e` @ `33eef72d`+ targeted review).

Scope: ordinary investor UI testids / primary CTAs. Advanced job-manage and modals listed only when stage-relevant.

## Legend

| Mark | Meaning |
|------|---------|
| keep | Still visible in that stage |
| remove | Removed from ordinary UI |
| merge | Absorbed into another control / message |
| add | New in #361 |
| secondary | Visible but not the stage primary CTA |

---

## Ordinary workflow (current)

Chronological sections (no tab switching required):

1. `tw-research-flow-stage-1` — 選擇研究股票  
2. `tw-research-flow-stage-2` — 收集與檢查市場資料  
3. `tw-research-flow-stage-3` — 挑選深入分析股票  
4. `tw-research-flow-stage-4` — 技術分析與研究結果  

Current stage is announced by `tw-research-flow-current`. Progress / raw JSON live under advanced disclosures.

---

## Stage: select_scope

| Control | Before (`c623e509`) | After |
|---------|---------------------|-------|
| `tw-research-start-full-research` | keep (primary) | keep (**primary** unless membership save is dirty) |
| `tw-research-save-membership` | keep | keep (**primary** only when membership dirty & no run) |
| Load / Preview / Reset membership | keep (always visible) | **secondary** — under membership advanced disclosure |
| Sector editor | keep | keep (edits enable Save) |
| `tw-research-start-first-pass` | keep | **secondary** (advanced; ordinary path uses Start Research) |
| `tw-research-create` (standalone scan create) | keep | **remove** |
| `tw-research-history` (standalone scan picker) | keep | **remove** |
| `tw-research-batch` (standalone scan batch) | keep | **remove** |

## Stage: collecting

| Control | Before | After |
|---------|--------|-------|
| `tw-research-start-full-research` | keep | keep (**primary**; re-arm / retry) |
| `tw-research-execution-status` | keep | keep |
| Load first-pass evidence | keep | keep (secondary) |
| `tw-research-run-first-pass-batch` | keep | **secondary** / advanced only |
| Chip evidence cards | keep | keep (auto from pinned snapshot) |
| Standalone scan create / batch / history | keep | **remove** |

## Stage: awaiting_candidate_selection

| Control | Before | After |
|---------|--------|-------|
| Candidate id editor | keep | keep |
| `tw-research-approve-candidates` | keep | keep (**one primary CTA**) |
| `tw-research-start-technical-from-candidates` | keep (gated) | keep (secondary until approved) |
| Candidate JSON textarea | keep | **secondary** (advanced disclosure) |
| Standalone scan create / batch / history | keep | **remove** |

## Stage: technical_analysis

| Control | Before | After |
|---------|--------|-------|
| `tw-research-start-technical-from-candidates` | keep | keep (**primary** until scan attached) |
| Phase-2 execution honesty | absent / implied | **add** `tw-research-phase2-execution-status` (`actively_analyzing` requires live lease; `between_batches` otherwise) |
| Technical results panel | ledger buried | **add** `tw-research-technical-results` (MACD/KD, pass eval, provenance when persisted) |
| Progress / worker / ledger | Progress tab | **secondary** advanced disclosure (`tw-research-tab-progress`) |
| Watchlist | Watchlist tab | keep in stage 4 (secondary table) |
| Standalone scan create / batch / history | keep | **remove** |

## Stage: results

| Control | Before | After |
|---------|--------|-------|
| `tw-research-start-full-research` | keep | keep (**primary**; start a new run) |
| Technical results panel | absent / ledger | **add** dedicated panel + historical badge when `?scan=` differs |
| Report evidence | keep | keep (advanced) |
| Agent JSON / chunk tools | keep | **secondary** advanced |
| Standalone scan create / batch / history | keep | **remove** |

---

## Historical research

| Control | Behavior |
|---------|----------|
| `?scan=<id>` / `initialScanId` | Preserved; does not overwrite with run-owned scan |
| `GET /research-runs/technical-results?scanId=` | Read-only; strict owner match; ownerless/foreign → 404 (no existence leak) |
| Viewing historical | Does **not** attach scan to active Research Run |

---

## Removed / merged / added summary

**Removed from ordinary path**

- `tw-research-create`
- `tw-research-batch`
- `tw-research-history`
- Always-visible Load / Preview / Reset membership (moved to advanced)

**Merged**

- “Scan exists ⇒ analyzing” → honest `phase2Execution` states  
- Standalone scan UX → run-owned First-Pass + Phase-2 drain + Stage-4 results  
- Three Tabs → chronological single-flow sections

**Added**

- One-primary-CTA rule per `TwResearchUiStage`
- Stage-4 technical-results API (scan-scoped) + panel
- Count breakdown: calculated / verified / daily-skipped / error / deferred / unknown / waiting / insufficient / pending (non-overlapping; intentional daily skip ≠ verified)
- Provenance: upstream feed (`twse`/`tpex`/`yahoo`) separate from PostgreSQL cache location; `tw_default` never shown as a verified provider; missing → unavailable
