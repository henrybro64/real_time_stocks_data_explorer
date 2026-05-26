from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import logging
from time import perf_counter
from typing import Any
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


@dataclass
class TimedOperation:
    logger: logging.Logger
    event_name: str
    fields: dict[str, Any] = field(default_factory=dict)
    start_level: int | None = None
    success_level: int = logging.INFO
    failure_level: int = logging.ERROR
    _started_at: float = field(default_factory=perf_counter, init=False)
    _closed: bool = field(default=False, init=False)

    def bind(self, **fields: Any) -> None:
        for key, value in fields.items():
            if value is not None:
                self.fields[key] = value

    def __enter__(self) -> TimedOperation:
        if self.start_level is not None:
            self.logger.log(
                self.start_level,
                f"{self.event_name} started",
                extra={"event": f"{self.event_name}.started", **self.fields},
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: object,
    ) -> bool:
        if exc is not None:
            self.failure(exc)
            return False
        self.success()
        return False

    def success(self, **fields: Any) -> None:
        if self._closed:
            return
        self.bind(**fields)
        self._closed = True
        self.logger.log(
            self.success_level,
            f"{self.event_name} completed",
            extra={
                "event": f"{self.event_name}.completed",
                "duration_ms": elapsed_ms(self._started_at),
                **self.fields,
            },
        )

    def failure(self, exc: BaseException, **fields: Any) -> None:
        if self._closed:
            return
        self.bind(**fields)
        self._closed = True
        self.logger.log(
            self.failure_level,
            f"{self.event_name} failed",
            extra={
                "event": f"{self.event_name}.failed",
                "duration_ms": elapsed_ms(self._started_at),
                "error_type": type(exc).__name__,
                "error": str(exc),
                **self.fields,
            },
            exc_info=self.failure_level >= logging.ERROR,
        )
