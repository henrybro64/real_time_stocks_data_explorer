from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.stock import StockResponse

TwBoard = Literal["TWSE", "TPEx"]
UsUniverse = Literal["DJIA", "NASDAQ100"]
RankingUniverse = Literal["TWSE", "TPEx", "DJIA", "NASDAQ100"]
_TW_UNIVERSES = {"TWSE", "TPEx"}
_US_UNIVERSES = {"DJIA", "NASDAQ100"}


def coerce_ranking_universe(
    market: Literal["US", "TW"],
    universe: RankingUniverse | None,
    tw_board: TwBoard | None = None,
) -> RankingUniverse:
    if market == "TW":
        value = universe or tw_board or "TWSE"
        if value not in _TW_UNIVERSES:
            raise ValueError("Taiwan rankings universe must be TWSE or TPEx")
        return value
    value = universe or "DJIA"
    if value not in _US_UNIVERSES:
        raise ValueError("U.S. rankings universe must be DJIA or NASDAQ100")
    return value


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
    # Selected ranking universe: TWSE/TPEx or DJIA/NASDAQ100.
    universe: RankingUniverse | None = None
    items: list[RankingItem]
    timestamp: str
    source: str
    sourceType: RankingSourceType
    isMarketOpen: bool
    dataFreshnessLabel: str
    warningMessage: str | None = None
    unsupportedReason: str | None = None
    # US-only: which universe/method backs the *current* tab so the scope banner stays honest
    # (DJIA / Nasdaq-100 constituent quotes, or legacy S&P 500 / Yahoo / quote paths).
    usRankingsScope: str | None = None
    # TW-only: Yahoo pre-ranked board vs configured quote universe vs hard-coded fallback.
    twRankingsScope: str | None = None
    # Present when the payload was loaded from the Azure SQL EOD archive (#168).
    tradingDate: str | None = None
    captureScope: str | None = None


class RankingsHistoryDatesResponse(BaseModel):
    market: Literal["US", "TW"]
    universe: RankingUniverse
    dates: list[str] = Field(
        description="Available archived trading dates (YYYY-MM-DD), newest first.",
    )


class RankingsRefreshRequest(BaseModel):
    market: Literal["US", "TW"]
    rankingType: RankingType
    universe: RankingUniverse
    limit: int = Field(default=50, ge=1, le=50)

    @model_validator(mode="after")
    def universe_matches_market(self) -> "RankingsRefreshRequest":
        coerce_ranking_universe(self.market, self.universe)
        return self
