from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteRecord:
    id: str
    last_price: float
    change: float
    change_percent: float
    previous_close: float
    timestamp: str
    is_market_open: bool
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None


@dataclass(frozen=True)
class CandleRecord:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int

