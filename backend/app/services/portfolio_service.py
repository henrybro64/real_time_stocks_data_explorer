from app.db.models import PortfolioAsset, PortfolioPosition
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.repositories.portfolio_repository import (
    UNSET as REPOSITORY_UNSET,
    portfolio_repository,
)
from app.services.quote_refresher_service import quote_refresher_service
from app.services.quote_service import quote_service
from app.services.user_quota_service import (
    enforce_portfolio_asset_cap,
    enforce_portfolio_position_cap,
)

SUPPORTED_PORTFOLIO_CURRENCIES = frozenset({"USD", "TWD"})


class PortfolioService:
    def list_positions(self, user_id: str) -> list[PortfolioPosition]:
        return portfolio_repository.list_positions(user_id)

    def add_position(
        self,
        user_id: str,
        stock_id: str,
        quantity: float,
        average_cost: float,
        currency: str,
        notes: str | None,
    ) -> PortfolioPosition:
        stock = self._validate_stock(stock_id)
        # Persist the canonical id so US:aapl and US:AAPL share one row (#275).
        canonical_id = stock.id
        normalized_currency = self._validate_holding_currency(stock, currency)
        existing = portfolio_repository.find_position_by_stock(user_id, canonical_id)
        if existing is not None:
            raise ValueError(
                f"Position already exists for stock id: {canonical_id}",
            )
        enforce_portfolio_position_cap(
            user_id,
            current_count=portfolio_repository.count_positions(user_id),
        )
        try:
            position = portfolio_repository.create_position(
                user_id=user_id,
                stock_id=canonical_id,
                quantity=quantity,
                average_cost=average_cost,
                currency=normalized_currency,
                notes=notes,
            )
        except Exception as exc:
            # Concurrent inserts race past the existence check (#275).
            from app.db.constraint_errors import is_user_stock_unique_violation

            if is_user_stock_unique_violation(
                exc,
                constraint="uq_portfolio_positions_user_stock",
                table="portfolio_positions",
            ):
                raise ValueError(
                    f"Position already exists for stock id: {canonical_id}",
                ) from exc
            raise
        quote_refresher_service.request_immediate_refresh(canonical_id)
        return position

    def update_position(
        self,
        user_id: str,
        position_id: int,
        quantity: float | None,
        average_cost: float | None,
        currency: str | None,
        notes: str | None,
    ) -> PortfolioPosition:
        position = portfolio_repository.update_position(
            user_id,
            position_id,
            quantity=(
                quantity if quantity is not None else REPOSITORY_UNSET
            ),
            average_cost=(
                average_cost if average_cost is not None else REPOSITORY_UNSET
            ),
            currency=(
                self._validated_position_currency(user_id, position_id, currency)
                if currency is not None
                else REPOSITORY_UNSET
            ),
            notes=notes,
        )
        if position is None:
            raise ValueError(f"Position not found: {position_id}")
        return position

    def delete_position(self, user_id: str, position_id: int) -> None:
        deleted = portfolio_repository.delete_position(user_id, position_id)
        if deleted == 0:
            raise ValueError(f"Position not found: {position_id}")

    def list_assets(self, user_id: str) -> list[PortfolioAsset]:
        return portfolio_repository.list_assets(user_id)

    def add_asset(
        self,
        user_id: str,
        asset_type: str,
        name: str,
        currency: str,
        value: float,
        notes: str | None,
    ) -> PortfolioAsset:
        enforce_portfolio_asset_cap(
            user_id,
            current_count=portfolio_repository.count_assets(user_id),
        )
        return portfolio_repository.create_asset(
            user_id=user_id,
            asset_type=asset_type,
            name=name,
            currency=currency,
            value=value,
            notes=notes,
        )

    def update_asset(
        self,
        user_id: str,
        asset_id: int,
        asset_type: str | None,
        name: str | None,
        currency: str | None,
        value: float | None,
        notes: str | None,
    ) -> PortfolioAsset:
        asset = portfolio_repository.update_asset(
            user_id,
            asset_id,
            asset_type=asset_type if asset_type is not None else REPOSITORY_UNSET,
            name=name if name is not None else REPOSITORY_UNSET,
            currency=currency if currency is not None else REPOSITORY_UNSET,
            value=value if value is not None else REPOSITORY_UNSET,
            notes=notes,
        )
        if asset is None:
            raise ValueError(f"Asset not found: {asset_id}")
        return asset

    def delete_asset(self, user_id: str, asset_id: int) -> None:
        deleted = portfolio_repository.delete_asset(user_id, asset_id)
        if deleted == 0:
            raise ValueError(f"Asset not found: {asset_id}")

    @staticmethod
    def build_cost_basis(position: PortfolioPosition) -> float:
        return round(position.quantity * position.average_cost, 2)

    @staticmethod
    def convert_to_usd(currency: str, value: float, usd_twd_rate: float) -> float:
        normalized = PortfolioService.normalize_currency(currency)
        if normalized == "USD":
            return value
        if normalized == "TWD":
            return value / usd_twd_rate if usd_twd_rate > 0 else 0.0
        return 0.0

    @staticmethod
    def build_metrics(
        position: PortfolioPosition,
        quote: QuoteRecord | None,
    ) -> tuple[float | None, float | None, float | None]:
        """Market value and P/L from a live quote only.

        Without a trustworthy quote, return ``None`` values rather than
        substituting cost basis as if it were a current price (#265).
        """
        cost_basis = PortfolioService.build_cost_basis(position)
        if quote is None:
            return None, None, None
        market_value = round(position.quantity * quote.last_price, 2)
        unrealized_pl = round(market_value - cost_basis, 2)
        unrealized_pl_percent = (
            0.0 if cost_basis == 0 else round((unrealized_pl / cost_basis) * 100, 2)
        )
        return market_value, unrealized_pl, unrealized_pl_percent

    @staticmethod
    def normalize_currency(currency: str) -> str:
        return currency.strip().upper()

    @staticmethod
    def _validate_holding_currency(stock: StockRecord, currency: str) -> str:
        normalized = PortfolioService.normalize_currency(currency)
        stock_currency = PortfolioService.normalize_currency(stock.currency)
        if normalized != stock_currency:
            raise ValueError(
                "Holding currency does not match the stock currency: "
                f"{currency!r} vs {stock.currency!r}",
            )
        if normalized not in SUPPORTED_PORTFOLIO_CURRENCIES:
            raise ValueError(
                f"Unsupported holding currency: {currency!r}. Only USD and TWD are supported.",
            )
        return normalized

    def _validated_position_currency(
        self,
        user_id: str,
        position_id: int,
        currency: str,
    ) -> str:
        positions = portfolio_repository.list_positions(user_id)
        position = next((row for row in positions if row.id == position_id), None)
        if position is None:
            raise ValueError(f"Position not found: {position_id}")
        stock = quote_service.get_stock(position.stock_id)
        if stock is None:
            raise ValueError(f"Unknown stock id: {position.stock_id}")
        return self._validate_holding_currency(stock, currency)

    @staticmethod
    def summarize_currency_buckets(
        entries: list[tuple[str, float]],
        usd_twd_rate: float,
    ) -> dict[str, float]:
        total_usd = round(
            sum(
                value
                for currency, value in entries
                if PortfolioService.normalize_currency(currency) == "USD"
            ),
            2,
        )
        total_twd = round(
            sum(
                value
                for currency, value in entries
                if PortfolioService.normalize_currency(currency) == "TWD"
            ),
            2,
        )
        combined_usd = round(
            total_usd + (total_twd / usd_twd_rate if usd_twd_rate > 0 else 0.0),
            2,
        )
        combined_twd = round(
            total_twd + (total_usd * usd_twd_rate if usd_twd_rate > 0 else 0.0),
            2,
        )
        return {
            "totalUsd": total_usd,
            "totalTwd": total_twd,
            "combinedUsd": combined_usd,
            "combinedTwd": combined_twd,
        }

    @staticmethod
    def summarize_position_buckets(
        entries: list[tuple[str, float, float]],
        usd_twd_rate: float,
    ) -> dict[str, float]:
        market_totals = PortfolioService.summarize_currency_buckets(
            [(currency, market_value) for currency, market_value, _ in entries],
            usd_twd_rate,
        )
        cost_totals = PortfolioService.summarize_currency_buckets(
            [(currency, cost_basis) for currency, _, cost_basis in entries],
            usd_twd_rate,
        )
        combined_gain_usd = round(
            market_totals["combinedUsd"] - cost_totals["combinedUsd"],
            2,
        )
        combined_gain_twd = round(
            market_totals["combinedTwd"] - cost_totals["combinedTwd"],
            2,
        )
        combined_gain_percent = (
            0.0
            if cost_totals["combinedUsd"] == 0
            else round((combined_gain_usd / cost_totals["combinedUsd"]) * 100, 2)
        )
        return {
            **market_totals,
            "totalCostUsd": cost_totals["totalUsd"],
            "totalCostTwd": cost_totals["totalTwd"],
            "combinedCostUsd": cost_totals["combinedUsd"],
            "combinedCostTwd": cost_totals["combinedTwd"],
            "combinedGainUsd": combined_gain_usd,
            "combinedGainTwd": combined_gain_twd,
            "combinedGainPercent": combined_gain_percent,
        }

    @staticmethod
    def summarize_allocation_percents(
        entries: list[tuple[str, float]],
        usd_twd_rate: float,
    ) -> list[float]:
        market_values_usd = [
            PortfolioService.convert_to_usd(currency, market_value, usd_twd_rate)
            for currency, market_value in entries
        ]
        total_market_value_usd = sum(market_values_usd)
        if total_market_value_usd <= 0:
            return [0.0] * len(entries)

        return [
            round((market_value_usd / total_market_value_usd) * 100, 2)
            for market_value_usd in market_values_usd
        ]

    def _validate_stock(self, stock_id: str) -> StockRecord:
        stock = quote_service.get_stock(stock_id)
        if stock is None:
            raise ValueError(f"Unknown stock id: {stock_id}")
        stock_currency = self.normalize_currency(stock.currency)
        if stock_currency not in SUPPORTED_PORTFOLIO_CURRENCIES:
            raise ValueError(
                f"Unsupported stock currency: {stock.currency!r}. Only USD and TWD are supported.",
            )
        return stock


portfolio_service = PortfolioService()
