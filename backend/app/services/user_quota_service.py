"""Per-user resource caps and server-key AI budget (#273)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.rate_limit_service import (
    RateLimitExceededError,
    peek_rate_limit,
    require_rate_limit,
)


class UserQuotaExceededError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def enforce_watchlist_cap(user_id: str, *, current_count: int) -> None:
    limit = int(settings.user_watchlist_max_items)
    if current_count >= limit:
        raise UserQuotaExceededError(
            f"Watchlist limit reached ({limit} items). Remove a symbol before adding another."
        )


def enforce_portfolio_position_cap(user_id: str, *, current_count: int) -> None:
    del user_id
    limit = int(settings.user_portfolio_max_positions)
    if current_count >= limit:
        raise UserQuotaExceededError(
            f"Portfolio position limit reached ({limit}). Remove a position before adding another."
        )


def enforce_portfolio_asset_cap(user_id: str, *, current_count: int) -> None:
    del user_id
    limit = int(settings.user_portfolio_max_assets)
    if current_count >= limit:
        raise UserQuotaExceededError(
            f"Other-asset limit reached ({limit}). Remove an asset before adding another."
        )


def enforce_alert_rule_cap(user_id: str, *, current_count: int) -> None:
    del user_id
    limit = int(settings.user_alert_max_rules)
    if current_count >= limit:
        raise UserQuotaExceededError(
            f"Alert rule limit reached ({limit}). Delete a rule before creating another."
        )


_process_locks_guard = Lock()
_process_locks: dict[tuple[str, str], Lock] = {}


def quota_process_lock(kind: str, user_id: str) -> Lock:
    """Serialize cap checks for one user inside this process."""
    key = (kind, user_id)
    with _process_locks_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = Lock()
            _process_locks[key] = lock
        return lock


@contextmanager
def user_quota_write(kind: str, user_id: str) -> Iterator[None]:
    with quota_process_lock(kind, user_id):
        yield


def lock_owner_row(session: Session, user_id: str) -> None:
    """Lock the user row on Postgres so two replicas cannot both pass a cap."""
    from app.db.models import User

    stmt = select(User.id).where(User.id == user_id)
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    session.execute(stmt)


def _server_ai_budget_detail(limit: int) -> str:
    return (
        f"Daily server AI key budget reached ({limit}). "
        "Add your own AI provider key or try again tomorrow."
    )


def _server_ai_budget_key(user_id: str) -> tuple[str, int]:
    limit = int(settings.user_ai_server_key_daily_limit)
    day = datetime.now(UTC).strftime("%Y%m%d")
    return f"ai:server:{user_id}:{day}", limit


def server_ai_provider_configured() -> bool:
    """True when a server-side AI provider can actually be called."""
    from app.services.ai_providers.base import AIProviderConfigurationError
    from app.services.ai_providers.factory import create_ai_provider

    try:
        return create_ai_provider() is not None
    except AIProviderConfigurationError:
        return False


def ensure_server_ai_budget_available(user_id: str) -> None:
    """Reject before the provider call when the daily server-key budget is spent.

    Does not consume a slot. ``consume_server_ai_budget`` counts a call that
    actually returned an analysis.
    """
    key, limit = _server_ai_budget_key(user_id)
    if limit <= 0:
        raise RateLimitExceededError(
            "Server AI key budget is disabled. Configure your own AI provider key.",
            retry_after_seconds=86_400,
        )
    decision = peek_rate_limit(key, limit=limit, window_seconds=86_400)
    if decision.allowed:
        return
    raise RateLimitExceededError(
        _server_ai_budget_detail(limit),
        retry_after_seconds=max(1, decision.retry_after_seconds),
    )


def consume_server_ai_budget(user_id: str) -> None:
    """Reserve one server-key slot before the provider call.

    Raises ``RateLimitExceededError`` when the budget is exhausted. Calls that
    use the user's own provider key must not call this. Refund with
    ``refund_server_ai_budget`` when the provider returns no analysis.
    """
    key, limit = _server_ai_budget_key(user_id)
    if limit <= 0:
        raise RateLimitExceededError(
            "Server AI key budget is disabled. Configure your own AI provider key.",
            retry_after_seconds=86_400,
        )
    require_rate_limit(
        key,
        limit=limit,
        window_seconds=86_400,
        detail=_server_ai_budget_detail(limit),
    )


def refund_server_ai_budget(user_id: str) -> None:
    """Return a reserved server-key slot when the provider produced no analysis."""
    from app.services.rate_limit_service import refund_rate_limit

    key, limit = _server_ai_budget_key(user_id)
    if limit <= 0:
        return
    refund_rate_limit(key)
