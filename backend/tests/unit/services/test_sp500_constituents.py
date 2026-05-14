from app.services.sp500_constituents import load_sp500_symbols


def test_load_sp500_symbols_reads_committed_snapshot() -> None:
    symbols = load_sp500_symbols()
    assert len(symbols) >= 400
    assert "AAPL" in symbols
    assert all(isinstance(s, str) and s.isupper() for s in symbols)
