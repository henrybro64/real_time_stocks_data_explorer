from pydantic import BaseModel


class TaiwanInstitutionalInvestorSummaryResponse(BaseModel):
    """Net values in 張 using symbol lot size (1000 default; 100 for K/C FX ETFs)."""

    foreignNetLots: float
    investmentTrustNetLots: float
    dealerNetLots: float
    totalNetLots: float
    dealerSelfNetLots: float | None = None
    dealerHedgeNetLots: float | None = None


class TaiwanInstitutionalInvestorHistoryPointResponse(BaseModel):
    """History point in 張; same lot-size rules as the summary."""

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
    sharesPerLot: int
    source: str
