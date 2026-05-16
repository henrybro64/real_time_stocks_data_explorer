from pydantic import BaseModel, Field


class StockAnalysisCitationResponse(BaseModel):
    url: str
    title: str | None = None
    source: str | None = None


class StockAnalysisSearchResultResponse(BaseModel):
    title: str
    url: str
    source: str | None = None


class ComputedStockAnalysisResponse(BaseModel):
    ticker: str
    companyName: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    marketCap: int | None = None
    trailingPe: float | None = None
    forwardPe: float | None = None
    currentPrice: float | None = None
    return1mPercent: float | None = None
    return3mPercent: float | None = None
    return6mPercent: float | None = None
    return1yPercent: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    priceVsSma50Percent: float | None = None
    priceVsSma200Percent: float | None = None
    rsi14: float | None = None
    volatility30dAnnualized: float | None = None
    maxDrawdown1yPercent: float | None = None
    averageVolume30d: int | None = None


class StockAnalysisResponse(BaseModel):
    stockId: str
    ticker: str
    computedData: ComputedStockAnalysisResponse
    aiProvider: str | None = None
    aiModel: str | None = None
    aiAnalysis: str | None = None
    analyzedAt: str | None = Field(
        default=None,
        description="UTC instant when the cached AI narrative was produced (refresh), if any.",
    )
    citations: list[StockAnalysisCitationResponse] = Field(default_factory=list)
    searchResults: list[StockAnalysisSearchResultResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
