"""YM unhedged naked-short medium: thresholds, time_flatten taker, entry filters."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from conftest import FakeClient, make_config
from test_engine import _build_group

from deribit_engine.config import load_config
from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.exit_eval import evaluate_defense_triggers, exit_eval_context_from_config
from deribit_engine.exit_reasons import DEFENSE_EXIT_REASONS, INCOME_EXIT_REASONS
from deribit_engine.models import OptionInstrument, OrderBookSnapshot, StrategyState
from deribit_engine.strategy import StrategySelector
from deribit_engine.vol_metrics import TrendReading

REPO = Path(__file__).resolve().parent.parent


def _naked_sub_account_config(*account_lines: str, investor_lines: tuple[str, ...] = ()) -> object:
    with tempfile.TemporaryDirectory(dir=REPO / "config" / "investors", prefix=".test-naked-") as tmp:
        investor = Path(tmp)
        if investor_lines:
            (investor / ".env.investor").write_text("\n".join(investor_lines) + "\n")
        accounts = investor / "accounts"
        accounts.mkdir()
        account = accounts / ".env.naked"
        base = [
            "DERIBIT_ENV=mainnet",
            "OPTION_STRATEGY=naked_short",
            f"STATE_FILE={tmp}/state.json",
        ]
        account.write_text("\n".join([*base, *account_lines]) + "\n")
        return load_config(account, require_private=False)


def test_naked_medium_profile_loads_unhedged_thresholds():
    config = _naked_sub_account_config()
    assert config.option_strategy == "naked_short"
    assert config.risk_tier == "medium"
    assert config.entry_dte_min == 14
    assert config.entry_dte_max == 45
    assert config.btc_put_delta_min == Decimal("0.07")
    assert config.btc_put_delta_max == Decimal("0.20")
    assert config.btc_preferred_put_delta_min == Decimal("0.08")
    assert config.btc_preferred_put_delta_max == Decimal("0.15")
    assert config.btc_put_otm_min == Decimal("0.08")
    assert config.eth_put_delta_min == Decimal("0.06")
    assert config.eth_put_delta_max == Decimal("0.16")
    assert config.eth_preferred_put_delta_min == Decimal("0.07")
    assert config.eth_preferred_put_delta_max == Decimal("0.13")
    assert config.eth_put_otm_min == Decimal("0.10")
    assert config.min_net_apr == Decimal("0.05")
    assert config.linear_min_open_interest == Decimal("8")
    assert config.linear_min_book_notional_usdc == Decimal("4000")
    assert config.linear_max_spread_ratio == Decimal("0.10")
    assert config.max_groups_per_currency == 1
    assert config.max_concurrent_groups == 2
    assert config.per_leg_im_cap_put == Decimal("0.10")
    assert config.expiry_im_cap_per_book == Decimal("0.20")
    assert config.book_im_target == Decimal("0.22")
    assert config.book_im_hard == Decimal("0.40")
    assert config.book_mm_target == Decimal("0.18")
    assert config.book_mm_hard == Decimal("0.32")
    assert config.halt_open_max_loss_pct == Decimal("0.40")
    assert config.naked_allow_elevated_entry is True
    assert config.enable_naked_topup is False
    assert config.tp_capture_pct_dte_long == Decimal("0.50")
    assert config.tp_capture_pct == Decimal("0.55")
    assert config.tp_capture_pct_dte_short == Decimal("0.60")
    assert config.enable_early_exit is True
    assert config.early_exit_min_profit_capture == Decimal("0.4")
    assert config.early_exit_remaining_apr == Decimal("0.04")
    assert config.time_exit_dte == 4
    assert config.time_exit_min_profit_capture == Decimal("0")
    assert config.soft_defense_delta == Decimal("0.20")
    assert config.hard_defense_delta == Decimal("0.28")
    assert config.soft_defense_loss_pct == Decimal("0.22")
    assert config.hard_stop_loss_pct == Decimal("0.32")
    assert config.defense_confirm_cycles == 2
    assert config.defense_trigger_use_mark is True
    assert config.naked_entry_below_ma_pct == Decimal("0.02")
    assert config.naked_block_second_ccy_on_mark_loss is True

    # Preferred bands must sit inside the hard bands, or the ranking target is
    # unreachable and every candidate is rejected on delta.
    for coin in ("btc", "eth"):
        dmin = getattr(config, f"{coin}_put_delta_min")
        dmax = getattr(config, f"{coin}_put_delta_max")
        pmin = getattr(config, f"{coin}_preferred_put_delta_min")
        pmax = getattr(config, f"{coin}_preferred_put_delta_max")
        assert dmin <= pmin <= pmax <= dmax, coin
        omin = getattr(config, f"{coin}_put_otm_min")
        omax = getattr(config, f"{coin}_put_otm_max")
        pomin = getattr(config, f"{coin}_preferred_otm_min")
        pomax = getattr(config, f"{coin}_preferred_otm_max")
        assert omin <= pomin <= pomax <= omax, coin


def test_jack_style_investor_hedge_survives_naked_skeleton():
    config = _naked_sub_account_config(
        investor_lines=(
            "ENABLE_PERP_HEDGE=true",
            "HEDGE_FIRST_ON_HARD=true",
            "PER_POSITION_HEDGE=true",
        )
    )
    assert config.enable_perp_hedge is True
    assert config.hedge_first_on_hard is True
    assert config.per_position_hedge is True


def test_ym_style_investor_stays_unhedged():
    config = _naked_sub_account_config(investor_lines=("ENABLE_PERP_HEDGE=false", "HEDGE_FIRST_ON_HARD=false"))
    assert config.enable_perp_hedge is False
    assert config.hedge_first_on_hard is False


def test_soft_hard_defense_deltas_match_medium(tmp_path):
    config = make_config(
        tmp_path,
        soft_defense_delta=Decimal("0.20"),
        hard_defense_delta=Decimal("0.28"),
        soft_defense_loss_pct=Decimal("0.22"),
        hard_stop_loss_pct=Decimal("0.32"),
        defense_trigger_use_mark=True,
    )
    ctx = exit_eval_context_from_config(config)
    group = _build_group(short_instrument_name="BTC_USDC-14APR30-63000-P")
    group.short_delta = Decimal("0.21")
    group.mark_debit = group.entry_credit
    soft, hard = evaluate_defense_triggers(
        group,
        soft_delta=config.soft_defense_delta,
        hard_delta=config.hard_defense_delta,
        ctx=ctx,
    )
    assert soft is True
    assert hard is False
    group.short_delta = Decimal("0.28")
    soft, hard = evaluate_defense_triggers(
        group,
        soft_delta=config.soft_defense_delta,
        hard_delta=config.hard_defense_delta,
        ctx=ctx,
    )
    assert soft is True
    assert hard is True


def test_naked_dte_flatten_uses_time_flatten_taker(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        option_markets_profile="linear_usdc",
        enable_early_exit=False,
        time_exit_dte=4,
        time_exit_min_profit_capture=Decimal("0"),
        tp_capture_pct=Decimal("0.99"),
        enable_dynamic_tp=False,
        soft_defense_delta=Decimal("0.99"),
        hard_defense_delta=Decimal("0.99"),
        income_exit_time_in_force="good_til_cancelled",
    )
    engine = DeribitOptionTrialBot(config, FakeClient())
    group = _build_group(short_instrument_name="BTC_USDC-14APR30-63000-P", dte_days=3)
    group.current_debit = Decimal("20")
    group.mark_debit = Decimal("20")
    group.entry_credit = Decimal("10")
    ctx = SimpleNamespace(orderbook_cache={})

    assert "time_flatten" in DEFENSE_EXIT_REASONS
    assert "time_flatten" not in INCOME_EXIT_REASONS
    assert engine._close_time_in_force("time_flatten") == "immediate_or_cancel"
    assert engine._income_exit_uses_resting_limit("time_flatten") is False
    assert engine._close_time_in_force("time_exit") == "good_til_cancelled"

    with (
        patch.object(engine, "_take_profit_triggered", return_value=False),
        patch.object(
            engine,
            "_close_group",
            return_value=[{"action": "close_group_preview", "reason": "time_flatten"}],
        ) as close_mock,
    ):
        actions = engine._manage_group(ctx, group, live=False)

    close_mock.assert_called_once_with(ctx, group, reason="time_flatten", live=False)
    assert actions[0]["reason"] == "time_flatten"


def test_time_flatten_close_price_is_defense_taker(tmp_path):
    config = make_config(tmp_path, exit_buffer_ratio=Decimal("0.03"))
    selector = StrategySelector(config)
    instrument = OptionInstrument.from_api(
        {
            "instrument_name": "BTC_USDC-26JUN26-66000-P",
            "base_currency": "BTC",
            "quote_currency": "USDC",
            "settlement_currency": "USDC",
            "instrument_type": "linear",
            "tick_size": "5",
            "tick_size_steps": [],
            "min_trade_amount": "0.01",
            "contract_size": "1",
            "option_type": "put",
            "expiration_timestamp": 1782460800000,
            "strike": "66000",
            "instrument_state": "open",
        }
    )
    book = OrderBookSnapshot(
        instrument_name="BTC_USDC-26JUN26-66000-P",
        best_bid_price=Decimal("80"),
        best_bid_amount=Decimal("1"),
        best_ask_price=Decimal("100"),
        best_ask_amount=Decimal("1"),
        mark_price=Decimal("90"),
        index_price=Decimal("70000"),
        delta=Decimal("0.10"),
        iv=Decimal("0.5"),
        open_interest=Decimal("100"),
    )
    flatten = selector.close_buy_price_for_exit(instrument, book, reason="time_flatten")
    income = selector.close_buy_price_for_exit(instrument, book, reason="time_exit")
    assert flatten >= Decimal("100")
    assert flatten >= income


def test_spot_below_ma20_skips_new_naked_entry(tmp_path):
    selector = StrategySelector(
        make_config(
            tmp_path,
            option_strategy="naked_short",
            naked_entry_below_ma_pct=Decimal("0.02"),
        )
    )
    selector.update_vol_entry_context(
        trend_by_currency={
            "BTC": TrendReading(signal=Decimal("-1"), deviation=Decimal("-0.025"), bull_regime=False),
        }
    )
    reason = selector.naked_below_ma_reason_zh("BTC")
    assert reason is not None
    assert "低於" in reason
    selector.update_vol_entry_context(
        trend_by_currency={
            "BTC": TrendReading(signal=Decimal("-0.2"), deviation=Decimal("-0.01"), bull_regime=False),
        }
    )
    assert selector.naked_below_ma_reason_zh("BTC") is None


def test_mark_loss_blocks_second_naked_currency(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        naked_block_second_ccy_on_mark_loss=True,
    )
    engine = DeribitOptionTrialBot(config, FakeClient())
    btc = _build_group(short_instrument_name="BTC_USDC-14APR30-63000-P", currency="BTC")
    btc.mark_debit = Decimal("20")
    btc.entry_credit = Decimal("10")
    state = StrategyState(groups=[btc])
    assert engine._naked_blocks_second_currency(state, "ETH") is True
    assert engine._naked_blocks_second_currency(state, "BTC") is False
    btc.mark_debit = Decimal("5")
    assert engine._naked_blocks_second_currency(state, "ETH") is False
