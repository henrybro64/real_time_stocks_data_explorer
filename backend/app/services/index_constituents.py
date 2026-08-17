"""Committed index constituent snapshots under ``app/data/``."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_index_symbols(filename: str) -> tuple[str, ...]:
    """Return unique upper-case tickers from a committed constituent JSON file."""
    path = _DATA_DIR / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Index constituent file missing or invalid (%s): %s", path, exc)
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


@lru_cache(maxsize=8)
def _load_index_symbols_cached(filename: str) -> tuple[str, ...]:
    return load_index_symbols(filename)


def load_sp500_symbols() -> tuple[str, ...]:
    """Return upper-case S&P 500 tickers from the committed snapshot file."""
    return _load_index_symbols_cached("sp500_constituents.json")


def load_djia_symbols() -> tuple[str, ...]:
    """Return upper-case DJIA tickers from the committed snapshot file."""
    return _load_index_symbols_cached("djia_constituents.json")


def load_nasdaq100_symbols() -> tuple[str, ...]:
    """Return upper-case Nasdaq-100 tickers from the committed snapshot file."""
    return _load_index_symbols_cached("nasdaq100_constituents.json")
