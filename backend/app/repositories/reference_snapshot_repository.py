"""Postgres copies of catalogs and holiday calendars (#264)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import ReferenceSnapshot

from .session import SessionBoundRepository


class ReferenceSnapshotRepository(SessionBoundRepository):
    def get_record(self, cache_key: str) -> tuple[dict, datetime] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(ReferenceSnapshot).where(ReferenceSnapshot.cache_key == cache_key)
            ).scalar_one_or_none()
            if row is None or not isinstance(row.payload, dict):
                return None
            return dict(row.payload), row.updated_at

    def get_payload(self, cache_key: str) -> dict | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(ReferenceSnapshot).where(ReferenceSnapshot.cache_key == cache_key)
            ).scalar_one_or_none()
            if row is None or not isinstance(row.payload, dict):
                return None
            return dict(row.payload)

    def put_payload(self, cache_key: str, payload: dict) -> None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(ReferenceSnapshot).where(ReferenceSnapshot.cache_key == cache_key)
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    ReferenceSnapshot(
                        cache_key=cache_key,
                        payload=payload,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            else:
                row.payload = payload
                row.updated_at = datetime.now(timezone.utc)
            session.commit()


reference_snapshot_repository = ReferenceSnapshotRepository()
