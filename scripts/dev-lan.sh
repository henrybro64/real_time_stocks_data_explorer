#!/usr/bin/env bash
# Start Next dev bound to 0.0.0.0:3000 (LAN access). Use if `npm run dev:lan` at repo root fails
# (root package.json missing) or prefer a single command from any cwd.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}/frontend"
exec npm run dev:lan
