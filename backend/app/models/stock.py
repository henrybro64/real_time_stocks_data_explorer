from dataclasses import dataclass


@dataclass(frozen=True)
class StockRecord:
    id: str
    market: str
    symbol: str
    name: str
    exchange: str
    currency: str
    timezone: str
    name_en: str | None = None
    name_zh_tw: str | None = None

