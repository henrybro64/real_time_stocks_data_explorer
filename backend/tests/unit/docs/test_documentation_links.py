from __future__ import annotations

import re
from pathlib import Path

from tests.repo_root import repo_root


_REPO_ROOT = repo_root()
_DOCS_DIR = _REPO_ROOT / "docs"


def _resolve_markdown_link(source: Path, target: str) -> Path | None:
    path_part = target.split("#", 1)[0].strip()
    if not path_part or path_part.startswith(("http://", "https://", "mailto:")):
        return None
    if path_part.startswith("../"):
        return (_REPO_ROOT / path_part.removeprefix("../")).resolve()
    if path_part.startswith("docs/"):
        return _REPO_ROOT / path_part
    if path_part.endswith(".md"):
        if source.parent == _DOCS_DIR:
            return _DOCS_DIR / path_part
        return _REPO_ROOT / "docs" / path_part
    return None


def _missing_links(source: Path, text: str) -> list[str]:
    missing: list[str] = []
    for match in re.finditer(r"\]\(([^)]+)\)", text):
        target = match.group(1).strip()
        if target.startswith("#"):
            continue
        resolved = _resolve_markdown_link(source, target)
        if resolved is not None and not resolved.is_file():
            missing.append(target)
    return missing


def test_readme_doc_links_resolve() -> None:
    missing = _missing_links(_REPO_ROOT / "README.md", (_REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    assert missing == [], f"README links to missing files: {missing}"


def test_all_docs_markdown_links_resolve() -> None:
    failures: list[str] = []
    for doc in sorted(_DOCS_DIR.glob("*.md")):
        missing = _missing_links(doc, doc.read_text(encoding="utf-8"))
        if missing:
            failures.append(f"{doc.name}: {missing}")
    assert failures == [], "Broken doc links:\n" + "\n".join(failures)
