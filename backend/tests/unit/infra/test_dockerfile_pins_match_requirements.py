"""Dockerfile runtime pins must match requirements.txt (#275)."""

from __future__ import annotations

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[3]
_REQ = _BACKEND / "requirements.txt"
_DOCKERFILE = _BACKEND / "Dockerfile"

_PIN_RE = re.compile(r'"([A-Za-z0-9_.\[\]-]+)==([^"]+)"')


def _requirement_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _REQ.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if "==" not in cleaned:
            continue
        name, version = cleaned.split("==", 1)
        pins[name.strip().lower()] = version.strip()
    return pins


def _dockerfile_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for match in _PIN_RE.finditer(_DOCKERFILE.read_text(encoding="utf-8")):
        pins[match.group(1).lower()] = match.group(2)
    return pins


def test_dockerfile_runtime_pins_match_requirements() -> None:
    requirements = _requirement_pins()
    dockerfile = _dockerfile_pins()
    assert "pyjwt[crypto]" in requirements
    assert "pyjwt[crypto]" in dockerfile
    missing = sorted(name for name in requirements if name not in dockerfile)
    mismatched = sorted(
        f"{name}: requirements={requirements[name]} dockerfile={dockerfile[name]}"
        for name in requirements
        if name in dockerfile and requirements[name] != dockerfile[name]
    )
    assert not missing, f"Dockerfile missing pins: {missing}"
    assert not mismatched, f"Pin mismatches: {mismatched}"
