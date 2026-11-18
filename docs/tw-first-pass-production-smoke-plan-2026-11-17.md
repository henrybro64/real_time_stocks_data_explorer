# First-Pass production smoke & activation plan (#361 / #362)

**Status:** preparation only until you give a separate explicit authorization
for each 🔒 step. Read-only steps are marked 👁.

Companion: `docs/tw-research-first-pass-executor-362-366.md`,  
`scripts/tw-first-pass-preflight.sh`, structure-aware local Secret edit in
`scripts/deploy-k3s.sh` (`_set_local_secret_key`).

Branch target: `feature/361-362-tw-research-e2e` (verify `git rev-parse HEAD`
before ship). Host: Ubuntu k3s, namespace `stock-ticker`.

Production baseline to re-verify (last noted 2026-11-17): CronJob
`suspend=true`; drain env unset; run `rr_6398f8e5c7af4bd0b287` with
`schedule_enabled=false`; job `fpj_rr_6398f8e5c7af4bd0b287` 0/73.

---

## Order (mandatory)

**👁 Preflight and backup come before merge/deploy and before any Secret write.**

### 0. Fresh read-only preflight 👁

```bash
sudo bash scripts/tw-first-pass-preflight.sh k3s
# or: ./scripts/tw-first-pass-preflight.sh k3s
```

Expect: CronJob `suspend=true`; drain Secret keys absent or names-only;
effective env unset (or documented); run/job as above; 0 symbol rows for that
job (or documented counts). **Stop** if anything differs.

Also confirm no other research CronJob is unsuspended and capacity is OK.

### 1. Backup completeness 🔒 (writes a dump file; not a Secret edit)

```bash
mkdir -p ~/backups && sudo k3s kubectl -n stock-ticker exec deploy/stock-ticker-postgres -c postgres -- \
  pg_dump -U postgres -d stocks -Fc > ~/backups/stocks-$(date +%F-%H%M)-pre-362.dump && ls -lh ~/backups | tail -1
```

Record: path, UTC + Asia/Taipei time, size.

**Restoration (practice only on a disposable DB; never against production
without separate restore authorization):**

```bash
pg_restore --clean --if-exists -d stocks_restore_practice ~/backups/stocks-YYYY-MM-DD-HHMM-pre-362.dump
```

Do not continue until path + restore notes are written down for the operator.

### 2. Ship the branch 🔒 (merge + deploy) — only after §0–§1

1. Open/merge the PR from the same branch after CI.
2. On Ubuntu: `git checkout main && git pull`, then **`./scripts/deploy-k3s.sh k3s`**.
   - ⚠️ Never use `scripts/k8s-redeploy.sh` — it deletes the namespace **and the PVC**.
   - ⚠️ Do not set `FORCE_AUTH_SECRET_APPLY=1` unless the local gitignored secret
     file already has the intended drain keys — it replaces the live Secret.
   - ⚠️ Git still ships the drain CronJob as `suspend: true` (default-disabled).
     After Stage D, `deploy-k3s.sh` **preserves** an operator-approved live
     `suspend=false` across redeploy (annotation
     `stock-ticker.ai/first-pass-drain-desired-suspend`). To force re-suspend on
     deploy: `FORCE_FIRST_PASS_DRAIN_CRON_SUSPEND=1`. The unrelated schedule
     CronJob stays suspended. See
     `docs/tw-research-first-pass-cronjob-stage-d-362.md`.
3. 👁 `./scripts/verify-k3s-post-deploy.sh` and `sudo k3s kubectl -n stock-ticker get pods -o wide`.
4. 👁 Re-run §0 preflight.

### 3. Configure drain Secret 🔒 (values never printed, never in argv)

Prefer patch-file (not argv) then structure-aware local sync:

```bash
NS=stock-ticker
TOKEN="$(openssl rand -hex 32)"
OWNER="$(sudo k3s kubectl -n $NS exec deploy/stock-ticker-postgres -c postgres -- \
  psql -U postgres -d stocks -At -c "select user_id from tw_research_research_runs where research_run_id='rr_6398f8e5c7af4bd0b287'")"
[ -n "$TOKEN" ] && [ -n "$OWNER" ] || { echo "missing token/owner — stop"; exit 1; }
umask 077; P="$(mktemp)"
printf '{"stringData":{"TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN":"%s","TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID":"%s","TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED":"true"}}' "$TOKEN" "$OWNER" > "$P"
sudo k3s kubectl -n $NS patch secret stock-ticker-backend-auth --type merge --patch-file "$P"
# Keep gitignored local file in sync via structure-aware helper (not sed/line hacks).
# From repo root, with deploy-k3s helpers available, or inline equivalent that only
# edits stringData, validates key names, writes atomically mode 0600, never prints values:
K_TOKEN="$TOKEN" K_OWNER="$OWNER" python3 - <<'PY'
import os, re, tempfile
from pathlib import Path

path = Path("k8s/backend-auth-secret.yaml")
if not path.is_file():
    raise SystemExit("local secret file missing — skip sync or create via deploy-k3s ensure")
text = path.read_text(encoding="utf-8")
if "stringData:" not in text:
    raise SystemExit("refusing edit: no stringData")

updates = {
    "TW_RESEARCH_FIRST_PASS_DRAIN_TOKEN": os.environ["K_TOKEN"],
    "TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID": os.environ["K_OWNER"],
    "TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED": "true",
}
for key in updates:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise SystemExit(f"invalid key {key}")

def yaml_quote(raw: str) -> str:
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'

lines = text.splitlines(keepends=True)
out, in_sd, sd_indent, pending = [], False, "", "  "
remaining = dict(updates)
for line in lines:
    stripped = line.lstrip(" \t")
    indent = line[: len(line) - len(stripped)]
    if re.match(r"^stringData:\s*(#.*)?$", line.rstrip("\n\r")):
        in_sd, sd_indent, pending = True, indent, indent + "  "
        out.append(line)
        continue
    if in_sd:
        if stripped and not stripped.startswith("#") and len(indent) <= len(sd_indent):
            for k, v in list(remaining.items()):
                out.append(f"{pending}{k}: {yaml_quote(v)}\n")
                remaining.pop(k)
            in_sd = False
            out.append(line)
            continue
        m = re.match(r"^([A-Za-z0-9_]+)\s*:", stripped)
        if m and m.group(1) in remaining:
            k = m.group(1)
            out.append(f"{indent}{k}: {yaml_quote(remaining.pop(k))}\n")
            continue
        out.append(line)
        continue
    out.append(line)
if in_sd:
    for k, v in remaining.items():
        out.append(f"{pending}{k}: {yaml_quote(v)}\n")
    remaining.clear()
if remaining:
    raise SystemExit("stringData block not found")
fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
os.close(fd)
tmp_path = Path(tmp)
try:
    tmp_path.write_text("".join(out), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
finally:
    if tmp_path.exists():
        tmp_path.unlink(missing_ok=True)
# Do not print TOKEN/OWNER.
print("local stringData keys updated (values not printed)")
PY
shred -u "$P" 2>/dev/null || rm -f "$P"
unset TOKEN OWNER P K_TOKEN K_OWNER
sudo k3s kubectl -n $NS rollout restart deploy/stock-ticker-backend
sudo k3s kubectl -n $NS rollout status deploy/stock-ticker-backend --timeout=180s
./scripts/tw-first-pass-preflight.sh k3s   # 👁 ENABLED=true, USER_ID=set, TOKEN=set; Cron still suspended
```

Do **not** use bare line-prefix append that can duplicate keys outside `stringData`.

### 4. Zero concurrent processing gate 👁

```bash
sudo k3s kubectl -n stock-ticker get cronjob stock-ticker-tw-research-first-pass-drain \
  -o custom-columns=NAME:.metadata.name,SUSPEND:.spec.suspend
# SUSPEND must be true before any live drain body
```

### 5. Arm the managed run 🔒 (UI) + dry-run 👁

1. `/research/tw` → **開始研究**. Expect resume/created outcome for the
   authoritative job and execution `executor_not_ticking` (armed, no tick).
   Not “actively collecting”.
2. In-pod dry-run (`kubectl exec -i` … `dryRun: true`) from the executor
   runbook — token from container env only.

Go criteria: availability true; `researchRunId=rr_6398…`;
`jobId=fpj_rr_6398…`; `scheduleEnabled=true`; pending 73; no live lease.

### 6. One 3-stock smoke 🔒 (Cron still suspended)

Exact body:

```json
{
  "dryRun": false,
  "maxBatches": 1,
  "batchSize": 3,
  "timeBudgetSeconds": 60,
  "jobId": "fpj_rr_6398f8e5c7af4bd0b287"
}
```

### 7. Data consistency 👁

```bash
sudo k3s kubectl -n stock-ticker exec -i deploy/stock-ticker-postgres -c postgres -- \
  env PGOPTIONS='-c default_transaction_read_only=on' psql -U postgres -d stocks -P pager=off <<'SQL'
SELECT status, count(*) FROM tw_research_first_pass_symbol_results
 WHERE job_id='fpj_rr_6398f8e5c7af4bd0b287' GROUP BY 1;
SELECT stock_id, status, payload_json->>'failureClass' cls, updated_at
 FROM tw_research_first_pass_symbol_results WHERE job_id='fpj_rr_6398f8e5c7af4bd0b287'
 ORDER BY updated_at DESC LIMIT 5;
SELECT status, cursor, reserve_expires_at, lease_generation, updated_at
 FROM tw_research_first_pass_jobs WHERE job_id='fpj_rr_6398f8e5c7af4bd0b287';
SELECT phase, status, schedule_enabled, dimensions_json->'firstPassDrain' drain
 FROM tw_research_research_runs WHERE research_run_id='rr_6398f8e5c7af4bd0b287';
SQL
sudo k3s kubectl -n stock-ticker logs deploy/stock-ticker-backend --since=10m | grep -iE "first_pass|drain|Traceback" | tail -40
```

**Pass** = exactly 3 new rows; ≥2 `ok`/`partial`; any `failed` has a
provider/data `failureClass` (not `programming_error`); finished &lt; 60s; lease
released/expiring; `lastTickAt` set; UI shows 3/73 + last-data-progress.
**Fail** = programming_error, traceback, &gt;3 rows, stuck lease, or UI still 0/73
→ stop, §9 rollback, capture output.

Refresh UI: current vs historical Job isolation must hold.

### 8. Unattended activation 🔒 (separate approval — not part of initial smoke)

Only after §6–§7 pass and explicit approval. Prefer the Stage D helper (sets
desired-suspend annotation so redeploy preserves the state):

```bash
CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh enable k3s
# equivalent patch still works, but annotation may be missing until next deploy restore
```

Validate pace toward 73; stop on stall / programming_error / high fail rate → §9.
Do **not** enable `stock-ticker-tw-research-schedule`.

### 9. Rollback 🔒

1. `CONFIRM=YES ./scripts/tw-first-pass-cronjob-stage-d.sh rollback k3s`
   (or re-suspend the CronJob + set desired-suspend=true).
2. `POST .../first-pass-drain` with `enabled=false` (pauses Phase-1 and Phase-2 scheduling).
3. Optional: set `TW_RESEARCH_FIRST_PASS_DRAIN_ENABLED=false` + restart.
4. Keep run/job/symbol rows. Restore from `~/backups/*-pre-362.dump` **only**
   with separate restore authorization if data is corrupted.

---

## Sign-off

| Step | Operator | UTC | Pass? |
|---|---|---|---|
| 0 Preflight | | | |
| 1 Backup + restore notes | | | |
| 2 Deploy | | | |
| 3 Secret (names only logged) | | | |
| 4 Zero concurrency | | | |
| 5 Arm + dry-run | | | |
| 6 Live 3-stock | | | |
| 7 Consistency + UI refresh | | | |
| 8 Cron unsuspend (later) | | | |
| 9 Rollback if needed | | | |
