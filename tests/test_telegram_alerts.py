from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deribit_demo.telegram_alerts import (
    TelegramAlertConfig,
    bootstrap_telegram_env,
    format_alert_message,
    send_telegram_alert,
    send_test_alert,
)


@pytest.fixture(autouse=True)
def _clear_telegram_cooldown():
    import deribit_demo.telegram_alerts as mod

    mod._last_sent_monotonic.clear()
    mod._shared_env_loaded = False
    yield
    mod._last_sent_monotonic.clear()
    mod._shared_env_loaded = False


def test_config_disabled_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALERTS_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = TelegramAlertConfig.from_environ()
    assert cfg.enabled is False


def test_send_alert_respects_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = TelegramAlertConfig(
        enabled=True,
        bot_token="token",
        chat_id="123",
        cooldown_seconds=60.0,
        request_timeout_seconds=5.0,
    )
    response = MagicMock()
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    with patch("deribit_demo.telegram_alerts.requests.post", return_value=response) as post:
        assert send_telegram_alert("one", event_key="evt", config=cfg) is True
        assert send_telegram_alert("two", event_key="evt", config=cfg) is False
        assert post.call_count == 1


def test_bootstrap_loads_shared_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "config" / "shared"
    shared.mkdir(parents=True)
    (shared / "strategies").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    (shared / "defaults.env").write_text(
        "TELEGRAM_ALERTS_ENABLED=true\nTELEGRAM_BOT_TOKEN=from_defaults\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bootstrap_telegram_env(tmp_path)
    assert os.environ.get("TELEGRAM_BOT_TOKEN") == "from_defaults"


def test_format_alert_message_includes_scope() -> None:
    text = format_alert_message(
        title="Hard derisk",
        body="books=['USDC']",
        level="warning",
        investor_id="youming",
        slug="naked",
        strategy="naked_short",
        deribit_env="mainnet",
    )
    assert "Hard derisk" in text
    assert "investor=youming" in text
    assert "slug=naked" in text


def test_send_test_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "config" / "shared"
    shared.mkdir(parents=True)
    (shared / "strategies").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    (shared / "defaults.env").write_text(
        "\n".join(
            [
                "TELEGRAM_ALERTS_ENABLED=true",
                "TELEGRAM_BOT_TOKEN=test-token",
                "TELEGRAM_CHAT_ID=999",
                "TELEGRAM_ALERT_COOLDOWN_SECONDS=0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    response = MagicMock()
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    with patch("deribit_demo.telegram_alerts.requests.post", return_value=response) as post:
        assert send_test_alert(repo_root=tmp_path) is True
        assert post.call_count == 1
