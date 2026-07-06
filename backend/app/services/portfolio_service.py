from sqlalchemy import delete, select

from app.db.models import PortfolioAsset, PortfolioPosition
from app.db.session import SessionLocal
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.quote_service import quote_service

SUPPORTED_PORTFOLIO_CURRENCIES = frozenset({"USD", "TWD"})


class PortfolioService:
    def list_positions(self, user_id: str) -> list[PortfolioPosition]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            positions = session.execute(
                select(PortfolioPosition)
                .where(PortfolioPosition.user_id == user_id)
                .order_by(PortfolioPosition.created_at.asc()),
            ).scalars()
            return list(positions)

    def add_position(
        self,
        user_id: str,
        stock_id: str,
        quantity: float,
        average_cost: float,
        currency: str,
        notes: str | None,
    ) -> PortfolioPosition:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        stock = self._validate_stock(stock_id)
        normalized_currency = self._validate_holding_currency(stock, currency)

        with SessionLocal() as session:
            existing = session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.user_id == user_id,
                    PortfolioPosition.stock_id == stock_id,
                ),
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(
                    f"Position already exists for stock id: {stock_id}",
                )
            position = PortfolioPosition(
                user_id=user_id,
                stock_id=stock_id,
                quantity=quantity,
                average_cost=average_cost,
                currency=normalized_currency,
                notes=notes,
            )
            session.add(position)
            session.commit()
            session.refresh(position)
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
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            position = session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.id == position_id,
                    PortfolioPosition.user_id == user_id,
                ),
            ).scalar_one_or_none()
            if position is None:
                raise ValueError(f"Position not found: {position_id}")
            stock = quote_service.get_stock(position.stock_id)
            if stock is None:
                raise ValueError(f"Unknown stock id: {position.stock_id}")
            if quantity is not None:
                position.quantity = quantity
            if average_cost is not None:
                position.average_cost = average_cost
            if currency is not None:
                position.currency = self._validate_holding_currency(stock, currency)
            position.notes = notes
            session.commit()
            session.refresh(position)
            return position

    def delete_position(self, user_id: str, position_id: int) -> None:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            deleted = session.execute(
                delete(PortfolioPosition).where(
                    PortfolioPosition.id == position_id,
                    PortfolioPosition.user_id == user_id,
                ),
            )
            session.commit()
            if deleted.rowcount == 0:
                raise ValueError(f"Position not found: {position_id}")

    def list_assets(self, user_id: str) -> list[PortfolioAsset]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            assets = session.execute(
                select(PortfolioAsset)
                .where(PortfolioAsset.user_id == user_id)
                .order_by(PortfolioAsset.created_at.asc()),
            ).scalars()
            return list(assets)

    def add_asset(
        self,
        user_id: str,
        asset_type: str,
        name: str,
        currency: str,
        value: float,
        notes: str | None,
    ) -> PortfolioAsset:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            asset = PortfolioAsset(
                user_id=user_id,
                asset_type=asset_type,
                name=name,
                currency=currency,
                value=value,
                notes=notes,
            )
            session.add(asset)
            session.commit()
            session.refresh(asset)
            return asset

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
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            asset = session.execute(
                select(PortfolioAsset).where(
                    PortfolioAsset.id == asset_id,
                    PortfolioAsset.user_id == user_id,
                ),
            ).scalar_one_or_none()
            if asset is None:
                raise ValueError(f"Asset not found: {asset_id}")
            if asset_type is not None:
                asset.asset_type = asset_type
            if name is not None:
                asset.name = name
            if currency is not None:
                asset.currency = currency
            if value is not None:
                asset.value = value
            asset.notes = notes
            session.commit()
            session.refresh(asset)
            return asset

    def delete_asset(self, user_id: str, asset_id: int) -> None:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            deleted = session.execute(
                delete(PortfolioAsset).where(
                    PortfolioAsset.id == asset_id,
                    PortfolioAsset.user_id == user_id,
                ),
            )
            session.commit()
            if deleted.rowcount == 0:
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
    ) -> tuple[float, float, float]:
        cost_basis = PortfolioService.build_cost_basis(position)
        if quote is None:
            market_value = cost_basis
            return market_value, 0.0, 0.0
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
