from typing import Literal

from pydantic import BaseModel

from app.models.quote import PriceLimitStatus, QuoteRecord
from app.schemas.stock import StockResponse


SmaDirectionResponse = Literal["up", "down", "flat"]
QuoteCacheSourceResponse = Literal["redis", "memory", "provider", "none"]
QuoteRefreshStatusResponse = Literal["idle", "queued", "refreshing", "unavailable"]
_PRICE_LIMIT_STATUSES = {"limit_up", "limit_down", "none", "unknown"}


class QuoteResponse(BaseModel):
    id: str
    lastPrice: float
    change: float
    changePercent: float
    previousClose: float
    timestamp: str
    isMarketOpen: bool
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    priceLimitStatus: PriceLimitStatus = "unknown"
    referencePrice: float | None = None
    limitUpPrice: float | None = None
    limitDownPrice: float | None = None


def quote_to_response(quote: QuoteRecord) -> QuoteResponse:
    """Map a quote record onto the public contract.

    U.S. quotes and any payload without a trustworthy Taiwan classification
    publish ``unknown`` and null limit prices. Callers must not infer a limit
    from ``changePercent``.
    """
    status = quote.price_limit_status if quote.price_limit_status in _PRICE_LIMIT_STATUSES else "unknown"
    return QuoteResponse(
        id=quote.id,
        lastPrice=quote.last_price,
        change=quote.change,
        changePercent=quote.change_percent,
        previousClose=quote.previous_close,
        open=quote.open,
        high=quote.high,
        low=quote.low,
        volume=quote.volume,
        timestamp=quote.timestamp,
        isMarketOpen=quote.is_market_open,
        priceLimitStatus=status,
        referencePrice=quote.reference_price,
        limitUpPrice=quote.limit_up_price,
        limitDownPrice=quote.limit_down_price,
    )


class QuoteFreshnessResponse(BaseModel):
    cachedAt: str | None = None
    ageSeconds: float | None = None
    stale: bool = False
    source: QuoteCacheSourceResponse = "none"
    refreshStatus: QuoteRefreshStatusResponse = "idle"


class CandleResponse(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class QuoteEnvelope(BaseModel):
    stock: StockResponse
    quote: QuoteResponse | None = None
    freshness: QuoteFreshnessResponse | None = None


class ChartPoint(BaseModel):
    x: float
    y: float


class ChartLayoutResponse(BaseModel):
    minY: float
    maxY: float
    pointsPolyline: str
    points: list[ChartPoint]
    yAxisTicks: list[float]
    xAxisTickIndices: list[int]
    firstClose: float
    lastClose: float
    trendUp: bool


class SmaPointResponse(BaseModel):
    time: str
    value: float | None = None
    direction: SmaDirectionResponse | None = None


class MovingAverageSeriesResponse(BaseModel):
    sma10: list[SmaPointResponse]
    sma20: list[SmaPointResponse]
    sma60: list[SmaPointResponse]


class HistoryEnvelope(BaseModel):
    stock: StockResponse
    range: str
    candles: list[CandleResponse]
    chartLayout: ChartLayoutResponse | None = None
    movingAverages: MovingAverageSeriesResponse | None = None

