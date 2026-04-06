from pydantic import BaseModel


class TWRevenuePointResponse(BaseModel):
    month: str
    revenueK: int
    momPercent: float
    yoyPercent: float


class TWRevenueInsightResponse(BaseModel):
    lastUpdated: str
    latestRevenueK: int
    latestMomPercent: float
    latestYoyPercent: float
    points: list[TWRevenuePointResponse]


class TWPeerMetricResponse(BaseModel):
    label: str
    period: str
    companyPercent: float
    industryAveragePercent: float
    rank: int
    total: int


class TWPeerMoverResponse(BaseModel):
    rank: int
    name: str
    symbol: str
    change1mPercent: float
    change3mPercent: float
    change6mPercent: float
    change1yPercent: float


class TWPeerInsightResponse(BaseModel):
    sector: str
    asOf: str
    companyCount: int
    metrics: list[TWPeerMetricResponse]
    compareUrl: str | None = None
    topMovers: list[TWPeerMoverResponse] = []


class TWDividendYearResponse(BaseModel):
    year: int
    cashDividend: float
    stockDividend: float
    cashYieldPercent: float
    exDividendDate: str | None = None
    payDate: str | None = None


class TWDividendInsightResponse(BaseModel):
    avgCashYield2Y: float
    years: list[TWDividendYearResponse]


class TWStockInsightsResponse(BaseModel):
    revenue: TWRevenueInsightResponse
    peer: TWPeerInsightResponse
    dividends: TWDividendInsightResponse
