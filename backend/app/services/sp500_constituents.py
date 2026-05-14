"""S&P 500 constituent symbols (snapshot JSON under ``app/data/``)."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_sp500_symbols() -> tuple[str, ...]:
    """Return upper-case S&P 500 tickers from the committed snapshot file."""
    path = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("S&P 500 constituent file missing or invalid (%s): %s", path, exc)
        return ()
    symbols = raw.get("symbols")
    if not isinstance(symbols, list):
        return ()
    out: list[str] = []
    for entry in symbols:
        token = str(entry).strip().upper()
        if token and token not in out:
            out.append(token)
    return tuple(out)
