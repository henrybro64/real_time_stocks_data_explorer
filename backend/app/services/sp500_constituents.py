"""S&P 500 constituent symbols (snapshot JSON under ``app/data/``)."""

from __future__ import annotations

from app.services.index_constituents import load_sp500_symbols

__all__ = ["load_sp500_symbols"]
