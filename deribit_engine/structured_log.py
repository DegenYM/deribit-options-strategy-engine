"""JSON structured logging for live bot runs."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_live_context: ContextVar[dict[str, str]] = ContextVar("live_log_context", default={})


class LiveContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _live_context.get({}).items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class LiveJsonFormatter(logging.Formatter):
    _SKIP = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("investor_id", "slug", "strategy", "deribit_env", "cycle", "regime"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._SKIP or key in payload:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def set_live_log_context(**fields: str) -> None:
    current = dict(_live_context.get({}))
    current.update({key: value for key, value in fields.items() if value is not None})
    _live_context.set(current)


def configure_live_structured_logging(scope: dict[str, str], *, verbose: bool = False) -> None:
    set_live_log_context(**scope)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(LiveJsonFormatter())
    handler.addFilter(LiveContextFilter())
    root.addHandler(handler)
