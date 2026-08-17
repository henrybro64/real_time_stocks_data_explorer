from app.services.index_constituents import (
    load_djia_symbols,
    load_nasdaq100_symbols,
    load_sp500_symbols,
)


def test_load_sp500_symbols_reads_committed_snapshot() -> None:
    symbols = load_sp500_symbols()
    assert len(symbols) >= 400
    assert "AAPL" in symbols
    assert all(isinstance(s, str) and s.isupper() for s in symbols)


def test_load_djia_symbols_reads_committed_snapshot() -> None:
    symbols = load_djia_symbols()
    assert len(symbols) == 30
    assert len(set(symbols)) == 30
    assert {"AAPL", "MSFT", "NVDA", "GOOGL", "JPM"} <= set(symbols)


def test_load_nasdaq100_symbols_reads_committed_snapshot() -> None:
    symbols = load_nasdaq100_symbols()
    assert len(symbols) >= 90
    assert len(set(symbols)) == len(symbols)
    assert {"AAPL", "MSFT", "NVDA", "META", "TSLA"} <= set(symbols)
    assert "JPM" not in symbols
