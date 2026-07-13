from typing import Literal

from pydantic import BaseModel, Field


class TaiwanMarginShortSummaryResponse(BaseModel):
    """Balances/changes in 張 (1000 default; 100 for K/C FX ETFs); amount is 萬元 via (Δshares × close) / 10_000."""

    marginBalanceLots: float
    shortMarginBalanceLots: float
    borrowedShortBalanceLots: float
    borrowedShortBalanceChangeLots: float | None = None
    borrowedShortBalanceChangeAmount10k: int | None = Field(
        default=None,
        description="借券賣出餘額增減金額(萬); null when previous balance or close is missing",
    )


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
    sharesPerLot: int
    source: str
