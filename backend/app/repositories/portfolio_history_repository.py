from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.db.models import PortfolioHistorySnapshot, User

from .session import database_is_configured, require_session_factory


class PortfolioHistoryRepository:
    def is_available(self) -> bool:
        return database_is_configured()

    def list_user_ids(self) -> list[str]:
        session_factory = require_session_factory()
        with session_factory() as session:
            return list(session.execute(select(User.id)).scalars())

    def upsert_snapshots(
        self,
        *,
        user_id: str,
        windows: list[tuple[str, datetime]],
        captured_at: datetime,
        fx_rate: float,
        holdings_value_usd: float,
        holdings_value_twd: float,
        combined_assets_usd: float,
        combined_assets_twd: float,
    ) -> int:
        from app.repositories.session import _dialect_insert

        session_factory = require_session_factory()
        payloads = [
            {
                "user_id": user_id,
                "snapshot_hour": snapshot_hour,
                "source_window": source_window,
                "captured_at": captured_at,
                "usd_twd_rate": fx_rate,
                "holdings_value_usd": holdings_value_usd,
                "holdings_value_twd": holdings_value_twd,
                "combined_assets_usd": combined_assets_usd,
                "combined_assets_twd": combined_assets_twd,
            }
            for source_window, snapshot_hour in windows
        ]
        if not payloads:
            return 0
        with session_factory() as session:
            insert = _dialect_insert(session)
            if insert is None:
                raise RuntimeError("Unsupported database dialect for snapshot upsert")
            stmt = insert(PortfolioHistorySnapshot).values(payloads)
            excluded = stmt.excluded
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["user_id", "snapshot_hour", "source_window"],
                    set_={
                        "captured_at": excluded.captured_at,
                        "usd_twd_rate": excluded.usd_twd_rate,
                        "holdings_value_usd": excluded.holdings_value_usd,
                        "holdings_value_twd": excluded.holdings_value_twd,
                        "combined_assets_usd": excluded.combined_assets_usd,
                        "combined_assets_twd": excluded.combined_assets_twd,
                    },
                )
            )
            session.commit()
            return len(payloads)

    def list_snapshots_since(
        self,
        user_id: str,
        cutoff: datetime,
    ) -> list[PortfolioHistorySnapshot]:
        session_factory = require_session_factory()
        with session_factory() as session:
            return list(
                session.execute(
                    select(PortfolioHistorySnapshot)
                    .where(
                        PortfolioHistorySnapshot.user_id == user_id,
                        PortfolioHistorySnapshot.snapshot_hour >= cutoff,
                    )
                    .order_by(
                        PortfolioHistorySnapshot.snapshot_hour.asc(),
                        PortfolioHistorySnapshot.source_window.asc(),
                    )
                ).scalars()
            )


portfolio_history_repository = PortfolioHistoryRepository()
