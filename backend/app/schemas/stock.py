from pydantic import BaseModel


class StockResponse(BaseModel):
    id: str
    market: str
    symbol: str
    name: str
    exchange: str
    currency: str
    timezone: str
    nameEn: str | None = None
    nameZhTw: str | None = None

