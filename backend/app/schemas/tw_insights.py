from pydantic import BaseModel


class TWRevenuePointResponse(BaseModel):
    month: str
    revenueK: int | None = None
    momPercent: float | None = None
    yoyPercent: float | None = None


class TWRevenueInsightResponse(BaseModel):
    status: str
    reason: str | None = None
    source: str | None = None
    reportingPeriod: str | None = None
    reportGeneratedDate: str | None = None
    retrievedAt: str | None = None
    lastUpdated: str | None = None
    latestRevenueK: int | None = None
    latestMomPercent: float | None = None
    latestYoyPercent: float | None = None
    stale: bool = False
    points: list[TWRevenuePointResponse] = []


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
    status: str
    reason: str | None = None
    source: str | None = None
    avgCashYield2Y: float | None = None
    years: list[TWDividendYearResponse] = []


class TWStockInsightsResponse(BaseModel):
    revenue: TWRevenueInsightResponse
    peer: TWPeerInsightResponse
    dividends: TWDividendInsightResponse
