"""Locate the git repository root from any test module."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "docs").is_dir() and (candidate / "backend").is_dir():
            return candidate
    raise AssertionError(f"could not locate repo root from {here}")
