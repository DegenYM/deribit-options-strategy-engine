"""The shared covered_call profile defines the ITM exit and the wheel, once, for every tier."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.config import ConfigurationError, load_config

REPO = Path(__file__).resolve().parent.parent
TIERS = ("low", "medium", "high")
WHEEL_KEYS = {
    "COVERED_CALL_SPOT_EXIT_ENABLED",
    "COVERED_CALL_ROBUST_EXIT_ENABLED",
    "COVERED_CALL_ITM_BUFFER_PCT",
    "COVERED_CALL_SPOT_ORDER_TYPE",
    "COVERED_CALL_SPOT_MAX_SLIPPAGE_PCT",
    "COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED",
    "CSP_PREMIUM_LADDER",
    "COVERED_CALL_CSP_PREMIUM_TARGET",
}


def _covered_call_config(tier: str | None = None):
    # The account env has to live inside the repo, or env_layer_paths cannot find the
    # shared profile and tier and the engine silently falls back to code defaults.
    with tempfile.TemporaryDirectory(dir=REPO, prefix=".test-profile-") as tmp:
        account = Path(tmp) / ".env.covered_call"
        lines = ["DERIBIT_ENV=mainnet", "OPTION_STRATEGY=covered_call", f"STATE_FILE={tmp}/state.json"]
        if tier is not None:
            lines.append(f"RISK_TIER={tier}")
        account.write_text("\n".join(lines) + "\n")
        return load_config(account, require_private=False)


def _sub_account_config(*lines: str):
    # A sub-account env is applied after the shared profile only in the investor layout,
    # config/investors/<id>/accounts/ (git-ignored), so the throwaway investor lives there.
    with tempfile.TemporaryDirectory(dir=REPO / "config" / "investors", prefix=".test-profile-") as tmp:
        accounts = Path(tmp) / "accounts"
        accounts.mkdir()
        account = accounts / ".env.covered_call"
        base = ["DERIBIT_ENV=mainnet", "OPTION_STRATEGY=covered_call", f"STATE_FILE={tmp}/state.json"]
        account.write_text("\n".join([*base, *lines]) + "\n")
        return load_config(account, require_private=False)


def test_self_assign_is_fenced():
    config = _covered_call_config()
    assert config.covered_call_csp_self_assign_max_dte == Decimal("1")
    assert config.covered_call_csp_self_assign_max_spread_ratio == Decimal("0.10")
    assert config.covered_call_csp_self_assign_confirm_cycles == 4


def test_the_profile_carries_the_trend_and_apr_changes():
    config = _covered_call_config()
    assert config.enable_trend_adaptive_selection is True
    assert config.min_net_apr == Decimal("0.045")


def test_the_wheel_is_on_with_self_assign_fenced():
    config = _covered_call_config()
    assert config.covered_call_itm_to_cash_secured_enabled is True
    assert config.covered_call_csp_self_assign_enabled is True
    assert config.covered_call_spot_exit_enabled is True
    # The put is collateralised in USDC, so the called-away coin has to be sold into it.
    assert "USDC" in config.traded_collaterals


@pytest.mark.parametrize("tier", TIERS)
def test_every_tier_runs_the_same_wheel(tier):
    config = _covered_call_config(tier)
    assert config.risk_tier == tier
    # 1. ITM -> sell the cover
    assert config.covered_call_spot_exit_enabled is True
    assert config.covered_call_robust_exit_enabled is False
    assert config.covered_call_itm_buffer_pct == Decimal("0")
    assert config.covered_call_spot_order_type == "market"
    assert config.covered_call_spot_max_slippage_pct == Decimal("0.001")
    # 2. CSP buy-back
    assert config.covered_call_itm_to_cash_secured_enabled is True
    # 3. premium ladder
    assert config.covered_call_csp_premium_ladder is True
    # 4. put premium stays in USDC
    assert config.covered_call_csp_premium_target == "usdc"


def test_the_tier_files_do_not_redefine_the_itm_exit_or_the_wheel():
    """A tier file is applied after the shared profile, so any of these keys there would silently win."""
    for tier in TIERS:
        path = REPO / "config" / "shared" / "strategies" / "tiers" / "covered_call" / f".env.{tier}"
        assigned = {
            line.split("=", 1)[0].strip()
            for line in path.read_text().splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        assert not assigned & WHEEL_KEYS, (tier, sorted(assigned & WHEEL_KEYS))


def test_a_sub_account_can_turn_the_ladder_off():
    config = _sub_account_config("CSP_PREMIUM_LADDER=false")
    assert config.covered_call_csp_premium_ladder is False
    assert config.covered_call_itm_to_cash_secured_enabled is True


def test_a_sub_account_that_wants_the_premium_swap_has_to_turn_the_ladder_off_too():
    with pytest.raises(ConfigurationError, match="CSP_PREMIUM_LADDER"):
        _sub_account_config("COVERED_CALL_CSP_PREMIUM_TARGET=spot")
    config = _sub_account_config("COVERED_CALL_CSP_PREMIUM_TARGET=spot", "CSP_PREMIUM_LADDER=false")
    assert config.covered_call_csp_premium_target == "spot"
