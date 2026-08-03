from typing import Literal

from pydantic import BaseModel

from app.schemas.stock import StockResponse


SmaDirectionResponse = Literal["up", "down", "flat"]
QuoteCacheSourceResponse = Literal["redis", "memory", "provider", "none"]
QuoteRefreshStatusResponse = Literal["idle", "queued", "refreshing", "unavailable"]


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

