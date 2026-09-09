"""Small, dependency-free parsers for environment-style string values.

Kept separate from :mod:`deribit_engine.config` so lightweight modules
(telegram alerts, state store, scripts) can share one boolean grammar without
importing the full ``BotConfig`` machinery.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f"})


def parse_env_bool(
    value: str | None,
    *,
    default: bool | None = None,
    strict: bool = True,
    name: str | None = None,
) -> bool | None:
    """Parse a boolean-ish env string.

    - ``None`` / empty / whitespace-only → ``default``.
    - ``1/true/yes/on/y/t`` → ``True``; ``0/false/no/off/n/f`` → ``False``
      (case-insensitive, surrounding whitespace ignored).
    - Anything else: ``strict=True`` raises :class:`ValueError`; ``strict=False``
      logs a WARNING and returns ``default``.

    ``name`` is only used to make the error / warning message actionable.
    """
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized == "":
        return default
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    label = f"{name}=" if name else ""
    if strict:
        raise ValueError(f"Invalid boolean config value: {label}{value}")
    LOGGER.warning(
        "invalid boolean value %s%r; using default=%r",
        label,
        value,
        default,
    )
    return default
