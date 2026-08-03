from __future__ import annotations

from sqlalchemy import delete, select

from app.db.models import PortfolioAsset, PortfolioPosition

from .session import database_is_configured, require_session_factory


UNSET = object()


class PortfolioRepository:
    def is_available(self) -> bool:
        return database_is_configured()

    def list_positions(self, user_id: str) -> list[PortfolioPosition]:
        session_factory = require_session_factory()
        with session_factory() as session:
            positions = session.execute(
                select(PortfolioPosition)
                .where(PortfolioPosition.user_id == user_id)
                .order_by(PortfolioPosition.created_at.asc()),
            ).scalars()
            return list(positions)

    def list_distinct_position_stock_ids(self) -> list[str]:
        """Return every stock ID held in any user's portfolio (deduplicated)."""
        session_factory = require_session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(PortfolioPosition.stock_id)
                .distinct()
                .order_by(PortfolioPosition.stock_id.asc()),
            ).all()
        return [row[0] for row in rows]

    def find_position_by_stock(
        self,
        user_id: str,
        stock_id: str,
    ) -> PortfolioPosition | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            return session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.user_id == user_id,
                    PortfolioPosition.stock_id == stock_id,
                ),
            ).scalar_one_or_none()

    def create_position(
        self,
        *,
        user_id: str,
        stock_id: str,
        quantity: float,
        average_cost: float,
        currency: str,
        notes: str | None,
    ) -> PortfolioPosition:
        session_factory = require_session_factory()
        with session_factory() as session:
            position = PortfolioPosition(
                user_id=user_id,
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
        user_id: str,
        position_id: int,
        *,
        quantity: float | object = UNSET,
        average_cost: float | object = UNSET,
        currency: str | object = UNSET,
        notes: str | None = None,
    ) -> PortfolioPosition | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            position = session.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.id == position_id,
                    PortfolioPosition.user_id == user_id,
                ),
            ).scalar_one_or_none()
            if position is None:
                return None
            if quantity is not UNSET:
                position.quantity = quantity
            if average_cost is not UNSET:
                position.average_cost = average_cost
            if currency is not UNSET:
                position.currency = currency
            position.notes = notes
            session.commit()
            session.refresh(position)
            return position

    def delete_position(self, user_id: str, position_id: int) -> int:
        session_factory = require_session_factory()
        with session_factory() as session:
            deleted = session.execute(
                delete(PortfolioPosition).where(
                    PortfolioPosition.id == position_id,
                    PortfolioPosition.user_id == user_id,
                ),
            )
            session.commit()
            return deleted.rowcount or 0

    def list_assets(self, user_id: str) -> list[PortfolioAsset]:
        session_factory = require_session_factory()
        with session_factory() as session:
            assets = session.execute(
                select(PortfolioAsset)
                .where(PortfolioAsset.user_id == user_id)
                .order_by(PortfolioAsset.created_at.asc()),
            ).scalars()
            return list(assets)

    def create_asset(
        self,
        *,
        user_id: str,
        asset_type: str,
        name: str,
        currency: str,
        value: float,
        notes: str | None,
    ) -> PortfolioAsset:
        session_factory = require_session_factory()
        with session_factory() as session:
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
        *,
        asset_type: str | object = UNSET,
        name: str | object = UNSET,
        currency: str | object = UNSET,
        value: float | object = UNSET,
        notes: str | None = None,
    ) -> PortfolioAsset | None:
        session_factory = require_session_factory()
        with session_factory() as session:
            asset = session.execute(
                select(PortfolioAsset).where(
                    PortfolioAsset.id == asset_id,
                    PortfolioAsset.user_id == user_id,
                ),
            ).scalar_one_or_none()
            if asset is None:
                return None
            if asset_type is not UNSET:
                asset.asset_type = asset_type
            if name is not UNSET:
                asset.name = name
            if currency is not UNSET:
                asset.currency = currency
            if value is not UNSET:
                asset.value = value
            asset.notes = notes
            session.commit()
            session.refresh(asset)
            return asset

    def delete_asset(self, user_id: str, asset_id: int) -> int:
        session_factory = require_session_factory()
        with session_factory() as session:
            deleted = session.execute(
                delete(PortfolioAsset).where(
                    PortfolioAsset.id == asset_id,
                    PortfolioAsset.user_id == user_id,
                ),
            )
            session.commit()
            return deleted.rowcount or 0


portfolio_repository = PortfolioRepository()
