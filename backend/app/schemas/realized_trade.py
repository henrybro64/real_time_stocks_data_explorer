from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


RealizedTradeCurrency = Literal["TWD", "USD"]


class RealizedTradeValueFields(BaseModel):
    buyPrice: float = Field(gt=0)
    sellPrice: float = Field(gt=0)
    shares: float = Field(gt=0)


class RealizedTradeCreate(RealizedTradeValueFields):
    symbol: str = Field(min_length=1, max_length=32)
    currency: RealizedTradeCurrency
    buyDate: date | None = None
    sellDate: date
    cumulativeDividends: float = Field(default=0, ge=0)


class RealizedTradeUpdate(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    currency: RealizedTradeCurrency | None = None
    buyPrice: float | None = Field(default=None, gt=0)
    sellPrice: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    buyDate: date | None = None
    sellDate: date | None = None
    cumulativeDividends: float | None = Field(default=None, ge=0)


class RealizedTradeResponse(RealizedTradeValueFields):
    id: int
    symbol: str
    currency: RealizedTradeCurrency
    buyDate: str | None
    sellDate: str
    cumulativeDividends: float
    priceGain: float
    realizedGain: float
    realizedReturnPercent: float
    createdAt: str
    updatedAt: str
