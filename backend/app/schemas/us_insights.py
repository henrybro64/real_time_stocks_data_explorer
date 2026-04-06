from pydantic import BaseModel


class USPeerCompanyReturnsResponse(BaseModel):
    change1mPercent: float
    change3mPercent: float
    change6mPercent: float
    change1yPercent: float


class USPeerMoverResponse(BaseModel):
    rank: int
    symbol: str
    change1mPercent: float
    change3mPercent: float
    change6mPercent: float
    change1yPercent: float


class USPeerInsightResponse(BaseModel):
    asOf: str
    sector: str
    industry: str
    compareUrl: str
    companySymbol: str
    companyRankBy1m: int | None = None
    companyCount: int
    companyReturns: USPeerCompanyReturnsResponse | None = None
    topMovers: list[USPeerMoverResponse]


class USStockInsightsResponse(BaseModel):
    peer: USPeerInsightResponse
