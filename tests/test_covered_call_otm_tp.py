"""Covered-call OTM take-profit table: 75/70 capture, early-exit off, skip near-strike / halt / thin TV."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from conftest import FakeClient, make_config
from test_engine import _covered_call_group

from deribit_engine.config import load_config
from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import OrderBookSnapshot


def _cc_tp_config(tmp_path, **overrides):
    values = dict(
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        enable_dynamic_tp=True,
        tp_dte_long_threshold=Decimal("14"),
        tp_dte_short_threshold=Decimal("7"),
        tp_capture_pct_dte_long=Decimal("0.75"),
        tp_capture_pct=Decimal("0.70"),
        tp_capture_pct_dte_short=Decimal("0.70"),
        enable_early_exit=False,
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0.10"),
        covered_call_spot_exit_enabled=False,
        income_exit_max_spread_ratio=Decimal("0.50"),
    )
    values.update(overrides)
    return make_config(tmp_path, **values)


def _far_otm_book(
    instrument: str, *, bid: str = "0.004", ask: str = "0.0042", delta: str = "0.08"
) -> OrderBookSnapshot:
    bid_px = Decimal(bid)
    ask_px = Decimal(ask)
    return OrderBookSnapshot(
        instrument_name=instrument,
        best_bid_price=bid_px,
        best_bid_amount=Decimal("1"),
        best_ask_price=ask_px,
        best_ask_amount=Decimal("1"),
        mark_price=(bid_px + ask_px) / Decimal("2"),
        index_price=Decimal("70000"),
        delta=Decimal(delta),
        iv=Decimal("0.5"),
        open_interest=Decimal("100"),
    )


def _ctx(group, book: OrderBookSnapshot | None = None, **extra):
    payload = {
        "orderbook_cache": {group.short_instrument_name: book or _far_otm_book(group.short_instrument_name)},
        "markets_by_currency": {},
    }
    payload.update(extra)
    return SimpleNamespace(**payload)


def _engine(tmp_path, **overrides):
    return DeribitOptionTrialBot(_cc_tp_config(tmp_path, **overrides), FakeClient(btc_book_equity="0.5"))


def test_load_config_income_exit_defaults(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DERIBIT_ENV=mainnet\nOPTION_STRATEGY=naked_short\n")
    config = load_config(env_file, require_private=False)
    assert config.enable_dynamic_tp is True
    assert config.tp_dte_long_threshold == Decimal("14")
    assert config.tp_dte_short_threshold == Decimal("7")
    assert config.tp_capture_pct_dte_long == Decimal("0.75")
    assert config.tp_capture_pct == Decimal("0.70")
    assert config.tp_capture_pct_dte_short == Decimal("0.70")
    assert config.enable_early_exit is False
    assert config.time_exit_dte == 4
    assert config.time_exit_min_profit_capture == Decimal("0.10")


def test_long_dte_forty_percent_does_not_take_profit(tmp_path):
    engine = _engine(tmp_path)
    group = _covered_call_group(dte_days=20, strike=Decimal("77000"))
    ctx = _ctx(group)
    # entry_credit=30 → close 18 is 40% capture; old long-DTE table used 40%.
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("18")):
        with patch.object(engine, "_close_group") as close_mock:
            actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_not_called()
    assert actions == []


def test_long_dte_seventy_five_percent_takes_profit(tmp_path):
    engine = _engine(tmp_path)
    group = _covered_call_group(dte_days=20, strike=Decimal("77000"))
    ctx = _ctx(group)
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("7.5")):
        with patch.object(
            engine,
            "_close_group",
            return_value=[{"action": "close_group_preview", "reason": "take_profit"}],
        ) as close_mock:
            actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_called_once_with(ctx, group, reason="take_profit", live=False)
    assert actions[0]["reason"] == "take_profit"


def test_early_exit_stays_off_even_when_remaining_apr_is_low(tmp_path):
    engine = _engine(tmp_path)
    assert engine.config.enable_early_exit is False
    group = _covered_call_group(dte_days=14, strike=Decimal("77000"))
    group.current_debit = Decimal("5.1")
    group.current_close_fee = Decimal("0.1")
    group.profit_capture = Decimal("0.83")
    ctx = _ctx(group)
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("12")):
        with patch.object(engine, "_close_group") as close_mock:
            actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_not_called()
    assert actions == []
    assert engine._maybe_early_exit_reason(ctx, group) is None


def test_near_strike_otm_band_skips_take_profit_and_time_exit(tmp_path):
    engine = _engine(tmp_path)
    group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
    ctx = _ctx(group)
    with patch.object(engine, "_covered_call_index_price", return_value=Decimal("75000")):
        with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("5")):
            with patch.object(engine, "_close_group") as close_mock:
                actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_not_called()
    assert actions == []


def test_near_strike_delta_skips_take_profit_and_time_exit(tmp_path):
    engine = _engine(tmp_path)
    group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
    group.short_delta = Decimal("0.20")
    ctx = _ctx(group, _far_otm_book(group.short_instrument_name, delta="0.08"))
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("5")):
        with patch.object(engine, "_close_group") as close_mock:
            actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_not_called()
    assert actions == []


def test_halt_new_entries_blocks_take_profit_not_time_exit(tmp_path):
    engine = _engine(tmp_path)
    snapshot = SimpleNamespace(
        halt_new_entries=True,
        portfolio_wide_entry_halt=False,
        halt_new_entries_by_currency={"BTC": True},
        halt_entries_by_book={"BTC": True},
    )
    tp_group = _covered_call_group(dte_days=20, strike=Decimal("77000"))
    tp_ctx = _ctx(tp_group, snapshot=snapshot)
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("5")):
        with patch.object(engine, "_close_group") as close_mock:
            assert engine._manage_covered_call_group(tp_ctx, tp_group, live=False) == []
    close_mock.assert_not_called()

    te_group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
    te_ctx = _ctx(te_group, snapshot=snapshot)
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("20")):
        with patch.object(
            engine,
            "_close_group",
            return_value=[{"action": "close_group_preview", "reason": "time_exit"}],
        ) as close_mock:
            actions = engine._manage_covered_call_group(te_ctx, te_group, live=False)
    close_mock.assert_called_once_with(te_ctx, te_group, reason="time_exit", live=False)
    assert actions[0]["reason"] == "time_exit"


def test_trend_pause_blocks_take_profit_not_time_exit(tmp_path):
    engine = _engine(tmp_path)
    tp_group = _covered_call_group(dte_days=20, strike=Decimal("77000"))
    tp_ctx = _ctx(tp_group)
    with patch.object(engine.strategy, "trend_pause_reason_zh", return_value="暫緩開新倉"):
        with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("5")):
            with patch.object(engine, "_close_group") as close_mock:
                assert engine._manage_covered_call_group(tp_ctx, tp_group, live=False) == []
        close_mock.assert_not_called()

        te_group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
        te_ctx = _ctx(te_group)
        with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("20")):
            with patch.object(
                engine,
                "_close_group",
                return_value=[{"action": "close_group_preview", "reason": "time_exit"}],
            ) as close_mock:
                actions = engine._manage_covered_call_group(te_ctx, te_group, live=False)
        close_mock.assert_called_once_with(te_ctx, te_group, reason="time_exit", live=False)
        assert actions[0]["reason"] == "time_exit"


def test_residual_tv_below_two_fees_does_not_buy_back(tmp_path):
    engine = _engine(tmp_path)
    group = _covered_call_group(dte_days=20, strike=Decimal("77000"))
    # qty 0.1 × ask 0.0002 = 0.00002 ≤ 2 × (0.0003 × 0.1) = 0.00006
    thin = _far_otm_book(group.short_instrument_name, bid="0.00015", ask="0.0002", delta="0.04")
    ctx = _ctx(group, thin)
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("1")):
        with patch.object(engine, "_close_group") as close_mock:
            actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_not_called()
    assert actions == []
