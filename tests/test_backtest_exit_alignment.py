from __future__ import annotations

from decimal import Decimal

from conftest import make_config

from deribit_engine.exit_eval import (
    backtest_remaining_apr_gate,
    backtest_tp_target_premium,
    exit_eval_context_from_config,
)


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
