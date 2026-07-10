from pydantic import BaseModel


class TaiwanInstitutionalInvestorSummaryResponse(BaseModel):
    foreignNetLots: float
    investmentTrustNetLots: float
    dealerNetLots: float
    totalNetLots: float
    dealerSelfNetLots: float | None = None
    dealerHedgeNetLots: float | None = None


class TaiwanInstitutionalInvestorHistoryPointResponse(BaseModel):
    date: str
    foreignNetLots: float
    investmentTrustNetLots: float
    dealerNetLots: float
    totalNetLots: float
    dealerSelfNetLots: float | None = None
    dealerHedgeNetLots: float | None = None


class TaiwanInstitutionalInvestorsResponse(BaseModel):
    stockId: str
    symbol: str
    market: str
    latestDate: str | None = None
    summary: TaiwanInstitutionalInvestorSummaryResponse | None = None
    history: list[TaiwanInstitutionalInvestorHistoryPointResponse]
    source: str
