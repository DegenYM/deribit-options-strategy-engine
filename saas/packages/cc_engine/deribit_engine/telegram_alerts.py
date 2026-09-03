"""Optional Telegram alerts for live trading ops."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .env_layout import CONFIG_SHARED, find_repo_root

LOGGER = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_last_sent_monotonic: dict[str, float] = {}
_lock = threading.Lock()
_shared_env_loaded = False


def _truthy(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelegramAlertConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    cooldown_seconds: float
    request_timeout_seconds: float

    @classmethod
    def from_environ(cls) -> TelegramAlertConfig:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        enabled = _truthy(os.environ.get("TELEGRAM_ALERTS_ENABLED"), default=False)
        if enabled and (not token or not chat_id):
            LOGGER.warning("TELEGRAM_ALERTS_ENABLED=true but token/chat_id missing; alerts disabled")
            enabled = False
        try:
            cooldown = max(float(os.environ.get("TELEGRAM_ALERT_COOLDOWN_SECONDS", "300")), 0.0)
        except (TypeError, ValueError):
            cooldown = 300.0
        try:
            timeout = max(float(os.environ.get("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "10")), 1.0)
        except (TypeError, ValueError):
            timeout = 10.0
        return cls(
            enabled=enabled,
            bot_token=token,
            chat_id=chat_id,
            cooldown_seconds=cooldown,
            request_timeout_seconds=timeout,
        )


def bootstrap_telegram_env(repo_root: Path | str | None = None) -> None:
    """Load shared defaults env for Telegram keys without overriding existing os.environ."""
    global _shared_env_loaded
    if _shared_env_loaded:
        return
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        _shared_env_loaded = True
        return
    try:
        from dotenv import dotenv_values
    except ImportError:
        _shared_env_loaded = True
        return
    for name in (".env.defaults", "defaults.env"):
        path = root / CONFIG_SHARED / name
        if not path.is_file():
            continue
        for key, value in dotenv_values(path).items():
            if value is None or key in os.environ:
                continue
            os.environ[key] = str(value)
    _shared_env_loaded = True


def _should_send(event_key: str, cooldown_seconds: float) -> bool:
    if cooldown_seconds <= 0:
        return True
    now = time.monotonic()
    with _lock:
        last = _last_sent_monotonic.get(event_key)
        if last is not None and (now - last) < cooldown_seconds:
            return False
        _last_sent_monotonic[event_key] = now
    return True


def format_alert_message(
    *,
    title: str,
    body: str = "",
    level: str = "info",
    investor_id: str | None = None,
    slug: str | None = None,
    strategy: str | None = None,
    deribit_env: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    lines = [f"[{level.upper()}] {title.strip()}"]
    scope_bits: list[str] = []
    if investor_id:
        scope_bits.append(f"investor={investor_id}")
    if slug:
        scope_bits.append(f"slug={slug}")
    if strategy:
        scope_bits.append(f"strategy={strategy}")
    if deribit_env:
        scope_bits.append(f"env={deribit_env}")
    if scope_bits:
        lines.append(" | ".join(scope_bits))
    if body.strip():
        lines.append(body.strip())
    if extra:
        for key, value in extra.items():
            if value is None:
                continue
            lines.append(f"{key}: {value}")
    text = "\n".join(lines)
    return text[:3900]


def send_telegram_alert(
    message: str,
    *,
    event_key: str,
    level: str = "warning",
    config: TelegramAlertConfig | None = None,
) -> bool:
    """Send a Telegram message when alerts are enabled. Returns True if sent."""
    cfg = config or TelegramAlertConfig.from_environ()
    if not cfg.enabled:
        return False
    key = f"{level}:{event_key}"
    if not _should_send(key, cfg.cooldown_seconds):
        LOGGER.debug("telegram alert suppressed by cooldown event_key=%s", event_key)
        return False
    url = _TELEGRAM_API.format(token=cfg.bot_token)
    payload = {
        "chat_id": cfg.chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=cfg.request_timeout_seconds)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            LOGGER.warning("telegram send failed: %s", body.get("description", body))
            return False
        return True
    except requests.RequestException as exc:
        # Scrub the bot token from the request URL that requests embeds in its
        # exception messages (e.g. ".../bot<token>/sendMessage").
        from .structured_log import scrub_secrets

        LOGGER.warning("telegram send error: %s", scrub_secrets(str(exc)))
        return False


def send_test_alert(*, repo_root: Path | str | None = None) -> bool:
    bootstrap_telegram_env(repo_root)
    cfg = TelegramAlertConfig.from_environ()
    if not cfg.enabled:
        raise RuntimeError(
            "Telegram alerts are disabled. Set TELEGRAM_ALERTS_ENABLED=true, "
            "TELEGRAM_BOT_TOKEN, and TELEGRAM_CHAT_ID in config/shared/.env.defaults "
            "or your account env."
        )
    message = format_alert_message(
        title="Deribit bot test alert",
        body="If you see this, Telegram notifications are configured correctly.",
        level="info",
    )
    return send_telegram_alert(message, event_key="manual_test", level="info", config=cfg)
