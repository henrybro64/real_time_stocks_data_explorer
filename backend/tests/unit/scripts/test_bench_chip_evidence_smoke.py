"""Smoke: offline chip-evidence micro-benchmark exits 0 (#346)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "bench_chip_evidence.py"


def test_bench_chip_evidence_smoke_n73_k2() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--n",
            "73",
            "--iterations",
            "2",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(_SCRIPT.parents[1]),
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "format_chip_evidence" in result.stdout
    assert "N=73" in result.stdout
