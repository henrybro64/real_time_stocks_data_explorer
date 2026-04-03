from sqlalchemy import delete, select

from app.db.init_db import DEFAULT_USER_ID
from app.db.models import PortfolioAsset, PortfolioPosition
from app.db.session import SessionLocal
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.quote_service import quote_service


class PortfolioService:
    def list_positions(self) -> list[PortfolioPosition]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            positions = session.execute(
                select(PortfolioPosition)
                .where(PortfolioPosition.user_id == DEFAULT_USER_ID)
                .order_by(PortfolioPosition.created_at.asc()),
            ).scalars()
            return list(positions)

    def add_position(
        self,
        stock_id: str,
        quantity: float,
        average_cost: float,
        currency: str,
        notes: str | None,
    ) -> PortfolioPosition:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        self._validate_stock(stock_id)

        with SessionLocal() as session:
            existing = session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.user_id == DEFAULT_USER_ID,
                    PortfolioPosition.stock_id == stock_id,
                ),
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(
                    f"Position already exists for stock id: {stock_id}",
                )
            position = PortfolioPosition(
                user_id=DEFAULT_USER_ID,
                stock_id=stock_id,
                quantity=quantity,
                average_cost=average_cost,
                currency=currency,
                notes=notes,
            )
            session.add(position)
            session.commit()
            session.refresh(position)
            return position

    def update_position(
        self,
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
                    PortfolioPosition.user_id == DEFAULT_USER_ID,
                ),
            ).scalar_one_or_none()
            if position is None:
                raise ValueError(f"Position not found: {position_id}")
            if quantity is not None:
                position.quantity = quantity
            if average_cost is not None:
                position.average_cost = average_cost
            if currency is not None:
                position.currency = currency
            position.notes = notes
            session.commit()
            session.refresh(position)
            return position

    def delete_position(self, position_id: int) -> None:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            deleted = session.execute(
                delete(PortfolioPosition).where(
                    PortfolioPosition.id == position_id,
                    PortfolioPosition.user_id == DEFAULT_USER_ID,
                ),
            )
            session.commit()
            if deleted.rowcount == 0:
                raise ValueError(f"Position not found: {position_id}")

    def list_assets(self) -> list[PortfolioAsset]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            assets = session.execute(
                select(PortfolioAsset)
                .where(PortfolioAsset.user_id == DEFAULT_USER_ID)
                .order_by(PortfolioAsset.created_at.asc()),
            ).scalars()
            return list(assets)

    def add_asset(
        self,
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
                user_id=DEFAULT_USER_ID,
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
                    PortfolioAsset.user_id == DEFAULT_USER_ID,
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

    def delete_asset(self, asset_id: int) -> None:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            deleted = session.execute(
                delete(PortfolioAsset).where(
                    PortfolioAsset.id == asset_id,
                    PortfolioAsset.user_id == DEFAULT_USER_ID,
                ),
            )
            session.commit()
            if deleted.rowcount == 0:
                raise ValueError(f"Asset not found: {asset_id}")

    @staticmethod
    def build_metrics(
        position: PortfolioPosition,
        quote: QuoteRecord,
    ) -> tuple[float, float, float]:
        market_value = round(position.quantity * quote.last_price, 2)
        cost_basis = position.quantity * position.average_cost
        unrealized_pl = round(market_value - cost_basis, 2)
        unrealized_pl_percent = (
            0.0 if cost_basis == 0 else round((unrealized_pl / cost_basis) * 100, 2)
        )
        return market_value, unrealized_pl, unrealized_pl_percent

    def _validate_stock(self, stock_id: str) -> StockRecord:
        stock = quote_service.get_stock(stock_id)
        if stock is None:
            raise ValueError(f"Unknown stock id: {stock_id}")
        return stock


portfolio_service = PortfolioService()
