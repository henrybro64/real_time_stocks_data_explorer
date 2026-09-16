from pydantic import BaseModel


class TaiwanOwnershipConcentrationPointResponse(BaseModel):
    date: str
    holders400LotsOrMorePercent: float | None = None
    foreignOwnershipPercent: float | None = None
    investmentTrustOwnershipPercent: float | None = None
    dealerOwnershipPercent: float | None = None
    directorOwnershipPercent: float | None = None
    weeklyClose: float | None = None


class TaiwanOwnershipConcentrationResponse(BaseModel):
    stockId: str
    range: str
    asOf: str | None = None
    source: str
    stale: bool = False
    points: list[TaiwanOwnershipConcentrationPointResponse]
