from app.models.stock import StockRecord
from app.services.quote_service import quote_service


class SearchService:
    def search(self, query: str) -> list[StockRecord]:
        return quote_service.search(query)


search_service = SearchService()

