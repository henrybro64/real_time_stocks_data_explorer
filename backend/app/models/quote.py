from dataclasses import dataclass
from typing import Literal

# Absent (None) means the payload predates price-limit fields or the market
# has no Taiwan limit classification. API serializers map that to "unknown".
PriceLimitStatus = Literal["limit_up", "limit_down", "none", "unknown"]


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
    price_limit_status: PriceLimitStatus | None = None
    reference_price: float | None = None
    limit_up_price: float | None = None
    limit_down_price: float | None = None


@dataclass(frozen=True)
class CandleRecord:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int

