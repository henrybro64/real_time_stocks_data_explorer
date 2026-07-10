from typing import Literal

from pydantic import BaseModel


class TaiwanMarginShortSummaryResponse(BaseModel):
    marginBalanceLots: float
    shortMarginBalanceLots: float
    borrowedShortBalanceLots: float
    borrowedShortBalanceChangeLots: float | None = None
    borrowedShortBalanceChangeAmount10k: int | None = None


class TaiwanMarginShortHistoryPointResponse(BaseModel):
    date: str
    closePrice: float | None = None
    marginBalanceLots: float
    shortMarginBalanceLots: float
    borrowedShortBalanceLots: float
    borrowedShortBalanceChangeLots: float | None = None
    borrowedShortBalanceChangeAmount10k: int | None = None


class TaiwanMarginShortResponse(BaseModel):
    stockId: str
    symbol: str
    market: Literal["TWSE", "TPEx"]
    latestDate: str | None = None
    summary: TaiwanMarginShortSummaryResponse | None = None
    history: list[TaiwanMarginShortHistoryPointResponse]
    source: str
