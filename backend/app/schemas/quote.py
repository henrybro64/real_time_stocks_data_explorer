from pydantic import BaseModel

from app.schemas.stock import StockResponse


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


class CandleResponse(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class QuoteEnvelope(BaseModel):
    stock: StockResponse
    quote: QuoteResponse


class HistoryEnvelope(BaseModel):
    stock: StockResponse
    range: str
    candles: list[CandleResponse]

