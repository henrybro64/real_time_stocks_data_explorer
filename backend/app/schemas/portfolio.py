from pydantic import BaseModel, Field

from app.schemas.quote import ChartLayoutResponse, QuoteResponse
from app.schemas.stock import StockResponse


class PortfolioPositionCreate(BaseModel):
    stockId: str
    quantity: float = Field(gt=0)
    averageCost: float = Field(gt=0)
    currency: str
    notes: str | None = None


class PortfolioPositionUpdate(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    averageCost: float | None = Field(default=None, gt=0)
    currency: str | None = None
    notes: str | None = None


class PortfolioPositionResponse(BaseModel):
    id: int
    stockId: str
    quantity: float
    averageCost: float
    currency: str
    notes: str | None = None
    stock: StockResponse
    quote: QuoteResponse | None = None
    marketValue: float
    unrealizedPl: float
    unrealizedPlPercent: float


class PortfolioAssetCreate(BaseModel):
    assetType: str
    name: str
    currency: str
    value: float = Field(gt=0)
    notes: str | None = None


class PortfolioAssetUpdate(BaseModel):
    assetType: str | None = None
    name: str | None = None
    currency: str | None = None
    value: float | None = Field(default=None, gt=0)
    notes: str | None = None


class PortfolioAssetResponse(BaseModel):
    id: int
    assetType: str
    name: str
    currency: str
    value: float
    notes: str | None = None


class PortfolioSummaryPosition(BaseModel):
    stockId: str
    symbol: str
    currency: str
    quantity: float
    lastPrice: float | None = None
    marketValue: float
    timestamp: str | None = None


class PortfolioSummaryResponse(BaseModel):
    totalUsd: float
    totalTwd: float
    combinedUsd: float
    combinedTwd: float
    usdTwdRate: float
    fxTimestamp: str
    positions: list[PortfolioSummaryPosition]
    skippedStockIds: list[str]


class PortfolioAssetSummaryResponse(BaseModel):
    totalUsd: float
    totalTwd: float
    combinedUsd: float
    combinedTwd: float
    liquidTotalUsd: float
    liquidTotalTwd: float
    liquidCombinedUsd: float
    liquidCombinedTwd: float
    usdTwdRate: float
    fxTimestamp: str
    skippedStockIds: list[str]


class PortfolioHistoryPointResponse(BaseModel):
    timestamp: str
    sourceWindow: str
    fxRate: float
    value: float


class PortfolioHistorySeriesResponse(BaseModel):
    currency: str
    points: list[PortfolioHistoryPointResponse]
    chartLayout: ChartLayoutResponse | None = None


class PortfolioHistoryResponse(BaseModel):
    range: str
    metric: str
    usd: PortfolioHistorySeriesResponse
    twd: PortfolioHistorySeriesResponse
