from pathlib import Path

from cc_engine.catalog import merged_catalog_env
from cc_engine.settings import CoveredCallSettings
from cc_engine.worker import settings_to_env
from deribit_engine.config import load_config_from_values


def test_catalog_locks_covered_call_only():
    values = merged_catalog_env("low")
    assert values["OPTION_STRATEGY"] == "covered_call"
    assert values["SHORT_OPTION_SIDE"] == "call"
    assert values["RISK_TIER"] == "low"
    assert "BTC_CALL_DELTA_MIN" in values


def test_high_tier_has_higher_apr_floor_than_low():
    low = merged_catalog_env("low")
    high = merged_catalog_env("high")
    assert float(high["MIN_NET_APR"]) > float(low["MIN_NET_APR"])


def test_settings_to_env_builds_bot_config(tmp_path: Path):
    settings = CoveredCallSettings(
        tenant_id="11111111-2222-3333-4444-555555555555",
        risk_tier="medium",
        coins=("BTC",),
        profit_sweep=True,
        live=False,
        state_dir=tmp_path,
        client_id="cid",
        client_secret="csecret",
    )
    values = settings_to_env(settings)
    config = load_config_from_values(values, require_private=True)
    assert config.option_strategy == "covered_call"
    assert config.risk_tier == "medium"
    assert "BTC" in config.traded_collaterals
    assert "USDT" in config.traded_collaterals
    assert config.covered_call_profit_sweep_enabled is True
    assert config.client_id == "cid"
    assert config.state_file == tmp_path / "covered_call.json"
