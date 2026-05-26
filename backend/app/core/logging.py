from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.diagnostics import current_log_context


_CONFIGURED = False
_RESERVED_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class ContextFieldsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_log_context().items():
            if getattr(record, key, None) is None:
                setattr(record, key, value)
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_log_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(
            record.created,
            tz=timezone.utc,
        ).isoformat(timespec="milliseconds")
        fields = _log_fields(record)
        event = fields.pop("event", None)
        parts = [timestamp, record.levelname, record.name]
        if event:
            parts.append(str(event))
        message = record.getMessage()
        if message:
            parts.append(message)
        parts.extend(
            f"{key}={_console_field_value(value)}"
            for key, value in fields.items()
        )
        if record.exc_info:
            parts.append(f"exception={self.formatException(record.exc_info)!r}")
        return " ".join(parts)


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    if settings.log_format.strip().lower() == "json":
        formatter: logging.Formatter = JsonLogFormatter()
    else:
        formatter = ConsoleLogFormatter()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    context_filter = ContextFieldsFilter()
    handler.addFilter(context_filter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.filters.clear()
    root_logger.setLevel(_parse_log_level(settings.log_level))
    root_logger.addHandler(handler)
    root_logger.addFilter(context_filter)

    logging.captureWarnings(True)

    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False

    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    _CONFIGURED = True


def _parse_log_level(level_name: str) -> int:
    return getattr(logging, level_name.strip().upper(), logging.INFO)


def _log_fields(record: logging.LogRecord) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _RESERVED_LOG_RECORD_FIELDS or key.startswith("_"):
            continue
        if value is None:
            continue
        fields[key] = value
    return fields


def _console_field_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return repr(value) if isinstance(value, str) and " " in value else str(value)
