from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


RealizedTradeCurrency = Literal["TWD", "USD"]


class RealizedTradeCreate(BaseModel):
    symbol: str
    currency: RealizedTradeCurrency
    buyPrice: float = Field(gt=0)
    sellPrice: float = Field(gt=0)
    shares: float = Field(gt=0)
    buyDate: date | None = None
    sellDate: date


class RealizedTradeUpdate(BaseModel):
    symbol: str | None = None
    currency: RealizedTradeCurrency | None = None
    buyPrice: float | None = Field(default=None, gt=0)
    sellPrice: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    buyDate: date | None = None
    sellDate: date | None = None


class RealizedTradeResponse(BaseModel):
    id: int
    symbol: str
    currency: RealizedTradeCurrency
    buyPrice: float
    sellPrice: float
    shares: float
    buyDate: str | None
    sellDate: str
    realizedGain: float
    realizedReturnPercent: float
    createdAt: str
    updatedAt: str
