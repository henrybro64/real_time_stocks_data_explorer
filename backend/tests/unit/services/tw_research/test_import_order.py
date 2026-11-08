"""Regression: TW research routes must import without relying on app.main order."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_tw_research_route_imports_in_clean_interpreter() -> None:
    """``from app.api.routes import tw_research`` must work as the first app import."""
    backend_root = Path(__file__).resolve().parents[4]
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(backend_root)!r}); "
        "from app.api.routes import tw_research; "
        "assert tw_research.router.prefix == '/tw-research'; "
        "from app.services.tw_research.preset import starter_preset_stock_ids; "
        "assert len(starter_preset_stock_ids()) >= 40; "
        "from app.services.stock_theme_service import DEFAULT_THEME_DEFINITIONS; "
        "assert any(theme.key == 'tw-pcb' for theme in DEFAULT_THEME_DEFINITIONS)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
