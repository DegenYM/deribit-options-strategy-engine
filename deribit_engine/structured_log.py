"""JSON structured logging for live bot runs."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# No constructor default: a shared mutable default would leak across contexts.
# Every read passes its own ``{}`` fallback instead.
_live_context: ContextVar[dict[str, str]] = ContextVar("live_log_context")

REDACTED = "***redacted***"

# Substrings (case-insensitive) that mark a log field as secret-bearing. Matching
# is done on the field name so any extra={"client_secret": ...} style payload, or
# common credential field names, get masked before reaching the JSON sink.
_REDACT_KEY_SUBSTRINGS = (
    "secret",
    "token",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
)

# Patterns that scrub secrets embedded inside free-form strings (e.g. exception
# text that includes a Telegram bot-token URL or a Deribit auth URL with creds).
_VALUE_SCRUBBERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot" + REDACTED),
    (re.compile(r"(client_secret=)[^&\s\"']+"), r"\1" + REDACTED),
    (re.compile(r"(access_token=)[^&\s\"']+"), r"\1" + REDACTED),
    (re.compile(r"(refresh_token=)[^&\s\"']+"), r"\1" + REDACTED),
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _REDACT_KEY_SUBSTRINGS)


def scrub_secrets(text: str) -> str:
    """Mask known secret patterns embedded inside a free-form string."""
    for pattern, replacement in _VALUE_SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


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
            "message": scrub_secrets(record.getMessage()),
        }
        for key in ("investor_id", "slug", "strategy", "deribit_env", "cycle", "regime"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in self._SKIP or key in payload:
                continue
            if _is_secret_key(key):
                payload[key] = REDACTED
            elif isinstance(value, str):
                payload[key] = scrub_secrets(value)
            elif isinstance(value, int | float | bool) or value is None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = scrub_secrets(self.formatException(record.exc_info))
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
