from pydantic import BaseModel


class TaiwanProfitabilityPointResponse(BaseModel):
    fiscalQuarter: str
    periodEnd: str
    grossMarginPercent: float | None = None
    operatingMarginPercent: float | None = None
    pretaxMarginPercent: float | None = None
    netMarginPercent: float | None = None
    quarterEndClose: float | None = None


class TaiwanProfitabilityResponse(BaseModel):
    stockId: str
    quarters: int
    asOf: str | None = None
    source: str
    stale: bool = False
    points: list[TaiwanProfitabilityPointResponse]
