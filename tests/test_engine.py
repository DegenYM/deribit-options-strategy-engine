from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deribit_demo.engine import DeribitOptionTrialBot
from deribit_demo.models import (
    AccountSummary,
    OrderBookSnapshot,
    Position,
    RiskRegime,
    StrategyState,
    TradeGroup,
    is_phantom_reconcile_close,
)
from deribit_demo.utils import utc_now, utc_now_ms
from deribit_demo.state import performance_exclusions_path

from conftest import FakeClient, future_expiry, make_config


def _build_group(
    *,
    short_instrument_name: str,
    currency: str = "BTC",
    collateral_currency: str = "USDC",
    quantity: Decimal = Decimal("0.1"),
    short_strike: Decimal = Decimal("63000"),
    entry_credit: Decimal = Decimal("10"),
    max_loss: Decimal = Decimal("50"),
    dte_days: int = 14,
) -> TradeGroup:
    return TradeGroup(
        group_id="0001",
        currency=currency,
        collateral_currency=collateral_currency,
        quantity=quantity,
        entry_timestamp_ms=1,
        expiration_timestamp_ms=future_expiry(dte_days),
        short_instrument_name=short_instrument_name,
        short_strike=short_strike,
        entry_credit=entry_credit,
        original_entry_credit=entry_credit,
        max_loss=max_loss,
        regime_at_entry="normal",
    )


def test_scan_returns_naked_put_candidates(tmp_path, fake_client):
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        enable_naked_topup=True,
        min_net_apr=Decimal("0.05"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    result = engine.scan()

    assert result["strategy_mode"] == "naked_short"
    assert "candidates" in result
    assert "portfolio" in result
    if result["candidates"]:
        c = result["candidates"][0]
        assert c["strategy"] == "naked_short"
        assert c["short_instrument_name"].endswith("-P")
        assert Decimal(c["net_apr"]) > 0


def test_scan_returns_bull_put_spread_candidates(tmp_path, fake_client):
    config = make_config(
        tmp_path,
        option_strategy="bull_put_spread",
        option_markets_profile="linear_usdc",
        min_net_apr=Decimal("0.05"),
        linear_min_book_notional_usdc=Decimal("3000"),
        bull_put_long_delta_min=Decimal("0.04"),
        bull_put_long_delta_max=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.scan(currencies=("BTC",), top_n=1)

    assert result["strategy_mode"] == "bull_put_spread"
    assert result["candidates"]
    candidate = result["candidates"][0]
    assert candidate["strategy"] == "bull_put_spread"
    assert candidate["short_instrument_name"].endswith("-P")
    assert candidate["long_instrument_name"].endswith("-P")
    assert Decimal(candidate["long_strike"]) < Decimal(candidate["short_strike"])


def test_scan_returns_covered_call_only_with_existing_cover(tmp_path):
    client = FakeClient(btc_book_equity="0.2")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        min_net_apr=Decimal("0.05"),
    )
    engine = DeribitOptionTrialBot(config, client)

    result = engine.scan(currencies=("BTC",), top_n=1)

    assert result["strategy_mode"] == "covered_call"
    assert result["candidates"]
    candidate = result["candidates"][0]
    assert candidate["strategy"] == "covered_call"
    assert candidate["short_instrument_name"].endswith("-C")
    assert Decimal(candidate["covered_underlying_quantity"]) > 0


def test_covered_call_live_entry_preserves_strategy_after_recheck(tmp_path):
    client = FakeClient(btc_book_equity="0.2")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        min_net_apr=Decimal("0.05"),
    )
    engine = DeribitOptionTrialBot(config, client)

    result = engine.enter_best(currencies=("BTC",), live=True)

    assert result["action"] == "covered_call_entered"
    assert result["candidate"]["strategy"] == "covered_call"
    assert result["execution_mode"] == "covered_call"
    assert result["group"].strategy == "covered_call"
    assert result["group"].option_type == "call"
    assert result["group"].covered_underlying_quantity > 0


def test_covered_call_scan_payload_uses_covered_call_diagnostics(tmp_path):
    client = FakeClient(btc_book_equity="0")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("BTC",),
        enable_short_put=False,
        enable_short_call=True,
        short_call_fallback_only=True,
        min_net_apr=Decimal("0.05"),
    )
    engine = DeribitOptionTrialBot(config, client)

    result = engine.scan(currencies=("BTC",), top_n=1)

    assert result["strategy_mode"] == "covered_call"
    assert result["scan_policy"]["note_zh"] is None
    assert result["scan_rejections"]["BTC"]["calls_in_dte_window"] == 2
    assert result["scan_rejections"]["BTC"]["after_liquidity_rejections"] == {
        "available_cover_quantity<=0": 2
    }
    assert result["scan_rejections_short_call"] is None
    assert result["entry_blockers"]
    assert all("naked" not in blocker for blocker in result["entry_blockers"])
    assert any("[covered_call]" in blocker for blocker in result["entry_blockers"])


def test_covered_call_scan_skips_book_im_target_when_native_cover_available(tmp_path):
    client = FakeClient(
        eth_book_equity="10",
        eth_initial_margin="3.7",
        eth_maintenance_margin="0.01",
    )
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("ETH",),
        book_im_target=Decimal("0.35"),
        book_mm_target=Decimal("0.22"),
        min_net_apr=Decimal("0.05"),
        eth_call_delta_min=Decimal("0.08"),
        eth_call_delta_max=Decimal("0.14"),
        eth_call_otm_min=Decimal("0.08"),
        eth_call_otm_max=Decimal("0.14"),
    )
    engine = DeribitOptionTrialBot(config, client)

    result = engine.scan(currencies=("ETH",), top_n=1)

    assert not any("im_ratio" in blocker for blocker in result["entry_blockers"])
    assert not any("mm_ratio" in blocker for blocker in result["entry_blockers"])


def test_covered_call_group_caps_ignore_existing_naked_put_groups(tmp_path):
    client = FakeClient(eth_book_equity="5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("ETH",),
        max_concurrent_groups=3,
        max_groups_per_currency=3,
        min_net_apr=Decimal("0.05"),
        eth_call_delta_min=Decimal("0.10"),
        eth_call_delta_max=Decimal("0.12"),
        eth_call_otm_min=Decimal("0.08"),
        eth_call_otm_max=Decimal("0.12"),
        eth_put_delta_min=Decimal("0.10"),
        eth_put_delta_max=Decimal("0.12"),
        eth_put_otm_min=Decimal("0.08"),
        eth_put_otm_max=Decimal("0.12"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    for index, short in enumerate(
        (
            "ETH-14APR30-3150-P",
            "ETH-21APR30-3150-P",
            "ETH-14APR30-3100-P",
        ),
        start=1,
    ):
        group = _build_group(
            short_instrument_name=short,
            currency="ETH",
            collateral_currency="ETH",
            quantity=Decimal("1"),
            short_strike=Decimal("3150"),
        )
        group.group_id = f"{index:04d}"
        group.strategy = "naked_short"
        group.option_type = "put"
        state.groups.append(group)
    engine.state_store.save(state)

    result = engine.scan(currencies=("ETH",), top_n=1)

    assert result["entry_blockers"] == []
    assert result["candidates"]
    assert result["candidates"][0]["strategy"] == "covered_call"


def _covered_call_group(*, dte_days: int = 0, strike: Decimal = Decimal("69000")) -> TradeGroup:
    group = _build_group(
        short_instrument_name="BTC-14APR30-77000-C",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        short_strike=strike,
        entry_credit=Decimal("30"),
        max_loss=Decimal("1000"),
        dte_days=dte_days,
    )
    group.option_type = "call"
    group.strategy = "covered_call"
    group.covered_underlying_quantity = Decimal("0.1")
    return group


def _short_call_position(group: TradeGroup) -> dict:
    return {
        "instrument_name": group.short_instrument_name,
        "direction": "sell",
        "kind": "option",
        "size": str(group.quantity),
        "size_currency": str(group.quantity),
        "mark_price": "0.0033",
        "average_price": "0.0032",
        "floating_profit_loss": "0",
        "delta": "0.11",
    }


def test_covered_call_manage_uses_call_specific_defense_delta(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        enable_early_exit=False,
        time_exit_dte=0,
        soft_defense_delta=Decimal("0.20"),
        hard_defense_delta=Decimal("0.28"),
        soft_defense_delta_call=Decimal("0.35"),
        hard_defense_delta_call=Decimal("0.50"),
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=14)
    group.short_delta = Decimal("0.26")

    actions = engine._manage_group(SimpleNamespace(orderbook_cache={}), group, live=False)

    assert actions == []


def test_covered_call_manage_ignores_option_leg_stop_loss_when_covered(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        enable_early_exit=False,
        time_exit_dte=0,
        soft_defense_delta_call=Decimal("0.35"),
        hard_defense_delta_call=Decimal("0.50"),
        soft_defense_loss_pct=Decimal("0.30"),
        hard_stop_loss_pct=Decimal("0.45"),
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=14)
    group.short_delta = Decimal("0.75")
    group.current_debit = Decimal("700")

    actions = engine._manage_group(SimpleNamespace(orderbook_cache={}), group, live=False)

    assert actions == []
    assert group.status == "open"


def test_covered_call_robust_exit_dry_run_previews_call_close_and_spot_sell(tmp_path):
    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        time_exit_dte=0,
        hard_defense_delta=Decimal("1"),
        soft_defense_delta=Decimal("1"),
        hard_defense_delta_call=Decimal("1"),
        soft_defense_delta_call=Decimal("1"),
        covered_call_spot_exit_enabled=True,
        covered_call_robust_exit_enabled=True,
        covered_call_robust_exit_dte=Decimal("1"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    group = _covered_call_group()
    state.groups.append(group)
    engine.state_store.save(state)
    client.positions = [_short_call_position(group)]

    result = engine.manage(live=False)

    actions = result["actions"]
    assert actions[0]["action"] == "close_group_preview"
    assert actions[0]["reason"] == "covered_call_robust_exit"
    assert actions[1]["action"] == "covered_call_spot_exit_preview"
    assert actions[1]["instrument_name"] == "BTC_USDC"
    assert actions[1]["amount"] == "0.1"


def test_covered_call_robust_exit_live_sells_spot_after_call_close(tmp_path):
    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        time_exit_dte=0,
        hard_defense_delta=Decimal("1"),
        soft_defense_delta=Decimal("1"),
        hard_defense_delta_call=Decimal("1"),
        soft_defense_delta_call=Decimal("1"),
        covered_call_spot_exit_enabled=True,
        covered_call_robust_exit_enabled=True,
        covered_call_robust_exit_dte=Decimal("1"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    group = _covered_call_group()
    state.groups.append(group)
    engine.state_store.save(state)
    client.positions = [_short_call_position(group)]

    result = engine.manage(live=True)

    assert any(action["action"] == "close_group" for action in result["actions"])
    spot_orders = [order for order in client.placed_orders if order["instrument_name"] == "BTC_USDC"]
    assert len(spot_orders) == 1
    assert spot_orders[0]["direction"] == "sell"
    assert spot_orders[0]["order_type"] == "market"
    saved = engine.state_store.load().groups[0]
    assert saved.spot_exit_status == "filled"
    assert saved.spot_exit_order_id


def test_covered_call_robust_exit_does_not_sell_spot_when_close_incomplete(tmp_path):
    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        time_exit_dte=0,
        hard_defense_delta=Decimal("1"),
        soft_defense_delta=Decimal("1"),
        hard_defense_delta_call=Decimal("1"),
        soft_defense_delta_call=Decimal("1"),
        covered_call_spot_exit_enabled=True,
        covered_call_robust_exit_enabled=True,
        covered_call_robust_exit_dte=Decimal("1"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    group = _covered_call_group()
    state.groups.append(group)
    engine.state_store.save(state)
    client.positions = [_short_call_position(group)]
    client.order_scripts_by_label["trial-spread-btc-0001-short-close"] = [
        {"filled_amount": "0", "order_state": "filled"},
        {"filled_amount": "0", "order_state": "filled"},
    ]

    result = engine.manage(live=True)

    assert any(action["action"] == "close_group_incomplete" for action in result["actions"])
    assert not [order for order in client.placed_orders if order["instrument_name"] == "BTC_USDC"]


def test_covered_call_robust_exit_ignores_non_itm_call(tmp_path):
    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        time_exit_dte=0,
        hard_defense_delta=Decimal("1"),
        soft_defense_delta=Decimal("1"),
        hard_defense_delta_call=Decimal("1"),
        soft_defense_delta_call=Decimal("1"),
        covered_call_spot_exit_enabled=True,
        covered_call_robust_exit_enabled=True,
        covered_call_robust_exit_dte=Decimal("2"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    group = _covered_call_group(dte_days=1, strike=Decimal("77000"))
    state.groups.append(group)
    engine.state_store.save(state)
    client.positions = [_short_call_position(group)]

    result = engine.manage(live=False)

    assert result["actions"] == []


def test_covered_call_high_delta_skips_hard_derisk_and_cooldown(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        hard_defense_delta_call=Decimal("0.50"),
        hard_stop_loss_pct=Decimal("0.45"),
    )
    engine = DeribitOptionTrialBot(config, FakeClient(eth_book_equity="10"))
    group = _covered_call_group(dte_days=3)
    group.currency = "ETH"
    group.collateral_currency = "ETH"
    group.covered_underlying_quantity = Decimal("0.1")
    group.short_delta = Decimal("0.85")
    group.current_debit = Decimal("950")
    state = StrategyState()
    state.groups.append(group)
    engine.state_store.save(state)

    context = engine._load_runtime()

    assert context.snapshot.hard_derisk is False
    assert not any(
        "open_group_hard_defense_or_stop_trigger" in reason
        for reason in context.snapshot.halt_entry_reasons
    )
    result = engine.manage(live=True)
    assert not any(action.get("action") == "cooldown_started" for action in result["actions"])


def test_covered_call_otm_take_profit_when_capture_exceeds_threshold(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        tp_capture_pct=Decimal("0.55"),
        time_exit_dte=4,
        enable_early_exit=True,
        covered_call_spot_exit_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=14, strike=Decimal("77000"))
    group.profit_capture = Decimal("0.9")
    ctx = SimpleNamespace(
        orderbook_cache={
            group.short_instrument_name: _tight_book(
                group.short_instrument_name,
                bid="50",
                ask="51",
                index_price="70000",
            )
        }
    )

    with patch.object(
        engine,
        "_close_group",
        return_value=[{"action": "close_group_preview", "reason": "take_profit"}],
    ) as close_mock:
        actions = engine._manage_covered_call_group(ctx, group, live=False)

    close_mock.assert_called_once_with(ctx, group, reason="take_profit", live=False)
    assert actions[0]["reason"] == "take_profit"


def test_covered_call_itm_skips_take_profit_until_robust_exit(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        tp_capture_pct=Decimal("0.55"),
        time_exit_dte=4,
        enable_early_exit=True,
        covered_call_spot_exit_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=14, strike=Decimal("69000"))
    group.profit_capture = Decimal("0.9")
    ctx = SimpleNamespace(
        orderbook_cache={
            group.short_instrument_name: _tight_book(
                group.short_instrument_name,
                bid="50",
                ask="51",
            )
        }
    )

    with patch.object(engine, "_close_group") as close_mock:
        actions = engine._manage_covered_call_group(ctx, group, live=False)

    close_mock.assert_not_called()
    assert actions == []


def test_covered_call_otm_time_exit_near_expiry(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        tp_capture_pct=Decimal("0.95"),
        time_exit_dte=4,
        enable_early_exit=False,
        covered_call_spot_exit_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, FakeClient(btc_book_equity="0.5"))
    group = _covered_call_group(dte_days=3, strike=Decimal("77000"))
    group.profit_capture = Decimal("0.2")
    ctx = SimpleNamespace(
        orderbook_cache={
            group.short_instrument_name: _tight_book(
                group.short_instrument_name,
                bid="50",
                ask="51",
                index_price="70000",
            )
        }
    )

    with patch.object(
        engine,
        "_close_group",
        return_value=[{"action": "close_group_preview", "reason": "time_exit"}],
    ) as close_mock:
        actions = engine._manage_covered_call_group(ctx, group, live=False)

    close_mock.assert_called_once_with(ctx, group, reason="time_exit", live=False)
    assert actions[0]["reason"] == "time_exit"


def test_covered_call_collateralized_book_ignores_drawdown_derisk(tmp_path, fake_client):
    from datetime import UTC, datetime

    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    today_key = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    group = _covered_call_group(dte_days=10)
    group.currency = "ETH"
    group.collateral_currency = "ETH"
    group.covered_underlying_quantity = Decimal("1")
    state = StrategyState()
    state.day_key = today_key
    state.day_start_equity_by_book = {"BTC": Decimal("70000"), "ETH": Decimal("35000"), "USDC": Decimal("1000")}
    state.day_start_equity_native_by_book = {"BTC": Decimal("1"), "ETH": Decimal("10"), "USDC": Decimal("1000")}
    state.last_equity_by_book = dict(state.day_start_equity_by_book)
    state.last_equity_native_by_book = dict(state.day_start_equity_native_by_book)
    state.groups.append(group)
    engine.state_store.save(state)
    fake_client.eth_book_equity = "8"
    fake_client.btc_book_equity = "1"

    context = engine._load_runtime()

    assert context.snapshot.hard_derisk_by_book.get("ETH") is not True
    result = engine.manage(live=True)
    assert not any(action.get("action") == "cooldown_started" for action in result["actions"])
    assert "ETH" not in engine.state_store.load().cooldown_until_ms_by_book


def test_covered_call_settlement_exit_marks_pending_and_previews_spot_sell(tmp_path):
    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        time_exit_dte=0,
        covered_call_spot_exit_enabled=True,
        covered_call_robust_exit_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    group = _covered_call_group()
    state.groups.append(group)
    engine.state_store.save(state)

    result = engine.manage(live=False)

    assert result["actions"][0]["action"] == "covered_call_spot_exit_preview"
    saved = engine.state_store.load().groups[0]
    assert saved.status == "closed"
    assert saved.spot_exit_status == "pending"
    assert saved.spot_exit_instrument_name == "BTC_USDC"


def test_dry_run_bull_put_spread_entry_includes_long_leg(tmp_path, fake_client):
    config = make_config(
        tmp_path,
        option_strategy="bull_put_spread",
        option_markets_profile="linear_usdc",
        min_net_apr=Decimal("0.05"),
        linear_min_book_notional_usdc=Decimal("3000"),
        bull_put_long_delta_min=Decimal("0.04"),
        bull_put_long_delta_max=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.enter_best(currencies=("BTC",), live=False)

    assert result["action"] == "dry_run_enter_bull_put_spread"
    assert "long_leg" in result["requests"]
    assert "short_leg" in result["requests"]


def test_scan_status_has_portfolio_snapshot(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    status = engine.status()
    assert "portfolio" in status
    assert "regime" in status["portfolio"]


def test_position_tracks_official_floating_profit_loss_presence():
    with_pnl = Position.from_api(
        {
            "instrument_name": "BTC-29MAY26-70000-P",
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "mark_price": "0.0091",
            "average_price": "0.012",
            "floating_profit_loss": "0.00028877",
            "delta": "-0.1",
        }
    )
    without_pnl = Position.from_api(
        {
            "instrument_name": "BTC-29MAY26-70000-P",
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "mark_price": "0.0091",
            "average_price": "0.012",
            "delta": "-0.1",
        }
    )

    assert with_pnl.has_floating_profit_loss is True
    assert with_pnl.floating_profit_loss == Decimal("0.00028877")
    assert without_pnl.has_floating_profit_loss is False


def test_position_tracks_official_floating_profit_loss_usd_presence():
    with_usd = Position.from_api(
        {
            "instrument_name": "BTC-29MAY26-70000-P",
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "mark_price": "0.0091",
            "average_price": "0.012",
            "floating_profit_loss": "0.00028877",
            "floating_profit_loss_usd": "22.73",
            "delta": "-0.1",
        }
    )
    without_usd = Position.from_api(
        {
            "instrument_name": "BTC-29MAY26-70000-P",
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "mark_price": "0.0091",
            "average_price": "0.012",
            "floating_profit_loss": "0.00028877",
            "delta": "-0.1",
        }
    )

    assert with_usd.has_floating_profit_loss_usd is True
    assert with_usd.floating_profit_loss_usd == Decimal("22.73")
    assert without_usd.has_floating_profit_loss_usd is False


def test_group_payload_exposes_official_short_floating_profit_loss(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="all")
    engine = DeribitOptionTrialBot(config, fake_client)
    group = _build_group(
        short_instrument_name="BTC-29MAY26-70000-P",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
    )
    position = Position.from_api(
        {
            "instrument_name": group.short_instrument_name,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "0.0091",
            "average_price": "0.012",
            "floating_profit_loss": "0.00028877",
            "floating_profit_loss_usd": "22.73",
            "delta": "-0.1",
        }
    )

    payload = engine._group_payload(group, short_position=position, orderbook_cache=None)

    assert payload["short_has_floating_profit_loss"] is True
    assert payload["short_floating_profit_loss"] == "0.00028877"
    assert payload["short_has_floating_profit_loss_usd"] is True
    assert payload["short_floating_profit_loss_usd"] == "22.73"


def test_enter_best_dry_run_returns_preview_or_noop(tmp_path, fake_client):
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        min_net_apr=Decimal("0.05"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    result = engine.enter_best(live=False)
    assert result["action"] in {
        "dry_run_enter_naked_put",
        "no_candidate",
        "entry_skipped",
    }


def test_manage_no_open_groups_returns_empty_actions(tmp_path, fake_client):
    config = make_config(tmp_path)
    engine = DeribitOptionTrialBot(config, fake_client)
    result = engine.manage(live=False)
    assert result["action"] == "manage"
    assert result["actions"] == [] or all(
        a["action"] != "close_group" for a in result["actions"]
    )


def test_report_returns_naked_report_payload(tmp_path, fake_client):
    config = make_config(tmp_path)
    engine = DeribitOptionTrialBot(config, fake_client)
    report = engine.report()
    assert report["action"] == "report"
    assert "summary" in report
    assert "realized_pnl_usdc" in report["summary"]


def test_report_excludes_performance_exclusion_groups(tmp_path, fake_client):
    config = make_config(tmp_path)
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    included = _build_group(short_instrument_name="BTC-14APR30-63000-P")
    included.status = "closed"
    included.closed_timestamp_ms = included.entry_timestamp_ms + 86_400_000
    included.realized_pnl = Decimal("5")
    excluded = _build_group(short_instrument_name="BTC-14APR30-64000-P")
    excluded.group_id = "0002"
    excluded.status = "closed"
    excluded.closed_timestamp_ms = excluded.entry_timestamp_ms + 86_400_000
    excluded.realized_pnl = Decimal("99")
    state.groups.extend([included, excluded])
    engine.state_store.save(state)
    performance_exclusions_path(engine.state_store.path).write_text(
        '{"excluded_group_ids": ["0002"]}',
        encoding="utf-8",
    )

    report = engine.report()

    assert report["summary"]["closed_group_count"] == 1
    assert report["summary"]["performance_excluded_closed_group_count"] == 1
    assert report["summary"]["realized_closed_group_count"] == 1
    assert report["summary"]["realized_pnl_usdc"] == Decimal("5")
    assert [row["group_id"] for row in report["recent_closed_trades"]] == ["0001"]


def test_panic_close_dry_run_lists_positions(tmp_path, fake_client):
    config = make_config(tmp_path)
    engine = DeribitOptionTrialBot(config, fake_client)
    result = engine.panic_close(live=False)
    assert result["action"] == "panic_close"
    assert result["live"] is False
    assert "actions" in result


def test_run_survives_transient_exchange_error(tmp_path, fake_client):
    from deribit_demo.exceptions import TransientExchangeError

    config = make_config(tmp_path)
    engine = DeribitOptionTrialBot(config, fake_client)
    original_manage = engine.manage
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky_manage(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientExchangeError("public/get_instruments rate limited: HTTP 429")
        return original_manage(**kwargs)

    engine.manage = flaky_manage
    engine.sleep_fn = sleeps.append

    result = engine.run(live=False, cycles=2)

    assert result["cycles"] == 2
    assert len(sleeps) == 2
    assert sleeps[0] >= 60


def test_close_position_list_returns_open_positions(tmp_path, fake_client):
    instrument = "BTC_USDC-14APR30-63000-P"
    fake_client.positions = [
        {
            "instrument_name": instrument,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.11",
        }
    ]
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.close_positions(list_only=True, live=False)

    assert result["action"] == "close-position"
    assert result["list_only"] is True
    assert len(result["positions"]) == 1
    assert result["positions"][0]["instrument_name"] == instrument


def test_close_position_preview_skips_unknown_instrument(tmp_path, fake_client):
    fake_client.positions = []
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.close_positions(
        instruments=["BTC_USDC-14APR30-63000-P"],
        live=False,
    )

    assert result["targets"] == []
    assert result["skipped"] == [
        {"instrument_name": "BTC_USDC-14APR30-63000-P", "reason": "no_open_position"}
    ]


def test_close_position_preview_short_option(tmp_path, fake_client):
    instrument = "BTC_USDC-14APR30-63000-P"
    fake_client.positions = [
        {
            "instrument_name": instrument,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.11",
        }
    ]
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.close_positions(instruments=[instrument], live=False)

    assert len(result["targets"]) == 1
    target = result["targets"][0]
    assert target["status"] == "preview"
    assert target["close_side"] == "buy"
    assert target["method"] == "reduce_only_market"
    assert target["amount"] == "0.1"


def test_close_position_live_market_option(tmp_path, fake_client):
    instrument = "BTC_USDC-14APR30-63000-P"
    fake_client.positions = [
        {
            "instrument_name": instrument,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.11",
        }
    ]
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.close_positions(
        instruments=[instrument],
        live=True,
        order_type="market",
    )

    assert result["targets"][0]["status"] == "filled"
    assert len(fake_client.placed_orders) == 1
    order = fake_client.placed_orders[0]
    assert order["instrument_name"] == instrument
    assert order["direction"] == "buy"
    assert order["reduce_only"] is True
    assert order["order_type"] == "market"


def test_close_position_live_closes_perp(tmp_path, fake_client):
    instrument = "BTC-PERPETUAL"
    fake_client.positions = [
        {
            "instrument_name": instrument,
            "direction": "buy",
            "kind": "future",
            "size": "100",
            "size_currency": "100",
            "mark_price": "70000",
            "average_price": "69000",
            "floating_profit_loss": "0",
            "delta": "1",
        }
    ]
    config = make_config(tmp_path)
    engine = DeribitOptionTrialBot(config, fake_client)

    result = engine.close_positions(instruments=[instrument], live=True)

    assert result["targets"][0]["action"] == "close_perp"
    assert fake_client.closed_positions == [instrument]


def test_delta_totals_ignore_summary_delta_total(tmp_path, fake_client):
    """_delta_totals_by_currency must not pull values from summary.delta_total.

    If the account summary reports a huge cross-strategy hedge we want to stay
    off that signal entirely and only count our own tracked option legs + perps.
    """
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    state = StrategyState()
    group = _build_group(
        short_instrument_name="BTC_USDC-14APR30-63000-P",
        currency="BTC",
        quantity=Decimal("0.5"),
    )
    group.short_delta = Decimal("0.2")
    state.groups.append(group)

    summaries = {
        "BTC": AccountSummary(
            currency="BTC",
            balance=Decimal("1"),
            equity=Decimal("1"),
            available_funds=Decimal("1"),
            available_withdrawal_funds=Decimal("1"),
            initial_margin=Decimal("0"),
            maintenance_margin=Decimal("0"),
            delta_total=Decimal("999"),  # noise we must ignore
            options_delta=Decimal("0"),
            options_gamma=Decimal("0"),
            options_theta=Decimal("0"),
            total_equity_usd=Decimal("70000"),
            total_initial_margin_usd=Decimal("0"),
            total_maintenance_margin_usd=Decimal("0"),
        )
    }

    totals = engine._delta_totals_by_currency(summaries, state, future_positions=[])

    # group_delta = option_sign(+1 for put) * 0.2 * 0.5 = 0.1, no hedge
    assert totals["BTC"] == Decimal("0.1")


def test_delta_totals_add_perp_hedge(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    state = StrategyState()
    group = _build_group(
        short_instrument_name="BTC_USDC-14APR30-63000-P",
        currency="BTC",
        quantity=Decimal("1"),
    )
    group.short_delta = Decimal("0.30")
    state.groups.append(group)

    perp = Position(
        instrument_name=engine._perp_instrument("BTC"),
        direction="sell",
        kind="future",
        size=Decimal("0.4"),
        size_currency=Decimal("0.4"),
        mark_price=Decimal("70000"),
        average_price=Decimal("70000"),
        floating_profit_loss=Decimal("0"),
        delta=Decimal("0"),
    )

    totals = engine._delta_totals_by_currency({}, state, future_positions=[perp])

    # option: +0.30, hedge: -0.4, net: -0.1
    assert totals["BTC"] == Decimal("-0.1")


def test_regime_falls_back_to_elevated_when_drawdown_unavailable(tmp_path):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")

    class PartialFakeClient(FakeClient):
        def get_index_chart_data(self, index_name, *, range_name="1d"):
            raise RuntimeError("deribit down")

    engine = DeribitOptionTrialBot(config, PartialFakeClient())
    instruments = [
        __import__("deribit_demo.models", fromlist=["OptionInstrument"]).OptionInstrument.from_api(item)
        for item in engine.client.get_instruments("BTC", kind="option", expired=False)
    ]

    regime, detail = engine._determine_regime_with_detail(
        "BTC",
        markets=instruments,
        orderbook_cache={},
    )

    assert regime is RiskRegime.ELEVATED
    assert any("data_unavailable" in note for note in detail)


def test_regime_uses_cached_value_when_feeds_fail_after_success(tmp_path):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")

    calls = {"chart": 0, "dvol": 0}

    class FlakyFakeClient(FakeClient):
        def get_index_chart_data(self, index_name, *, range_name="1d"):
            calls["chart"] += 1
            if calls["chart"] > 1:
                raise RuntimeError("boom")
            return super().get_index_chart_data(index_name, range_name=range_name)

        def get_volatility_index_data(self, currency, *, start_timestamp, end_timestamp, resolution="1D"):
            calls["dvol"] += 1
            if calls["dvol"] > 1:
                raise RuntimeError("boom")
            return super().get_volatility_index_data(
                currency,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                resolution=resolution,
            )

    engine = DeribitOptionTrialBot(config, FlakyFakeClient())
    instruments = [
        __import__("deribit_demo.models", fromlist=["OptionInstrument"]).OptionInstrument.from_api(item)
        for item in engine.client.get_instruments("BTC", kind="option", expired=False)
    ]

    first_regime, first_detail = engine._determine_regime_with_detail(
        "BTC", markets=instruments, orderbook_cache={}
    )
    assert first_regime is RiskRegime.NORMAL

    second_regime, second_detail = engine._determine_regime_with_detail(
        "BTC", markets=instruments, orderbook_cache={}
    )
    # feeds now fail — must reuse cached value from the first call, not escalate to crisis.
    assert second_regime is RiskRegime.NORMAL
    assert any("cached" in note for note in second_detail)


def test_index_drawdown_returns_none_when_client_errors(tmp_path):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")

    class DeadFakeClient(FakeClient):
        def get_index_chart_data(self, index_name, *, range_name="1d"):
            raise RuntimeError("deribit down")

    engine = DeribitOptionTrialBot(config, DeadFakeClient())
    assert engine._index_drawdown_24h("BTC") is None


def test_dvol_ratio_returns_none_when_client_errors(tmp_path):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")

    class DeadFakeClient(FakeClient):
        def get_volatility_index_data(self, currency, *, start_timestamp, end_timestamp, resolution="1D"):
            raise RuntimeError("deribit down")

    engine = DeribitOptionTrialBot(config, DeadFakeClient())
    assert engine._dvol_ratio("BTC") is None


def _tight_book(
    instrument: str,
    *,
    bid: str,
    ask: str,
    index_price: str = "70000",
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        instrument_name=instrument,
        best_bid_price=Decimal(bid),
        best_bid_amount=Decimal("1"),
        best_ask_price=Decimal(ask),
        best_ask_amount=Decimal("1"),
        mark_price=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        index_price=Decimal(index_price),
        delta=Decimal("-0.05"),
        iv=Decimal("0.5"),
        open_interest=Decimal("100"),
    )


def _early_exit_group(short_instrument: str) -> TradeGroup:
    group = _build_group(
        short_instrument_name=short_instrument,
        entry_credit=Decimal("10"),
        max_loss=Decimal("10000"),
        dte_days=14,
    )
    group.current_debit = Decimal("5.1")  # remaining_credit = debit - close_fee = 5
    group.current_close_fee = Decimal("0.1")
    group.profit_capture = (group.entry_credit - group.current_debit) / group.entry_credit
    return group


def test_early_exit_triggers_when_remaining_apr_below_threshold(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    short = "BTC_USDC-14APR30-63000-P"
    group = _early_exit_group(short)
    # remaining_apr = 5 / 10000 * 365/14 ≈ 0.013, well below default 0.08
    ctx = SimpleNamespace(orderbook_cache={short: _tight_book(short, bid="50", ask="51")})
    assert engine._maybe_early_exit_reason(ctx, group) == "early_exit_low_apr"


def test_early_exit_skipped_when_spread_too_wide(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    short = "BTC_USDC-14APR30-63000-P"
    group = _early_exit_group(short)
    # spread_ratio = 10/55 ≈ 0.18 > default 0.05 → skip
    ctx = SimpleNamespace(orderbook_cache={short: _tight_book(short, bid="50", ask="60")})
    assert engine._maybe_early_exit_reason(ctx, group) is None


def test_early_exit_skipped_when_profit_capture_too_low(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    short = "BTC_USDC-14APR30-63000-P"
    group = _early_exit_group(short)
    group.current_debit = Decimal("9")
    group.current_close_fee = Decimal("0.1")
    group.profit_capture = (group.entry_credit - group.current_debit) / group.entry_credit  # 0.1 < 0.25
    ctx = SimpleNamespace(orderbook_cache={short: _tight_book(short, bid="50", ask="51")})
    assert engine._maybe_early_exit_reason(ctx, group) is None


def test_early_exit_respects_enable_flag(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc", enable_early_exit=False)
    engine = DeribitOptionTrialBot(config, fake_client)
    short = "BTC_USDC-14APR30-63000-P"
    group = _early_exit_group(short)
    ctx = SimpleNamespace(orderbook_cache={short: _tight_book(short, bid="50", ask="51")})
    assert engine._maybe_early_exit_reason(ctx, group) is None


def test_refresh_group_fetches_profile_filtered_linear_metadata(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="inverse_native")
    engine = DeribitOptionTrialBot(config, fake_client)
    markets = engine._load_supported_option_markets()
    short = "BTC_USDC-14APR30-63000-P"
    assert not any(m.instrument_name == short for values in markets.values() for m in values)

    group = _build_group(short_instrument_name=short, quantity=Decimal("0.1"))

    engine._refresh_group(context_markets=markets, group=group, orderbook_cache={})

    assert group.current_debit > 0
    assert group.current_close_fee > 0
    assert group.short_delta == Decimal("0.11")


def test_close_group_preview_fetches_profile_filtered_spread_metadata(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="inverse_native")
    engine = DeribitOptionTrialBot(config, fake_client)
    markets = engine._load_supported_option_markets()
    short = "BTC_USDC-14APR30-63000-P"
    long = "BTC_USDC-14APR30-60000-P"
    assert not any(m.instrument_name in {short, long} for values in markets.values() for m in values)

    group = _build_group(short_instrument_name=short, quantity=Decimal("0.1"))
    group.long_instrument_name = long
    ctx = SimpleNamespace(markets_by_currency=markets, orderbook_cache={})

    actions = engine._close_group(ctx, group, reason="take_profit", live=False)

    requests = actions[0]["requests"]
    assert requests["short_leg"]["instrument_name"] == short
    assert requests["long_leg"]["instrument_name"] == long


def test_metadata_lookup_uses_exact_instrument_endpoint(tmp_path):
    short = "BTC_USDC-29MAY26-68000-P"

    class ExactOnlyClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.instrument_calls: list[str] = []
            self.list_calls: list[tuple[str, str, bool]] = []

        def get_instrument(self, instrument_name):
            self.instrument_calls.append(instrument_name)
            if instrument_name != short:
                raise KeyError(instrument_name)
            return {
                "instrument_name": short,
                "base_currency": "BTC",
                "quote_currency": "USDC",
                "settlement_currency": "USDC",
                "instrument_type": "linear",
                "tick_size": "5",
                "tick_size_steps": [],
                "min_trade_amount": "0.01",
                "contract_size": "1",
                "option_type": "put",
                "expiration_timestamp": 1780041600000,
                "strike": "68000",
                "instrument_state": "open",
            }

        def get_instruments(self, currency, *, kind="option", expired=False):
            self.list_calls.append((currency, kind, expired))
            return []

    client = ExactOnlyClient()
    engine = DeribitOptionTrialBot(make_config(tmp_path), client)

    instrument = engine._find_or_fetch_instrument({}, short)

    assert instrument.instrument_name == short
    assert instrument.quote_currency == "USDC"
    assert instrument.settlement_currency == "USDC"
    assert instrument.strike == Decimal("68000")
    assert client.instrument_calls == [short]
    assert client.list_calls == []


def test_short_option_open_size_accepts_deribit_negative_sell_size():
    p = Position.from_api(
        {
            "instrument_name": "BTC_USDC-14APR30-63000-P",
            "direction": "sell",
            "kind": "option",
            "size": "-0.1",
            "size_currency": "-0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.1",
        }
    )
    assert DeribitOptionTrialBot._short_option_open_size(p) == Decimal("0.1")


def test_reconcile_adopts_exchange_short_put_missing_from_state(tmp_path, fake_client):
    """Exchange has a naked short put but state has no group → create TradeGroup so manage can run."""
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    short = "BTC_USDC-14APR30-63000-P"
    pos = Position.from_api(
        {
            "instrument_name": short,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.1",
        }
    )
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=[pos],
        orderbook_cache={},
        markets_by_currency=markets,
    )
    open_groups = [g for g in state.groups if g.status == "open"]
    assert len(open_groups) == 1
    assert open_groups[0].short_instrument_name == short
    assert open_groups[0].last_action == "adopted_from_exchange"
    assert open_groups[0].quantity == Decimal("0.1")


def test_reconcile_defers_external_close_for_recent_open_group(tmp_path, fake_client):
    short = "ETH_USDC-29MAY26-2350-C"
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    group = _build_group(short_instrument_name=short, currency="ETH", collateral_currency="USDC")
    group.entry_timestamp_ms = utc_now_ms()
    group.status = "open"
    state.groups.append(group)
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=[],
        orderbook_cache={},
        markets_by_currency=markets,
    )
    assert group.status == "open"


def test_is_phantom_reconcile_close_when_same_leg_still_open():
    short = "ETH_USDC-29MAY26-2350-C"
    phantom = _build_group(short_instrument_name=short, currency="ETH", collateral_currency="USDC")
    phantom.status = "closed"
    phantom.close_reason = "reconciled_external"
    phantom.closed_timestamp_ms = phantom.entry_timestamp_ms + 3_000
    phantom.realized_pnl = Decimal("-1")
    live = _build_group(short_instrument_name=short, currency="ETH", collateral_currency="USDC")
    live.group_id = "0002"
    live.status = "open"
    open_names = {g.short_instrument_name for g in [phantom, live] if g.status != "closed"}
    assert is_phantom_reconcile_close(phantom, open_short_names=open_names)
    assert not is_phantom_reconcile_close(phantom, open_short_names=set())


def test_reconcile_adopts_short_call_via_exact_instrument_when_absent_from_bulk(tmp_path):
    """Bulk get_instruments can omit names the account still holds; adopt must use get_instrument like refresh."""
    short = "BTC-22MAY26-85000-C"
    exp_ms = future_expiry(11)

    class BulkSparseClient(FakeClient):
        def get_instruments(self, currency, *, kind="option", expired=False):
            if currency.upper() == "BTC" and kind == "option":
                return []
            return super().get_instruments(currency, kind=kind, expired=expired)

        def get_instrument(self, instrument_name):
            if instrument_name == short:
                return {
                    "instrument_name": short,
                    "base_currency": "BTC",
                    "quote_currency": "BTC",
                    "settlement_currency": "BTC",
                    "instrument_type": "reversed",
                    "tick_size": "0.0001",
                    "tick_size_steps": [],
                    "min_trade_amount": "0.1",
                    "contract_size": "0.1",
                    "option_type": "call",
                    "expiration_timestamp": exp_ms,
                    "strike": "85000",
                    "instrument_state": "open",
                }
            return super().get_instrument(instrument_name)

    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        option_markets_profile="inverse_native",
        enable_short_put=False,
        enable_short_call=True,
    )
    engine = DeribitOptionTrialBot(config, BulkSparseClient())
    state = StrategyState()
    pos = Position.from_api(
        {
            "instrument_name": short,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "0.0033",
            "average_price": "0.003",
            "floating_profit_loss": "0",
            "delta": "-0.11",
        }
    )
    markets = engine._load_supported_option_markets()
    assert all(inst.instrument_name != short for inst in markets.get("BTC", []))
    engine._reconcile_state(
        state,
        option_positions=[pos],
        orderbook_cache={},
        markets_by_currency=markets,
    )
    open_groups = [g for g in state.groups if g.status == "open"]
    assert len(open_groups) == 1
    assert open_groups[0].short_instrument_name == short
    assert open_groups[0].last_action == "adopted_from_exchange"


def test_reconcile_promotes_existing_bull_put_spread_group_from_long_position(tmp_path, fake_client):
    config = make_config(
        tmp_path,
        option_strategy="bull_put_spread",
        option_markets_profile="linear_usdc",
        order_label_prefix="bull_put_spread",
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    short = "BTC_USDC-14APR30-63000-P"
    long = "BTC_USDC-14APR30-60000-P"
    group = _build_group(short_instrument_name=short, quantity=Decimal("0.1"))
    group.strategy = "naked_short"
    group.short_label = "bull_put_spread-spread-btc-0001-short"
    state.groups.append(group)
    positions = [
        Position.from_api(
            {
                "instrument_name": short,
                "direction": "sell",
                "kind": "option",
                "size": "0.1",
                "size_currency": "0.1",
                "mark_price": "610",
                "average_price": "600",
                "floating_profit_loss": "0",
                "delta": "-0.1",
            }
        ),
        Position.from_api(
            {
                "instrument_name": long,
                "direction": "buy",
                "kind": "option",
                "size": "0.1",
                "size_currency": "0.1",
                "mark_price": "195",
                "average_price": "195",
                "floating_profit_loss": "0",
                "delta": "-0.05",
            }
        ),
    ]
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=positions,
        orderbook_cache={},
        markets_by_currency=markets,
    )

    open_groups = [g for g in state.groups if g.status == "open"]
    assert len(open_groups) == 1
    assert open_groups[0].strategy == "bull_put_spread"
    assert open_groups[0].long_instrument_name == long
    assert open_groups[0].long_strike == Decimal("60000")
    assert open_groups[0].entry_credit == Decimal("36.3000")
    assert open_groups[0].max_loss == Decimal("263.7000")


def test_reconcile_adopts_untracked_bull_put_spread_with_long_leg(tmp_path, fake_client):
    config = make_config(
        tmp_path,
        option_strategy="bull_put_spread",
        option_markets_profile="linear_usdc",
        order_label_prefix="bull_put_spread",
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    short = "BTC_USDC-14APR30-63000-P"
    long = "BTC_USDC-14APR30-60000-P"
    positions = [
        Position.from_api(
            {
                "instrument_name": short,
                "direction": "sell",
                "kind": "option",
                "size": "0.1",
                "size_currency": "0.1",
                "mark_price": "610",
                "average_price": "600",
                "floating_profit_loss": "0",
                "delta": "-0.1",
            }
        ),
        Position.from_api(
            {
                "instrument_name": long,
                "direction": "buy",
                "kind": "option",
                "size": "0.1",
                "size_currency": "0.1",
                "mark_price": "195",
                "average_price": "195",
                "floating_profit_loss": "0",
                "delta": "-0.05",
            }
        ),
    ]
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=positions,
        orderbook_cache={},
        markets_by_currency=markets,
    )

    open_groups = [g for g in state.groups if g.status == "open"]
    assert len(open_groups) == 1
    assert open_groups[0].strategy == "bull_put_spread"
    assert open_groups[0].short_instrument_name == short
    assert open_groups[0].long_instrument_name == long
    assert open_groups[0].long_label == "bull_put_spread-spread-btc-0001-long"


def test_reconcile_adopts_sell_short_with_negative_size(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    short = "BTC_USDC-14APR30-63000-P"
    pos = Position.from_api(
        {
            "instrument_name": short,
            "direction": "sell",
            "kind": "option",
            "size": "-0.1",
            "size_currency": "-0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.1",
        }
    )
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=[pos],
        orderbook_cache={},
        markets_by_currency=markets,
    )
    open_groups = [g for g in state.groups if g.status == "open"]
    assert len(open_groups) == 1
    assert open_groups[0].quantity == Decimal("0.1")


def test_reconcile_does_not_duplicate_existing_open_group(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    short = "BTC_USDC-14APR30-63000-P"
    state = StrategyState()
    state.groups.append(_build_group(short_instrument_name=short, quantity=Decimal("0.1")))
    pos = Position.from_api(
        {
            "instrument_name": short,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.1",
        }
    )
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=[pos],
        orderbook_cache={},
        markets_by_currency=markets,
    )
    open_groups = [g for g in state.groups if g.status == "open"]
    assert len(open_groups) == 1


def test_reconcile_skips_adoption_when_disabled(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc", enable_adopt_exchange_positions=False)
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    short = "BTC_USDC-14APR30-63000-P"
    pos = Position.from_api(
        {
            "instrument_name": short,
            "direction": "sell",
            "kind": "option",
            "size": "0.1",
            "size_currency": "0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.1",
        }
    )
    markets = engine._load_supported_option_markets()
    engine._reconcile_state(
        state,
        option_positions=[pos],
        orderbook_cache={},
        markets_by_currency=markets,
    )
    assert [g for g in state.groups if g.status == "open"] == []


def _make_summary(currency: str, *, equity: str, initial_margin: str, maintenance_margin: str) -> AccountSummary:
    """Minimal ``AccountSummary`` factory for portfolio-snapshot tests."""
    return AccountSummary(
        currency=currency,
        balance=Decimal(equity),
        equity=Decimal(equity),
        available_funds=Decimal(equity),
        available_withdrawal_funds=Decimal(equity),
        initial_margin=Decimal(initial_margin),
        maintenance_margin=Decimal(maintenance_margin),
        delta_total=Decimal("0"),
        options_delta=Decimal("0"),
        options_gamma=Decimal("0"),
        options_theta=Decimal("0"),
        total_equity_usd=Decimal("0"),
        total_initial_margin_usd=Decimal("0"),
        total_maintenance_margin_usd=Decimal("0"),
    )


def test_portfolio_snapshot_hard_derisks_when_book_im_exceeds_hard_cap(tmp_path, fake_client):
    """When any book breaches ``book_im_hard`` the portfolio must hard-derisk.

    The three-book redesign uses per-book ``BOOK_IM_HARD`` / ``BOOK_MM_HARD``
    instead of a single portfolio margin gate. This test locks that in: a USDC book running
    at 50% IM utilization against ``book_im_hard=0.45`` should force
    ``hard_derisk=True`` and surface a per-book reason in ``halt_entry_reasons``.
    """
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        book_im_target=Decimal("0.35"),
        book_im_hard=Decimal("0.45"),
        book_mm_target=Decimal("0.22"),
        book_mm_hard=Decimal("0.33"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    summaries = {
        "USDC": _make_summary(
            "USDC", equity="1000", initial_margin="500", maintenance_margin="100"
        ),
        "BTC": _make_summary("BTC", equity="1", initial_margin="0", maintenance_margin="0.01"),
        "ETH": _make_summary("ETH", equity="10", initial_margin="0", maintenance_margin="0.01"),
    }
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={"BTC": ("market_conditions_normal",), "ETH": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )
    assert snapshot.hard_derisk is True
    assert snapshot.halt_new_entries is True
    assert any("USDC" in reason and "book_im_hard" in reason for reason in snapshot.halt_entry_reasons)


def test_portfolio_snapshot_stays_green_below_book_caps(tmp_path, fake_client):
    """Below both target and hard caps the snapshot must not flip any gate.

    Complements the hard-derisk test: validates that a healthy three-book state
    (IM well under 35%, MM well under 22%) leaves ``hard_derisk`` and
    ``halt_new_entries`` clear, so a future regression that makes the book-caps
    fire unconditionally breaks here.
    """
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        book_im_target=Decimal("0.35"),
        book_im_hard=Decimal("0.45"),
        book_mm_target=Decimal("0.22"),
        book_mm_hard=Decimal("0.33"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    summaries = {
        "USDC": _make_summary(
            "USDC", equity="1000", initial_margin="100", maintenance_margin="40"
        ),
        "BTC": _make_summary("BTC", equity="1", initial_margin="0", maintenance_margin="0.01"),
        "ETH": _make_summary("ETH", equity="10", initial_margin="0", maintenance_margin="0.01"),
    }
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={"BTC": ("market_conditions_normal",), "ETH": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )
    assert snapshot.hard_derisk is False
    assert snapshot.halt_new_entries is False


def test_portfolio_snapshot_isolates_drawdown_per_book(tmp_path, fake_client):
    """A drop in the ETH book must not trigger hard_derisk on BTC/USDC books.

    Reproduces the 2026-04-20 scenario: the operator withdrew ETH from the
    ETH-collateral book, dropping its equity by 20%. Under the previous
    aggregate-equity model the whole portfolio tripped ``halt_drawdown_pct``
    and ``hard_derisk_drawdown_pct``. With segregated books only the ETH
    book should halt, leaving BTC and USDC books free to keep trading.
    """
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    # Seed day_start so drawdown math has a reference point. ETH book is down
    # 20% from its day-start valuation; BTC and USDC books are flat.
    state.day_start_equity_by_book = {
        "BTC": Decimal("70000"),
        "ETH": Decimal("35000"),
        "USDC": Decimal("1000"),
    }
    state.day_start_equity_native_by_book = {
        "BTC": Decimal("1"),
        "ETH": Decimal("10"),
        "USDC": Decimal("1000"),
    }
    summaries = {
        "BTC": _make_summary("BTC", equity="1", initial_margin="0", maintenance_margin="0.01"),
        "ETH": _make_summary("ETH", equity="8", initial_margin="0", maintenance_margin="0.01"),  # was 10 ETH
        "USDC": _make_summary("USDC", equity="1000", initial_margin="50", maintenance_margin="10"),
    }
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={"BTC": ("market_conditions_normal",), "ETH": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )

    assert snapshot.hard_derisk_by_book["ETH"] is True
    assert snapshot.halt_entries_by_book["ETH"] is True
    assert snapshot.hard_derisk_by_book["BTC"] is False
    assert snapshot.halt_entries_by_book["BTC"] is False
    assert snapshot.hard_derisk_by_book["USDC"] is False
    assert snapshot.halt_entries_by_book["USDC"] is False
    # Aggregate hard_derisk stays True because any breach escalates, but
    # ``halt_new_entries`` is also True only because some book is halted —
    # the scan path checks ``halt_entries_by_book`` to keep other books live.
    assert snapshot.hard_derisk is True
    assert "ETH" in snapshot.day_drawdown_pct_by_book
    assert snapshot.day_drawdown_pct_by_book["ETH"] >= Decimal("0.06")
    assert snapshot.day_drawdown_pct_by_book["BTC"] == Decimal("0")
    assert snapshot.day_drawdown_pct_by_book["USDC"] == Decimal("0")


def test_inverse_book_drawdown_uses_native_equity_not_usdc_mark(tmp_path, fake_client):
    """A coin price drop alone must not halt an inverse-native covered-call book."""
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        scan_underlyings=("ETH",),
        traded_collaterals=("ETH",),
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    # USDC-equivalent book value is down >2.5%, but the ETH balance is flat.
    state.day_start_equity_by_book = {"ETH": Decimal("3679.7376123")}
    state.day_start_equity_native_by_book = {"ETH": Decimal("1")}
    summaries = {
        "BTC": _make_summary("BTC", equity="0", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="1", initial_margin="0", maintenance_margin="0.01"),
        "USDC": _make_summary("USDC", equity="0", initial_margin="0", maintenance_margin="0"),
    }

    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={"ETH": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )

    assert snapshot.day_drawdown_pct_by_book["ETH"] == Decimal("0")
    assert snapshot.day_pnl_usdc_ex_flow_by_book["ETH"] == Decimal("-179.7376123")
    assert snapshot.day_pnl_usdc_ex_flow_ex_spot_by_book["ETH"] == Decimal("0")
    assert snapshot.day_pnl_usdc_ex_flow_ex_spot == Decimal("0")
    assert snapshot.halt_entries_by_book["ETH"] is False
    assert snapshot.halt_new_entries is False


def test_manage_writes_cooldown_only_to_triggering_book(tmp_path, fake_client):
    """``manage`` must stamp cooldown on the specific book that tripped hard_derisk."""
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    # Pre-populate state so drawdown computation sees the ETH book down.
    # day_key must match today's UTC key so ``_reset_daily_state`` doesn't
    # stomp ``day_start_equity_by_book`` during ``_load_runtime``.
    from datetime import UTC, datetime
    today_key = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    store = engine.state_store
    state = StrategyState()
    state.day_key = today_key
    state.day_start_equity_by_book = {
        "BTC": Decimal("70000"),
        "ETH": Decimal("35000"),
        "USDC": Decimal("1000"),
    }
    state.day_start_equity_native_by_book = {
        "BTC": Decimal("1"),
        "ETH": Decimal("10"),
        "USDC": Decimal("1000"),
    }
    state.last_equity_by_book = dict(state.day_start_equity_by_book)
    state.last_equity_native_by_book = dict(state.day_start_equity_native_by_book)
    store.save(state)
    # Make the fake client report the withdrawn ETH book so the snapshot
    # picks up the drawdown.
    fake_client.eth_book_equity = "8"
    fake_client.btc_book_equity = "1"
    result = engine.manage(live=True)
    assert result["action"] == "manage"
    cooldowns = [a for a in result["actions"] if a.get("action") == "cooldown_started"]
    assert cooldowns, "expected a cooldown_started action"
    assert cooldowns[0]["books"] == ["ETH"]
    reloaded = store.load()
    assert "ETH" in reloaded.cooldown_until_ms_by_book
    # BTC and USDC books must not have been stamped with cooldowns.
    assert "BTC" not in reloaded.cooldown_until_ms_by_book
    assert "USDC" not in reloaded.cooldown_until_ms_by_book


def test_book_router_honors_traded_collaterals(tmp_path, fake_client):
    """Only pools in ``traded_collaterals`` should become real books.

    With ``traded_collaterals=("USDC",)`` a USDC-only deployment must not
    construct BTC or ETH inverse books, so dust in those sub-accounts can't
    leak into drawdown calculations.
    """
    from deribit_demo.book import BookRouter

    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        scan_underlyings=("BTC", "ETH"),
        traded_collaterals=("USDC",),
    )
    summaries = {
        "BTC": _make_summary("BTC", equity="0.00001", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="0.00001", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="6300", initial_margin="500", maintenance_margin="100"),
    }
    router = BookRouter.from_summaries(config, summaries)
    collaterals = {book.collateral for book in router.books}

    assert collaterals == {"USDC"}
    assert not any(book.inverse for book in router.books)


def test_book_router_skips_usdc_when_not_traded(tmp_path, fake_client):
    """If USDC isn't whitelisted, no linear book is built even with BTC/ETH underlyings.

    Guards against the inverse ambiguity: if the user wants a pure
    inverse-only setup (``traded_collaterals=(BTC, ETH)``), the BookRouter
    must *not* automatically invent a USDC book.
    """
    from deribit_demo.book import BookRouter

    config = make_config(
        tmp_path,
        scan_underlyings=("BTC", "ETH"),
        traded_collaterals=("BTC", "ETH"),
    )
    summaries = {
        "BTC": _make_summary("BTC", equity="1", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="10", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="1000", initial_margin="0", maintenance_margin="0"),
    }
    router = BookRouter.from_summaries(config, summaries)
    collaterals = {book.collateral for book in router.books}

    assert collaterals == {"BTC", "ETH"}


def test_drawdown_ignores_dust_below_floor(tmp_path, fake_client):
    """Books whose day-start equity is below ``min_book_equity_usdc`` must
    not generate a drawdown entry even if their balance swings violently.

    Reproduces the 2026-04-20 phantom-drawdown pattern: a leftover $0.51 of
    BTC dust in an otherwise USDC-only account. Without the dust floor the
    BTC book would appear to be deeply underwater every time that dust
    fluctuates, tripping ``halt_drawdown_pct``.
    """
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
        min_book_equity_usdc=Decimal("50"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    state.day_start_equity_by_book = {
        "BTC": Decimal("0.51"),  # dust: below 50 USDC floor
        "USDC": Decimal("6300"),
    }
    state.day_start_equity_native_by_book = {
        "BTC": Decimal("0.00001"),
        "USDC": Decimal("6300"),
    }
    summaries = {
        "BTC": _make_summary("BTC", equity="0", initial_margin="0", maintenance_margin="0"),  # dust gone
        "ETH": _make_summary("ETH", equity="0", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="6295", initial_margin="100", maintenance_margin="30"),
    }
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={
            "BTC": ("market_conditions_normal",),
            "ETH": ("market_conditions_normal",),
        },
        future_positions=[],
        orderbook_cache={},
    )

    assert "BTC" not in snapshot.day_drawdown_pct_by_book
    assert snapshot.hard_derisk_by_book.get("BTC") is False
    assert snapshot.halt_entries_by_book.get("BTC") is False
    assert snapshot.hard_derisk is False
    assert snapshot.halt_new_entries is False


def test_drawdown_corrects_withdrawal_via_net_flow(tmp_path, fake_client):
    """A user withdrawal must not masquerade as a trading loss.

    Seeds ``day_net_flow_usdc_by_book`` with a -1000 USDC withdrawal and a
    post-withdrawal equity matching that outflow (with a tiny 10 USDC real
    loss). The corrected drawdown must be about 10 / 6300 ≈ 0.16% rather
    than the naive 1010 / 6300 ≈ 16% that the old formula produced.
    """
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    state.day_start_equity_by_book = {"USDC": Decimal("6300")}
    # Simulated transaction-log outcome: -1000 USDC net (withdrawal).
    state.day_net_flow_usdc_by_book = {"USDC": Decimal("-1000")}
    summaries = {
        "BTC": _make_summary("BTC", equity="0", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="0", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="5290", initial_margin="100", maintenance_margin="30"),
    }
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={
            "BTC": ("market_conditions_normal",),
            "ETH": ("market_conditions_normal",),
        },
        future_positions=[],
        orderbook_cache={},
    )

    dd = snapshot.day_drawdown_pct_by_book["USDC"]
    assert dd < Decimal("0.005"), f"expected <0.5%, got {dd}"
    assert snapshot.hard_derisk_by_book["USDC"] is False
    assert snapshot.halt_entries_by_book["USDC"] is False


def test_drawdown_catches_loss_masked_by_deposit(tmp_path, fake_client):
    """A deposit must not *hide* a real trading loss.

    Mirror of the withdrawal test: equity rose from 6,300 to 6,500 but the
    user also deposited 500, so the trading P&L is -300. The snapshot must
    register that as a 300/6300 ≈ 4.76% drawdown rather than clamping to
    zero just because equity looks higher than day-start.
    """
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
    )
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    state.day_start_equity_by_book = {"USDC": Decimal("6300")}
    state.day_net_flow_usdc_by_book = {"USDC": Decimal("500")}
    summaries = {
        "BTC": _make_summary("BTC", equity="0", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="0", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="6500", initial_margin="100", maintenance_margin="30"),
    }
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={
            "BTC": ("market_conditions_normal",),
            "ETH": ("market_conditions_normal",),
        },
        future_positions=[],
        orderbook_cache={},
    )

    dd = snapshot.day_drawdown_pct_by_book["USDC"]
    # 300 / 6300 = 0.04761...
    assert Decimal("0.047") < dd < Decimal("0.05"), f"expected ~4.76%, got {dd}"
    # Crosses halt threshold (0.025) but not hard-derisk (0.06).
    assert snapshot.halt_entries_by_book["USDC"] is True
    assert snapshot.hard_derisk_by_book["USDC"] is False


def test_first_run_midday_deposit_does_not_double_count_flow(tmp_path, fake_client):
    """First bot run mid-day must not re-count today's deposit in day_net_flow."""
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        scan_underlyings=("BTC",),
        traded_collaterals=("BTC",),
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
        cash_flow_query_interval_seconds=0,
    )
    deposit_ts = utc_now_ms() - 3_600_000
    fake_client.transaction_log = {
        "BTC": [{"type": "deposit", "change": "0.1", "timestamp": deposit_ts}],
    }
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    summaries = {
        "BTC": _make_summary("BTC", equity="0.1", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="0", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="0", initial_margin="0", maintenance_margin="0"),
    }

    state = engine._reset_daily_state(state, summaries)
    engine._refresh_cash_flows_by_book(state, {})
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        regime_detail_by_currency={"BTC": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )

    assert state.day_net_flow_native_by_book.get("BTC", Decimal("0")) == Decimal("0")
    assert snapshot.day_pnl_usdc_ex_flow_ex_spot == Decimal("0")
    assert snapshot.day_drawdown_pct == Decimal("0")
    assert snapshot.halt_new_entries is False
    assert snapshot.hard_derisk is False


def test_deposit_after_equity_anchor_still_counts_in_flow(tmp_path, fake_client):
    """Deposits after day-start anchor must still appear in day_net_flow."""
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        scan_underlyings=("BTC",),
        traded_collaterals=("BTC",),
        cash_flow_query_interval_seconds=0,
    )
    anchor_ms = utc_now_ms() - 3_600_000
    deposit_ts = utc_now_ms() - 1_800_000
    fake_client.transaction_log = {
        "BTC": [{"type": "deposit", "change": "0.02", "timestamp": deposit_ts}],
    }
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    state.day_key = utc_now().strftime("%Y-%m-%d")
    state.day_start_equity_by_book = {"BTC": Decimal("7700")}
    state.day_start_equity_native_by_book = {"BTC": Decimal("0.1")}
    state.day_equity_anchor_ms_by_book = {"BTC": anchor_ms}
    summaries = {
        "BTC": _make_summary("BTC", equity="0.12", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="0", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="0", initial_margin="0", maintenance_margin="0"),
    }

    engine._refresh_cash_flows_by_book(state, {})
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        regime_detail_by_currency={"BTC": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )

    assert state.day_net_flow_native_by_book["BTC"] == Decimal("0.02")
    assert snapshot.day_pnl_usdc_ex_flow_ex_spot == Decimal("0")


def test_legacy_flow_double_count_healed_on_reset(tmp_path, fake_client):
    """Legacy state with duplicated deposit must self-heal on the next cycle."""
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        scan_underlyings=("BTC",),
        traded_collaterals=("BTC",),
        halt_drawdown_pct=Decimal("0.025"),
        hard_derisk_drawdown_pct=Decimal("0.06"),
        cash_flow_query_interval_seconds=0,
    )
    deposit_ts = utc_now_ms() - 3_600_000
    fake_client.transaction_log = {
        "BTC": [{"type": "deposit", "change": "0.1", "timestamp": deposit_ts}],
    }
    engine = DeribitOptionTrialBot(config, fake_client)
    state = StrategyState()
    state.day_key = utc_now().strftime("%Y-%m-%d")
    state.day_start_equity_by_book = {"BTC": Decimal("7724")}
    state.day_start_equity_native_by_book = {"BTC": Decimal("0.1")}
    state.day_net_flow_usdc_by_book = {"BTC": Decimal("7724")}
    state.day_net_flow_native_by_book = {"BTC": Decimal("0.1")}
    summaries = {
        "BTC": _make_summary("BTC", equity="0.1", initial_margin="0", maintenance_margin="0"),
        "ETH": _make_summary("ETH", equity="0", initial_margin="0", maintenance_margin="0"),
        "USDC": _make_summary("USDC", equity="0", initial_margin="0", maintenance_margin="0"),
    }

    state = engine._reset_daily_state(state, summaries)
    engine._refresh_cash_flows_by_book(state, {})
    snapshot = engine._build_portfolio_snapshot(
        state=state,
        summaries=summaries,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        regime_detail_by_currency={"BTC": ("market_conditions_normal",)},
        future_positions=[],
        orderbook_cache={},
    )

    assert state.day_net_flow_native_by_book["BTC"] == Decimal("0")
    assert "BTC" in state.day_equity_anchor_ms_by_book
    assert snapshot.day_drawdown_pct == Decimal("0")
    assert snapshot.halt_new_entries is False


def test_trade_group_round_trips_without_spread_fields(tmp_path):
    group = _build_group(short_instrument_name="BTC_USDC-14APR30-63000-P")
    payload = group.to_dict()
    assert "long_instrument_name" not in payload
    assert "long_strike" not in payload
    assert "roll_count" not in payload
    restored = TradeGroup.from_dict({**payload,
        "quantity": str(payload["quantity"]),
        "entry_timestamp_ms": payload["entry_timestamp_ms"],
        "expiration_timestamp_ms": payload["expiration_timestamp_ms"],
        "short_strike": str(payload["short_strike"]),
        "entry_credit": str(payload["entry_credit"]),
        "original_entry_credit": str(payload["original_entry_credit"]),
        "max_loss": str(payload["max_loss"]),
    })
    assert restored.group_id == group.group_id
    assert restored.short_instrument_name == group.short_instrument_name


def test_naked_im_by_expiry_keeps_inverse_and_usdc_units_separate(tmp_path, fake_client):
    """Inverse (BTC/ETH) and linear (USDC) IM must never be summed together.

    Regression: before ``estimated_im_collateral`` existed, the inverse branch
    of ``_naked_im_by_expiry`` blindly added ``group.max_loss`` (USDC-scale)
    to the BTC-equity capacity check, which made ``max_by_expiry`` collapse
    into a large negative number as soon as any inverse group was open.
    """
    config = make_config(tmp_path, option_markets_profile="all")
    engine = DeribitOptionTrialBot(config, fake_client)

    state = StrategyState()

    btc_group = _build_group(
        short_instrument_name="BTC-8MAY26-68000-P",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        max_loss=Decimal("800"),  # USDC-scale (legacy)
    )
    btc_group.estimated_im_collateral = Decimal("0.0109")  # BTC-native
    state.groups.append(btc_group)

    usdc_group = _build_group(
        short_instrument_name="BTC_USDC-8MAY26-72000-P",
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("1"),
        max_loss=Decimal("1500"),
    )
    usdc_group.estimated_im_collateral = Decimal("1500")  # USDC-native
    state.groups.append(usdc_group)

    exp = btc_group.expiration_timestamp_ms
    btc_by_exp = engine._naked_im_by_expiry(state, "BTC")
    usdc_by_exp = engine._naked_im_by_expiry(state, "USDC")

    assert btc_by_exp == {exp: Decimal("0.0109")}
    assert usdc_by_exp == {exp: Decimal("1500")}
    assert "USDC" not in str(btc_by_exp)  # sanity: no string contamination
    # A ``summary_equity * 0.3`` check at BTC equity ≈ 0.1 must now stay
    # positive instead of going deeply negative:
    btc_equity = Decimal("0.1")
    assert btc_equity * Decimal("0.3") - btc_by_exp[exp] > 0


def test_naked_im_by_expiry_fallback_uses_current_index_for_legacy_inverse(
    tmp_path, fake_client, monkeypatch
):
    """Legacy groups (no ``estimated_im_collateral``) must still aggregate
    in coin units via the current index price, not raw USDC ``max_loss``."""
    config = make_config(tmp_path, option_markets_profile="all")
    engine = DeribitOptionTrialBot(config, fake_client)

    state = StrategyState()
    legacy = _build_group(
        short_instrument_name="BTC-8MAY26-68000-P",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        max_loss=Decimal("800"),  # USDC-scale only, no estimated_im_collateral
    )
    # Ensure fallback path runs: estimated_im_collateral stays at 0.
    assert legacy.estimated_im_collateral == Decimal("0")
    state.groups.append(legacy)

    monkeypatch.setattr(
        engine,
        "_currency_index_price",
        lambda currency, orderbook_cache: Decimal("80000") if currency == "BTC" else Decimal("0"),
    )
    by_exp = engine._naked_im_by_expiry(state, "BTC", orderbook_cache={})
    # 800 USDC / 80_000 USDC-per-BTC = 0.01 BTC
    assert by_exp == {legacy.expiration_timestamp_ms: Decimal("0.01")}
