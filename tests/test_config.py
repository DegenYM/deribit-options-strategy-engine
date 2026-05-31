from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.config import load_config
from deribit_engine.exceptions import ConfigurationError


def test_load_config_parses_strategy_fields(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=testnet",
                "MANAGED_CURRENCIES=btc,eth",
                "TOP_N=3",
                "REFERENCE_CAPITAL_USDC=1500",
                "TARGET_PORTFOLIO_APR=0.25",
                "OPTION_STRATEGY=bull_put_spread",
                "BULL_PUT_LONG_DELTA_MIN=0.03",
                "BULL_PUT_LONG_DELTA_MAX=0.06",
                "MIN_LIQUID_EXPIRIES_REQUIRED=1",
                "SHORT_PUT_DELTA_MAX=0.13",
                "STATE_FILE=.state/custom.json",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.env == "testnet"
    assert config.managed_currencies == ("BTC", "ETH")
    assert config.top_n == 3
    assert str(config.reference_capital_usdc) == "1500"
    assert str(config.target_portfolio_apr) == "0.25"
    assert config.target_annual_net_pnl_usdc == Decimal("375")
    assert config.min_liquid_expiries_required == 1
    assert str(config.short_put_delta_max) == "0.13"
    assert config.enable_perp_hedge is False
    assert config.state_file.name == "custom.json"
    assert config.option_markets_profile == "all"
    assert config.option_strategy == "bull_put_spread"
    assert config.bull_put_long_delta_min == Decimal("0.03")
    assert config.bull_put_long_delta_max == Decimal("0.06")
    assert config.halt_open_max_loss_pct == Decimal("0.45")
    assert config.regime_entry_option_sides() == ("put",)


def test_regime_entry_option_sides_by_strategy(tmp_path: Path):
    covered = tmp_path / ".env.covered"
    covered.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=testnet",
                "OPTION_STRATEGY=covered_call",
                "SHORT_OPTION_SIDE=call",
            ]
        )
    )
    naked_call = tmp_path / ".env.naked_call"
    naked_call.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=testnet",
                "OPTION_STRATEGY=naked_short",
                "SHORT_OPTION_SIDE=call",
            ]
        )
    )
    naked_both = tmp_path / ".env.naked_both"
    naked_both.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=testnet",
                "OPTION_STRATEGY=naked_short",
                "SHORT_OPTION_SIDE=both",
            ]
        )
    )

    assert load_config(covered, require_private=False).regime_entry_option_sides() == ("call",)
    assert load_config(naked_call, require_private=False).regime_entry_option_sides() == ("call",)
    assert load_config(naked_both, require_private=False).regime_entry_option_sides() == ("put", "call")


def test_load_config_parses_currency_specific_min_open_interest(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "INVERSE_MIN_OPEN_INTEREST=20",
                "LINEAR_MIN_OPEN_INTEREST=8",
                "BTC_MIN_OPEN_INTEREST=18",
                "BTC_LINEAR_MIN_OPEN_INTEREST=9",
                "ETH_INVERSE_MIN_OPEN_INTEREST=14",
                "ETH_LINEAR_MIN_OPEN_INTEREST=5",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.liquidity_gates("reversed", "BTC")[0] == Decimal("18")
    assert config.liquidity_gates("linear", "BTC")[0] == Decimal("9")
    assert config.liquidity_gates("reversed", "ETH")[0] == Decimal("14")
    assert config.liquidity_gates("linear", "ETH")[0] == Decimal("5")
    assert config.liquidity_gates("reversed", "SOL")[0] == Decimal("20")
    assert config.liquidity_gates("linear", "SOL")[0] == Decimal("8")


def test_short_option_side_both_overrides_legacy_flags(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SHORT_OPTION_SIDE=both",
                "ENABLE_SHORT_PUT=false",
                "ENABLE_SHORT_CALL=false",
                "SHORT_CALL_FALLBACK_ONLY=true",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.enable_short_put is True
    assert config.enable_short_call is True
    assert config.short_call_fallback_only is False


def test_short_option_side_call_only(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("SHORT_OPTION_SIDE=call\n")

    config = load_config(env_file, require_private=False)

    assert config.enable_short_put is False
    assert config.enable_short_call is True
    assert config.short_call_fallback_only is True


def test_strategy_profile_overlay_overrides_base_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=bull_put_spread",
                "SHORT_PUT_DELTA_MAX=0.11",
                "BTC_PUT_DELTA_MAX=0.12",
                "MIN_NET_APR=0.13",
                "PER_LEG_IM_CAP_PUT=0.12",
            ]
        )
    )
    (tmp_path / ".env.bull_put_spread").write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=bull_put_spread",
                "SHORT_PUT_DELTA_MAX=0.17",
                "BTC_PUT_DELTA_MAX=0.18",
                "MIN_NET_APR=0.15",
                "PER_LEG_IM_CAP_PUT=0.18",
                "BULL_PUT_LONG_DELTA_MIN=0.025",
                "BULL_PUT_LONG_DELTA_MAX=0.07",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.option_strategy == "bull_put_spread"
    assert config.short_put_delta_max == Decimal("0.17")
    assert config.btc_put_delta_max == Decimal("0.18")
    assert config.min_net_apr == Decimal("0.15")
    assert config.per_leg_im_cap_put == Decimal("0.18")
    assert config.bull_put_long_delta_min == Decimal("0.025")
    assert config.bull_put_long_delta_max == Decimal("0.07")


def test_strategy_override_loads_requested_profile(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=naked_short",
                "SHORT_OPTION_SIDE=put",
                "MIN_NET_APR=0.11",
            ]
        )
    )
    (tmp_path / ".env.covered_call").write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=covered_call",
                "OPTION_MARKETS_PROFILE=inverse_native",
                "SHORT_OPTION_SIDE=call",
                "MIN_NET_APR=0.20",
            ]
        )
    )

    config = load_config(env_file, require_private=False, strategy_override="covered-call")

    assert config.option_strategy == "covered_call"
    assert config.option_markets_profile == "inverse_native"
    assert config.enable_short_put is False
    assert config.enable_short_call is True
    assert config.min_net_apr == Decimal("0.20")


def test_strategy_profile_overlay_keeps_single_file_behavior_when_absent(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=bull_put_spread",
                "SHORT_PUT_DELTA_MAX=0.13",
                "BULL_PUT_LONG_DELTA_MIN=0.03",
                "BULL_PUT_LONG_DELTA_MAX=0.06",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.option_strategy == "bull_put_spread"
    assert config.short_put_delta_max == Decimal("0.13")
    assert config.bull_put_long_delta_min == Decimal("0.03")
    assert config.bull_put_long_delta_max == Decimal("0.06")


def test_covered_call_spot_exit_switches_parse(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=covered_call",
                "COVERED_CALL_SPOT_EXIT_ENABLED=true",
                "COVERED_CALL_ROBUST_EXIT_ENABLED=true",
                "COVERED_CALL_ROBUST_EXIT_DTE=0.25",
                "COVERED_CALL_ITM_BUFFER_PCT=0.01",
                "COVERED_CALL_SPOT_ORDER_TYPE=market",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.covered_call_spot_exit_enabled is True
    assert config.covered_call_robust_exit_enabled is True
    assert config.covered_call_robust_exit_dte == Decimal("0.25")
    assert config.covered_call_itm_buffer_pct == Decimal("0.01")
    assert config.covered_call_spot_order_type == "market"


def test_covered_call_spot_exit_appends_usdt_collateral(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=covered_call",
                "TRADED_COLLATERALS=BTC,ETH",
                "COVERED_CALL_SPOT_EXIT_ENABLED=true",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.covered_call_spot_exit_enabled is True
    assert "USDT" in config.traded_collaterals


def test_covered_call_profit_sweep_appends_usdt_collateral(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=covered_call",
                "TRADED_COLLATERALS=BTC,ETH",
                "COVERED_CALL_PROFIT_SWEEP_ENABLED=true",
            ]
        )
    )

    config = load_config(env_file, require_private=False)

    assert config.covered_call_profit_sweep_enabled is True
    assert "USDT" in config.traded_collaterals


def test_strategy_profile_mismatch_raises(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPTION_STRATEGY=bull_put_spread\n")
    (tmp_path / ".env.bull_put_spread").write_text("OPTION_STRATEGY=covered_call\n")

    with pytest.raises(ConfigurationError, match="does not match account OPTION_STRATEGY"):
        load_config(env_file, require_private=False)


def test_fee_account_skips_strategy_profile(tmp_path: Path, monkeypatch):
    repo = tmp_path
    (repo / "deribit_engine").mkdir()
    strategies = repo / "config/shared/strategies"
    strategies.mkdir(parents=True)
    (strategies / ".env.naked_short").write_text("MIN_NET_APR=0.99\n")
    investor = repo / "config/investors/alice"
    accounts = investor / "accounts"
    accounts.mkdir(parents=True)
    fee_env = accounts / ".env.fee"
    fee_env.write_text("ACCOUNT_ROLE=fee\nDERIBIT_ENV=testnet\n")
    monkeypatch.chdir(repo)

    config = load_config(fee_env, require_private=False)

    assert config.is_fee_collection_account is True
    assert config.min_net_apr == Decimal("0.12")


def test_assert_trading_account_rejects_fee_wallet(tmp_path: Path):
    env_file = tmp_path / ".env.fee"
    env_file.write_text("ACCOUNT_ROLE=fee\nDERIBIT_ENV=testnet\n")
    config = load_config(env_file, require_private=False)

    with pytest.raises(ConfigurationError, match="Fee collection account"):
        from deribit_engine.config import assert_trading_account

        assert_trading_account(config)
