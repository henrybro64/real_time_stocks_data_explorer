from typing import Literal

from pydantic import BaseModel

OwnershipComponentStatus = Literal[
    "available",
    "not_published",
    "unsupported",
    "temporarily_unavailable",
    "no_data_for_period",
]


class OwnershipComponentStateResponse(BaseModel):
    status: OwnershipComponentStatus
    reason: str | None = None


class OwnershipDataStatusResponse(BaseModel):
    tdcc: OwnershipComponentStateResponse
    foreignOwnership: OwnershipComponentStateResponse
    weeklyClose: OwnershipComponentStateResponse


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
    currency: str | None = "TWD"
    dataStatus: OwnershipDataStatusResponse
    points: list[TaiwanOwnershipConcentrationPointResponse]
