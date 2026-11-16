# TW Research #361 — control inventory (Stage-4)

Visible controls on `/research/tw` **before** (`main@c623e509`) vs **after** (`feature/361-362-tw-research-e2e`).

Scope: ordinary investor UI testids / primary CTAs. Advanced job-manage and modals listed only when stage-relevant.

## Legend

| Mark | Meaning |
|------|---------|
| keep | Still visible in that stage |
| remove | Removed from ordinary UI |
| merge | Absorbed into another control / message |
| add | New in #361 |

---

## Stage: select_scope

| Control | Before (`c623e509`) | After |
|---------|---------------------|-------|
| `tw-research-start-full-research` | keep (primary) | keep (primary) |
| `tw-research-save-membership` | keep | keep (primary when membership dirty) |
| `tw-research-start-first-pass` | keep | keep (advanced / secondary) |
| `tw-research-create` (standalone scan create) | keep | **remove** |
| `tw-research-history` (standalone scan picker) | keep | **remove** |
| `tw-research-batch` (standalone scan batch) | keep | **remove** |

## Stage: collecting

| Control | Before | After |
|---------|--------|-------|
| `tw-research-start-full-research` | keep | keep (primary; re-arm / retry path) |
| `tw-research-run-first-pass-batch` | keep | keep (secondary / advanced) |
| `tw-research-execution-status` | keep | keep |
| Standalone scan create / batch / history | keep | **remove** |

## Stage: awaiting_candidate_selection

| Control | Before | After |
|---------|--------|-------|
| Candidate id editor + resolve/save | keep | keep |
| `tw-research-approve-candidates` (`saveCandidateSet`) | keep (often secondary) | keep (**one primary CTA**) |
| `tw-research-start-technical-from-candidates` | keep (gated) | keep (secondary until approved) |
| Standalone scan create / batch / history | keep | **remove** |

## Stage: technical_analysis

| Control | Before | After |
|---------|--------|-------|
| `tw-research-start-technical-from-candidates` | keep | keep (**one primary CTA** until attached) |
| Phase-2 execution honesty message | absent / implied by scan create | **add** `tw-research-phase2-execution-status` |
| Technical results table (partial rows) | ledger buried under Progress / Watchlist | **add** `tw-research-technical-results` |
| Standalone scan create / batch / history | keep | **remove** |
| Pause/resume via `first-pass-drain` | Phase-1 only | **merge** — same control also arms/pauses Phase-2 drain |

## Stage: results

| Control | Before | After |
|---------|--------|-------|
| `tw-research-start-full-research` | keep | keep (primary; start a new run) |
| Qualified / dual-pass ledger | Progress tab table | **add** dedicated results panel with monthly/daily MACD·KD, pass chip, bars, zh-TW/EN reasons |
| Report evidence | keep | keep |
| Standalone scan create / batch / history | keep | **remove** |

---

## Removed / merged summary

**Removed from ordinary Stage-4 path**

- `tw-research-create`
- `tw-research-batch`
- `tw-research-history`

**Merged**

- “Scan exists ⇒ analyzing” implication → honest `phase2Execution` message states
- Standalone scan progress UX for the managed run → run-owned technical drain + Stage-4 results read model (`GET /research-runs/technical-results`)

**Added**

- One-primary-CTA rule per `TwResearchUiStage`
- Phase-2 execution status copy (zh-TW / EN)
- Technical results panel (desktop table / mobile stacked cards &lt; 672px)
- Control inventory (this document)
