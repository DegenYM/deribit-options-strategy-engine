"""Time exit must not fall back to stale mark-implied profit_capture when book is unusable."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from conftest import FakeClient, future_expiry, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.exit_eval import exit_eval_context_from_config, time_exit_triggered
from deribit_engine.models import OrderBookSnapshot, TradeGroup
from tests.test_engine import _covered_call_group


def _time_exit_group(*, dte_days: int, profit_capture: Decimal) -> TradeGroup:
    group = TradeGroup(
        group_id="g1",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=future_expiry(dte_days),
        short_instrument_name="BTC-TEST-C",
        short_strike=Decimal("70000"),
        entry_credit=Decimal("100"),
        original_entry_credit=Decimal("100"),
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        profit_capture=profit_capture,
    )
    return group


def test_time_exit_skips_stale_profit_capture_when_close_debit_unavailable(tmp_path):
    config = make_config(
        tmp_path,
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0.01"),
    )
    ctx = exit_eval_context_from_config(config)
    group = _time_exit_group(dte_days=3, profit_capture=Decimal("0.9"))

    assert time_exit_triggered(group, close_debit_usdc=None, ctx=ctx) is False


def test_time_exit_triggers_with_executable_debit_when_min_capture_configured(tmp_path):
    config = make_config(
        tmp_path,
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0.01"),
    )
    ctx = exit_eval_context_from_config(config)
    group = _time_exit_group(dte_days=3, profit_capture=Decimal("0.9"))

    assert time_exit_triggered(group, close_debit_usdc=Decimal("70"), ctx=ctx) is True
    assert time_exit_triggered(group, close_debit_usdc=Decimal("105"), ctx=ctx) is False


def test_time_exit_unconditional_when_min_capture_zero_and_no_book(tmp_path):
    config = make_config(
        tmp_path,
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0"),
    )
    ctx = exit_eval_context_from_config(config)
    group = _time_exit_group(dte_days=3, profit_capture=Decimal("0.9"))

    assert time_exit_triggered(group, close_debit_usdc=None, ctx=ctx) is True


def test_covered_call_skips_time_exit_when_only_mark_implied_capture(tmp_path):
    """Wide-spread / empty books must not trigger time exit on stale profit_capture."""
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        tp_capture_pct=Decimal("0.95"),
        income_exit_max_spread_ratio=Decimal("0.50"),
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0.01"),
        enable_early_exit=False,
        covered_call_spot_exit_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
    group.entry_credit = Decimal("26.4052278")
    group.profit_capture = Decimal("0.9")
    book = OrderBookSnapshot(
        instrument_name=group.short_instrument_name,
        best_bid_price=Decimal("0.0015"),
        best_bid_amount=Decimal("0.1"),
        best_ask_price=Decimal("0.0037"),
        best_ask_amount=Decimal("0.1"),
        mark_price=Decimal("0.0020"),
        index_price=Decimal("62992.05"),
        delta=Decimal("0.075"),
        iv=Decimal("0.5"),
        open_interest=Decimal("10"),
    )
    ctx = SimpleNamespace(orderbook_cache={group.short_instrument_name: book})

    with patch.object(engine, "_close_group") as close_mock:
        actions = engine._manage_covered_call_group(ctx, group, live=False)

    close_mock.assert_not_called()
    assert actions == []


def test_covered_call_time_exit_still_triggers_when_min_capture_zero(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        tp_capture_pct=Decimal("0.95"),
        income_exit_max_spread_ratio=Decimal("0.50"),
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0"),
        enable_early_exit=False,
        covered_call_spot_exit_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
    group.profit_capture = Decimal("0.9")
    book = OrderBookSnapshot(
        instrument_name=group.short_instrument_name,
        best_bid_price=Decimal("0.0015"),
        best_bid_amount=Decimal("0.1"),
        best_ask_price=Decimal("0.0037"),
        best_ask_amount=Decimal("0.1"),
        mark_price=Decimal("0.0020"),
        index_price=Decimal("62992.05"),
        delta=Decimal("0.075"),
        iv=Decimal("0.5"),
        open_interest=Decimal("10"),
    )
    ctx = SimpleNamespace(orderbook_cache={group.short_instrument_name: book})

    with patch.object(
        engine,
        "_close_group",
        return_value=[{"action": "close_group_preview", "reason": "time_exit"}],
    ) as close_mock:
        actions = engine._manage_covered_call_group(ctx, group, live=False)

    close_mock.assert_called_once_with(ctx, group, reason="time_exit", live=False)
    assert actions[0]["reason"] == "time_exit"
