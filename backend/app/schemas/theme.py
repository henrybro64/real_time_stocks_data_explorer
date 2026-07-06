from pydantic import BaseModel

from app.schemas.stock import StockResponse


class ThemeResponse(BaseModel):
    key: str
    label: str


class ThemeStocksResponse(BaseModel):
    theme: ThemeResponse
    stocks: list[StockResponse]
