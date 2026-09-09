"""BotConfig secret masking, shared bool grammar, and state-persistence knobs (FIX 2 / FIX 6)."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest
from conftest import make_config

from deribit_engine import telegram_alerts
from deribit_engine.config import (
    SECRET_MASK,
    BotConfig,
    is_secret_field_name,
    load_config,
    load_env_values,
    mask_secret,
)
from deribit_engine.env_parse import parse_env_bool
from deribit_engine.exceptions import ConfigurationError


def _env(tmp_path: Path, *lines: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(("DERIBIT_ENV=mainnet", *lines)), encoding="utf-8")
    return env_file


# ---- secrets -------------------------------------------------------------------------


def test_repr_and_str_mask_client_secret(tmp_path: Path) -> None:
    config = make_config(tmp_path, client_id="my-client-id", client_secret="hunter2-very-secret-9Z")
    for rendered in (repr(config), str(config), f"{config}"):
        assert "hunter2-very-secret-9Z" not in rendered
        assert "client_secret='***9Z'" in rendered
        assert "my-client-id" in rendered  # client_id is not a secret
    assert rendered.startswith("BotConfig(")


def test_to_safe_dict_masks_only_secret_fields(tmp_path: Path) -> None:
    config = make_config(tmp_path, client_secret="abcdef")
    safe = config.to_safe_dict()
    assert safe["client_secret"] == "***ef"
    assert safe["client_id"] == config.client_id
    assert set(safe) == {f.name for f in dataclasses.fields(BotConfig)}
    # asdict would leak; make sure the raw value is really still there for the client.
    assert dataclasses.asdict(config)["client_secret"] == "abcdef"
    assert config.client_secret == "abcdef"


def test_mask_secret_edge_cases() -> None:
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("abcd") == SECRET_MASK
    assert mask_secret("abcde") == "***de"
    assert is_secret_field_name("client_secret")
    assert is_secret_field_name("TELEGRAM_BOT_TOKEN")
    assert is_secret_field_name("dashboard_api_token")
    assert not is_secret_field_name("client_id")
    assert not is_secret_field_name("state_file")


# ---- shared bool grammar --------------------------------------------------------------


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on", "y", "t"])
def test_parse_env_bool_truthy(raw: str) -> None:
    assert parse_env_bool(raw) is True


@pytest.mark.parametrize("raw", ["0", "false", "No", "off", "n", "f"])
def test_parse_env_bool_falsy(raw: str) -> None:
    assert parse_env_bool(raw) is False


def test_parse_env_bool_default_and_strictness(caplog: pytest.LogCaptureFixture) -> None:
    assert parse_env_bool(None) is None
    assert parse_env_bool("", default=True) is True
    assert parse_env_bool("   ", default=False) is False
    with pytest.raises(ValueError, match="Invalid boolean config value: FOO=maybe"):
        parse_env_bool("maybe", name="FOO")
    with caplog.at_level(logging.WARNING, logger="deribit_engine.env_parse"):
        assert parse_env_bool("maybe", default=True, strict=False, name="FOO") is True
    assert "FOO='maybe'" in caplog.text


def test_config_bool_invalid_still_raises_configuration_error(tmp_path: Path) -> None:
    env_file = _env(tmp_path, "ENABLE_PERP_HEDGE=sometimes")
    with pytest.raises(ConfigurationError, match="Invalid boolean config value: sometimes"):
        load_config(env_file, require_private=False)


def test_config_bool_accepts_legacy_and_short_forms(tmp_path: Path) -> None:
    env_file = _env(tmp_path, "ENABLE_PERP_HEDGE=Yes", "ENABLE_EARLY_EXIT=n")
    config = load_config(env_file, require_private=False)
    assert config.enable_perp_hedge is True
    assert config.enable_early_exit is False


def test_telegram_truthy_warns_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TELEGRAM_ALERTS_ENABLED", "definitely")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    with caplog.at_level(logging.WARNING):
        cfg = telegram_alerts.TelegramAlertConfig.from_environ()
    assert cfg.enabled is False
    assert "TELEGRAM_ALERTS_ENABLED='definitely'" in caplog.text
    monkeypatch.setenv("TELEGRAM_ALERTS_ENABLED", "yes")
    assert telegram_alerts.TelegramAlertConfig.from_environ().enabled is True
    assert telegram_alerts._truthy(None, default=True) is True
    assert telegram_alerts._truthy("garbage") is False


# ---- state persistence knobs --------------------------------------------------------------


def test_state_knob_defaults(tmp_path: Path) -> None:
    config = load_config(_env(tmp_path), require_private=False)
    assert config.state_json_pretty is False
    assert config.state_closed_archive_enabled is False
    assert config.state_closed_archive_keep_days == 90
    assert config.state_closed_archive_keep_min == 20


def test_state_knobs_parse(tmp_path: Path) -> None:
    env_file = _env(
        tmp_path,
        "STATE_JSON_PRETTY=true",
        "STATE_CLOSED_ARCHIVE_ENABLED=true",
        "STATE_CLOSED_ARCHIVE_KEEP_DAYS=30",
        "STATE_CLOSED_ARCHIVE_KEEP_MIN=-5",
    )
    config = load_config(env_file, require_private=False)
    assert config.state_json_pretty is True
    assert config.state_closed_archive_enabled is True
    assert config.state_closed_archive_keep_days == 30
    assert config.state_closed_archive_keep_min == 0  # clamped


def _engine_state_store(tmp_path: Path, **overrides):
    from conftest import FakeClient

    from deribit_engine.engine import DeribitOptionTrialBot

    tmp_path.mkdir(parents=True, exist_ok=True)
    config = make_config(tmp_path, **overrides)
    return config, DeribitOptionTrialBot(config, FakeClient()).state_store


def test_engine_state_store_honours_state_json_pretty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``BotConfig.state_json_pretty`` must reach the engine's store, not the process env."""
    from deribit_engine.models import StrategyState

    monkeypatch.delenv("STATE_JSON_PRETTY", raising=False)
    config, store = _engine_state_store(tmp_path / "pretty", state_json_pretty=True)
    assert store.path == config.state_file
    assert store.pretty is True
    store.save(StrategyState())
    raw = store.path.read_text(encoding="utf-8")
    assert raw.count("\n") > 1
    assert '\n  "' in raw  # indent=2

    config, store = _engine_state_store(tmp_path / "compact")
    assert config.state_json_pretty is False
    assert store.pretty is False
    store.save(StrategyState())
    assert "\n" not in store.path.read_text(encoding="utf-8").strip()

    # Config wins over a contradicting process environment.
    monkeypatch.setenv("STATE_JSON_PRETTY", "true")
    _, store = _engine_state_store(tmp_path / "env", state_json_pretty=False)
    assert store.pretty is False


# ---- dashboard / admin console knobs (mirrors of process env) -----------------------------


def test_dashboard_admin_knob_defaults(tmp_path: Path) -> None:
    config = load_config(_env(tmp_path), require_private=False)
    assert config.dashboard_api_token == ""
    assert config.dashboard_api_token_embed is False
    assert config.dashboard_cors_origins == ()
    assert config.admin_console_token == ""
    assert config.admin_console_token_embed is False


def test_dashboard_admin_knobs_parse_and_repr_masks_tokens(tmp_path: Path) -> None:
    env_file = _env(
        tmp_path,
        "DASHBOARD_API_TOKEN=dash-super-secret-token-Q7",
        "DASHBOARD_API_TOKEN_EMBED=true",
        "DASHBOARD_CORS_ORIGINS=https://portal.example, https://ops.example ,",
        "ADMIN_CONSOLE_TOKEN=admin-super-secret-token-K2",
        "ADMIN_CONSOLE_TOKEN_EMBED=1",
    )
    config = load_config(env_file, require_private=False)
    assert config.dashboard_api_token == "dash-super-secret-token-Q7"
    assert config.dashboard_api_token_embed is True
    assert config.dashboard_cors_origins == ("https://portal.example", "https://ops.example")
    assert config.admin_console_token == "admin-super-secret-token-K2"
    assert config.admin_console_token_embed is True

    assert is_secret_field_name("dashboard_api_token")
    assert is_secret_field_name("admin_console_token")
    assert not is_secret_field_name("dashboard_api_token_embed")
    assert not is_secret_field_name("admin_console_token_embed")

    safe = config.to_safe_dict()
    assert safe["dashboard_api_token"] == "***Q7"
    assert safe["admin_console_token"] == "***K2"
    assert safe["dashboard_api_token_embed"] is True
    assert safe["admin_console_token_embed"] is True
    for rendered in (repr(config), str(config)):
        assert "dash-super-secret-token-Q7" not in rendered
        assert "admin-super-secret-token-K2" not in rendered
        assert "dashboard_api_token='***Q7'" in rendered
        assert "admin_console_token='***K2'" in rendered
        assert "dashboard_api_token_embed=True" in rendered


def test_load_env_values_exposes_merged_raw_values(tmp_path: Path) -> None:
    env_file = _env(tmp_path, "STATE_FILE=.state/x.json")
    values = load_env_values(env_file)
    assert values["STATE_FILE"] == ".state/x.json"
    assert values["OPTION_STRATEGY"] == "naked_short"
