from enum import Enum

from pydantic import BaseModel

from app.schemas.stock import StockResponse


class RankingType(str, Enum):
    volume = "volume"
    volume_up = "volume_up"
    volume_down = "volume_down"
    net_buy = "net_buy"
    net_sell = "net_sell"


class RankingSourceType(str, Enum):
    free_public = "free-public"
    free_unofficial = "free-unofficial"
    cache = "cache"
    unsupported = "unsupported"


class RankingItem(BaseModel):
    rank: int
    stock: StockResponse
    lastPrice: float | None = None
    changePercent: float | None = None
    volume: int | None = None
    volumeDeltaPercent: float | None = None
    netBuySellShares: int | None = None
    timestamp: str


class RankingResponse(BaseModel):
    market: str
    rankingType: RankingType
    items: list[RankingItem]
    timestamp: str
    source: str
    sourceType: RankingSourceType
    isMarketOpen: bool
    dataFreshnessLabel: str
    warningMessage: str | None = None
    unsupportedReason: str | None = None
