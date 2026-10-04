"""PAUSE_NEW_ENTRIES: operator wind-down blocks new groups, not management."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from conftest import FakeClient, make_config
from test_cash_secured_ops import _itm_sold_group
from test_csp_active_roll import (
    NEXT,
    _open_csp,
    _parent,
    _roll_actions,
    _roll_books,
    _roll_engine,
    _seed,
    _short_position,
)
from test_engine import _build_group, _covered_call_group

from deribit_engine.config import load_config
from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.entry_gates import PAUSE_NEW_ENTRIES_REASON
from deribit_engine.models import StrategyState


def test_pause_new_entries_defaults_false(tmp_path):
    assert make_config(tmp_path).pause_new_entries is False
    env_file = tmp_path / ".env"
    env_file.write_text("")
    assert load_config(env_file, require_private=False).pause_new_entries is False


def test_load_config_parses_pause_new_entries(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PAUSE_NEW_ENTRIES=true\n")
    config = load_config(env_file, require_private=False)
    assert config.pause_new_entries is True


def _scan_kwargs(strategy: str) -> dict:
    if strategy == "covered_call":
        return {
            "option_strategy": "covered_call",
            "option_markets_profile": "inverse_native",
            "min_net_apr": Decimal("0.05"),
        }
    if strategy == "bull_put_spread":
        return {
            "option_strategy": "bull_put_spread",
            "option_markets_profile": "linear_usdc",
            "min_net_apr": Decimal("0.05"),
            "linear_min_book_notional_usdc": Decimal("3000"),
            "bull_put_long_delta_min": Decimal("0.04"),
            "bull_put_long_delta_max": Decimal("0.06"),
        }
    return {
        "option_strategy": "naked_short",
        "option_markets_profile": "linear_usdc",
        "min_net_apr": Decimal("0.05"),
    }


def _scan_engine(tmp_path, strategy: str, *, pause: bool):
    work = tmp_path / strategy
    work.mkdir(parents=True, exist_ok=True)
    client = FakeClient(btc_book_equity="0.2") if strategy == "covered_call" else FakeClient()
    return DeribitOptionTrialBot(
        make_config(work, pause_new_entries=pause, **_scan_kwargs(strategy)),
        client,
    )


def test_pause_false_still_scans_each_strategy(tmp_path):
    for strategy, currencies in (
        ("bull_put_spread", ("BTC",)),
        ("covered_call", ("BTC",)),
    ):
        engine = _scan_engine(tmp_path, strategy, pause=False)
        result = engine.scan(currencies=currencies, top_n=1)
        assert PAUSE_NEW_ENTRIES_REASON not in result["entry_blockers"]
        assert result["candidates"]
    naked = _scan_engine(tmp_path, "naked_short", pause=False)
    naked_scan = naked.scan(top_n=1)
    assert PAUSE_NEW_ENTRIES_REASON not in naked_scan["entry_blockers"]


def test_pause_blocks_scan_for_each_strategy(tmp_path):
    for strategy, currencies in (
        ("naked_short", None),
        ("bull_put_spread", ("BTC",)),
        ("covered_call", ("BTC",)),
    ):
        engine = _scan_engine(tmp_path, strategy, pause=True)
        result = engine.scan(currencies=currencies, top_n=1)
        assert result["candidates"] == []
        assert result["entry_blockers"] == [PAUSE_NEW_ENTRIES_REASON]
        assert PAUSE_NEW_ENTRIES_REASON in result["portfolio"]["halt_entry_reasons"]
        assert result["portfolio"]["halt_new_entries"] is True


def test_pause_skips_enter_best_for_each_strategy(tmp_path):
    open_engine = _scan_engine(tmp_path, "covered_call", pause=False)
    assert open_engine.scan(currencies=("BTC",), top_n=1)["candidates"]
    open_enter = open_engine.enter_best(currencies=("BTC",), live=False)
    assert open_enter["action"] != "entry_skipped" or open_enter.get("reason") != PAUSE_NEW_ENTRIES_REASON

    for strategy in ("naked_short", "bull_put_spread", "covered_call"):
        paused = _scan_engine(tmp_path, strategy, pause=True)
        result = paused.enter_best(live=False)
        assert result["action"] == "entry_skipped"
        assert result["reason"] == PAUSE_NEW_ENTRIES_REASON


def test_pause_does_not_block_covered_call_take_profit(tmp_path):
    engine = DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            pause_new_entries=True,
            tp_capture_pct=Decimal("0.55"),
            time_exit_dte=4,
            enable_early_exit=True,
            covered_call_spot_exit_enabled=False,
        ),
        FakeClient(btc_book_equity="0.5"),
    )
    group = _covered_call_group(dte_days=14, strike=Decimal("77000"))
    group.profit_capture = Decimal("0.9")
    ctx = SimpleNamespace(
        snapshot=SimpleNamespace(
            halt_new_entries=True,
            portfolio_wide_entry_halt=False,
            halt_new_entries_by_currency={"BTC": True},
            halt_entries_by_book={"BTC": True},
        ),
        orderbook_cache={},
    )
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("10")):
        with patch.object(
            engine,
            "_close_group",
            return_value=[{"action": "close_group_preview", "reason": "take_profit"}],
        ) as close_mock:
            actions = engine._manage_covered_call_group(ctx, group, live=False)
    close_mock.assert_called_once_with(ctx, group, reason="take_profit", live=False)
    assert actions[0]["reason"] == "take_profit"


def test_pause_does_not_block_time_exit_or_hard_stop(tmp_path):
    engine = DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_markets_profile="linear_usdc",
            pause_new_entries=True,
            enable_early_exit=False,
            time_exit_dte=5,
            hard_defense_delta=Decimal("0.30"),
            defense_confirm_cycles=1,
        ),
        FakeClient(),
    )
    time_group = _build_group(short_instrument_name="BTC_USDC-14APR30-63000-P", dte_days=3)
    time_group.current_debit = time_group.entry_credit
    time_ctx = SimpleNamespace(orderbook_cache={})
    with patch.object(engine, "_income_exit_close_debit", return_value=Decimal("5")):
        with patch.object(
            engine,
            "_close_group",
            return_value=[{"action": "close_group_preview", "reason": "time_flatten"}],
        ) as close_mock:
            actions = engine._manage_group(time_ctx, time_group, live=False)
    close_mock.assert_called_once_with(time_ctx, time_group, reason="time_flatten", live=False)
    assert actions[0]["reason"] == "time_flatten"

    hard_group = _build_group(short_instrument_name="BTC_USDC-14APR30-63000-P", dte_days=14)
    hard_group.current_debit = hard_group.entry_credit
    hard_group.short_delta = Decimal("0.40")
    hard_ctx = SimpleNamespace(orderbook_cache={})
    with patch.object(
        engine,
        "_close_group",
        return_value=[{"action": "close_group_preview", "reason": "hard_stop"}],
    ) as close_mock:
        actions = engine._manage_group(hard_ctx, hard_group, live=False)
    close_mock.assert_called_once_with(hard_ctx, hard_group, reason="hard_stop", live=False)
    assert actions[0]["reason"] == "hard_stop"


def test_pause_blocks_wheel_next_put_after_assignment(tmp_path):
    client = FakeClient(btc_book_equity="0.2")
    engine = DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            pause_new_entries=True,
            covered_call_spot_exit_enabled=True,
            covered_call_itm_to_cash_secured_enabled=True,
            covered_call_csp_dte_min=2,
            covered_call_csp_dte_max=21,
            linear_min_book_notional_usdc=Decimal("1000"),
            linear_min_open_interest=Decimal("1"),
        ),
        client,
    )
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.manage(live=False)
    assert not any(a.get("action") == "cash_secured_preview" for a in result["actions"])
    skips = [a for a in result["actions"] if a.get("action") == "cash_secured_skipped"]
    assert skips
    assert any(a.get("reason") == PAUSE_NEW_ENTRIES_REASON for a in skips)
    assert not any(a.get("action") == "cash_secured_entered" for a in result["actions"])


def test_pause_still_allows_csp_active_roll_of_existing_put(tmp_path):
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client, pause_new_entries=True)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is True
    assert rolls[0]["replacement_instrument"] == NEXT
    assert any(
        a.get("action") == "close_group_preview" and a.get("reason") == "csp_active_roll" for a in result["actions"]
    )
