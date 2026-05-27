from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.stock import StockResponse


AlertRuleType = Literal["price_above", "price_below"]


class AlertRuleCreate(BaseModel):
    stockId: str
    ruleType: AlertRuleType
    thresholdValue: float = Field(gt=0)
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    stockId: str | None = None
    ruleType: AlertRuleType | None = None
    thresholdValue: float | None = Field(default=None, gt=0)
    enabled: bool | None = None


class AlertRuleResponse(BaseModel):
    id: int
    stockId: str
    stock: StockResponse | None = None
    ruleType: AlertRuleType
    thresholdValue: float
    enabled: bool
    lastConditionMet: bool
    lastObservedPrice: float | None = None
    lastObservedQuoteTimestamp: str | None = None
    lastEvaluatedAt: str | None = None
    lastTriggeredAt: str | None = None
    createdAt: str
    updatedAt: str


class AlertEventResponse(BaseModel):
    id: int
    alertRuleId: int | None = None
    stockId: str
    stock: StockResponse | None = None
    ruleType: AlertRuleType
    thresholdValue: float
    triggeredAt: str
    observedPrice: float
    quoteTimestamp: str | None = None
    message: str
