from enum import Enum
from typing import Literal

from pydantic import BaseModel

from app.schemas.stock import StockResponse

TwBoard = Literal["TWSE", "TPEx"]


class RankingType(str, Enum):
    volume = "volume"
    volume_up = "volume_up"
    volume_down = "volume_down"
    change_up = "change_up"
    change_down = "change_down"
    valuation_low_pe = "valuation_low_pe"
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
    peRatio: float | None = None
    peRatioType: str | None = None
    peRatioSource: str | None = None
    valuationAsOf: str | None = None
    valuationWarning: str | None = None
    timestamp: str


class RankingResponse(BaseModel):
    market: str
    rankingType: RankingType
    # When ``market`` is TW, which listing segment this response is scoped to.
    twBoard: TwBoard | None = None
    items: list[RankingItem]
    timestamp: str
    source: str
    sourceType: RankingSourceType
    isMarketOpen: bool
    dataFreshnessLabel: str
    warningMessage: str | None = None
    unsupportedReason: str | None = None
    # US-only: which universe/method backs the *current* tab so the scope banner stays honest
    # (S&P 500 batch quotes vs Yahoo screeners vs the app's quote snapshot list).
    usRankingsScope: str | None = None
