from __future__ import annotations

from decimal import Decimal

from conftest import future_expiry, make_config

from deribit_engine.exit_eval import (
    backtest_remaining_apr_gate,
    backtest_time_exit_gate,
    backtest_tp_target_premium,
    exit_eval_context_from_config,
    time_exit_triggered,
)
from deribit_engine.models import TradeGroup


def test_backtest_tp_and_early_exit_helpers(tmp_path):
    config = make_config(
        tmp_path,
        enable_dynamic_tp=True,
        tp_capture_pct_dte_long=Decimal("0.35"),
        enable_early_exit=True,
        early_exit_remaining_apr=Decimal("0.10"),
        early_exit_min_profit_capture=Decimal("0.20"),
    )
    ctx = exit_eval_context_from_config(config)
    assert backtest_tp_target_premium(Decimal("1000"), Decimal("20"), ctx) == Decimal("650")
    assert backtest_remaining_apr_gate(
        entry_premium=Decimal("500"),
        current_premium=Decimal("100"),
        close_fee_per_contract=Decimal("5"),
        quantity=Decimal("1"),
        capital_base=Decimal("63000"),
        dte_days=Decimal("8"),
        ctx=ctx,
    )


def test_time_exit_requires_profit_when_min_capture_configured(tmp_path):
    config = make_config(
        tmp_path,
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0.01"),
    )
    ctx = exit_eval_context_from_config(config)
    group = TradeGroup(
        group_id="g1",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=future_expiry(3),
        short_instrument_name="BTC-TEST-C",
        short_strike=Decimal("70000"),
        entry_credit=Decimal("100"),
        original_entry_credit=Decimal("100"),
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        profit_capture=Decimal("0.25"),
    )
    assert time_exit_triggered(group, close_debit_usdc=Decimal("70"), ctx=ctx) is True
    assert time_exit_triggered(group, close_debit_usdc=Decimal("105"), ctx=ctx) is False

    assert backtest_time_exit_gate(
        entry_credit=Decimal("100"),
        close_debit=Decimal("70"),
        profit_capture_mark=Decimal("0.25"),
        dte_days=Decimal("3"),
        ctx=ctx,
    )
    assert not backtest_time_exit_gate(
        entry_credit=Decimal("100"),
        close_debit=Decimal("105"),
        profit_capture_mark=Decimal("-0.05"),
        dte_days=Decimal("3"),
        ctx=ctx,
    )


def test_time_exit_legacy_behavior_when_min_capture_zero(tmp_path):
    config = make_config(tmp_path, time_exit_dte=4, time_exit_min_profit_capture=Decimal("0"))
    ctx = exit_eval_context_from_config(config)
    group = TradeGroup(
        group_id="g1",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=future_expiry(3),
        short_instrument_name="BTC-TEST-C",
        short_strike=Decimal("70000"),
        entry_credit=Decimal("100"),
        original_entry_credit=Decimal("100"),
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        profit_capture=Decimal("-0.10"),
    )
    assert time_exit_triggered(group, close_debit_usdc=Decimal("120"), ctx=ctx) is True
