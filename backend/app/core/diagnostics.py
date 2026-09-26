from __future__ import annotations

from contextvars import ContextVar, Token
from time import perf_counter
from uuid import uuid4


_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def new_request_id() -> str:
    return uuid4().hex


def set_request_id(request_id: str | None) -> Token[str | None]:
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_user_id(user_id: str | None) -> Token[str | None]:
    return _user_id_var.set(user_id)


def reset_user_id(token: Token[str | None]) -> None:
    _user_id_var.reset(token)


def get_user_id() -> str | None:
    return _user_id_var.get()


def current_log_context() -> dict[str, str]:
    context: dict[str, str] = {}
    request_id = get_request_id()
    user_id = get_user_id()
    if request_id:
        context["request_id"] = request_id
    if user_id:
        context["user_id"] = user_id
    return context


def elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)
