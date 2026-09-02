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


class RealizedTradeUpdate(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    currency: RealizedTradeCurrency | None = None
    buyPrice: float | None = Field(default=None, gt=0)
    sellPrice: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    buyDate: date | None = None
    sellDate: date | None = None


class RealizedTradeResponse(RealizedTradeValueFields):
    id: int
    symbol: str
    currency: RealizedTradeCurrency
    buyDate: str | None
    sellDate: str
    realizedGain: float
    realizedReturnPercent: float
    createdAt: str
    updatedAt: str
