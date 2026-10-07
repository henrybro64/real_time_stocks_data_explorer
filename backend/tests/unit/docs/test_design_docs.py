"""Guardrails for the design docs and portfolio README (#30, #7)."""

from __future__ import annotations

import pytest

from tests.repo_root import repo_root


_REPO = repo_root()
_README = _REPO / "README.md"
_DESIGN_DOCS = (
    "architecture-overview.md",
    "multi-user-design.md",
    "testing-strategy.md",
    "quote-refresh-design.md",
)


@pytest.mark.parametrize("name", _DESIGN_DOCS)
def test_design_doc_exists_is_linked_and_lists_limitations(name: str) -> None:
    doc = _REPO / "docs" / name
    assert doc.is_file()
    assert f"](docs/{name})" in _README.read_text(encoding="utf-8")
    text = doc.read_text(encoding="utf-8")
    # testing-strategy.md names its section after #30's "biggest test gaps".
    assert "## Current limitations" in text or "## Biggest gaps" in text


def test_architecture_overview_has_a_diagram() -> None:
    text = (_REPO / "docs" / "architecture-overview.md").read_text(encoding="utf-8")
    assert "```mermaid" in text


def test_readme_explains_the_system_before_setup() -> None:
    readme = _README.read_text(encoding="utf-8")
    explanatory = (
        "## Problem statement",
        "## Architecture",
        "## Key design decisions",
        "## Multi-user model",
        "## Testing strategy",
        "## CI/CD",
    )
    setup = readme.find("## Getting started")
    assert setup != -1
    for heading in explanatory:
        position = readme.find(heading)
        assert position != -1, f"README is missing {heading!r}"
        # Product and system explanation come before local setup (#7).
        assert position < setup, f"{heading!r} should come before setup"
    for heading in ("## Known limitations", "## Future roadmap", "## Documentation"):
        assert heading in readme, f"README is missing {heading!r}"
    assert "```mermaid" in readme
    assert "### Sample flows" in readme


def test_readme_screenshots_exist() -> None:
    import re

    readme = _README.read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme)
    assert images, "README should show screenshots (#7)"
    missing = [path for path in images if not (_REPO / path).is_file()]
    assert missing == [], f"README images missing: {missing}"
