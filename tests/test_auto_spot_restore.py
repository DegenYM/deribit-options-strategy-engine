from decimal import Decimal
from unittest.mock import MagicMock

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.spot_restore_ops import (
    evaluate_auto_spot_restore,
    evaluate_auto_spot_restore_park,
    execute_spot_restore_for_group,
    spot_restore_order_label,
)


def _itm_sold_group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0095",
        "currency": "BTC",
        "short_instrument_name": "BTC-28AUG26-73000-C",
        "short_label": "cc-btc-0095",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "covered_underlying_quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "closed_timestamp_ms": 1_746_000_000_000,
        "short_strike": "73000",
        "entry_credit": "30",
        "original_entry_credit": "30",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "spot_exit_status": "filled",
        "spot_exit_amount": "0.1",
        "spot_exit_quote_proceeds": "9000",
        "spot_exit_quote_proceeds_lifetime": "9000",
        "spot_exit_reason": "covered_call_settlement_exit",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_evaluate_auto_spot_restore_favorable_when_ask_below_breakeven() -> None:
    group = _itm_sold_group()
    # unrestored 0.1, proceeds 9000 → breakeven 90000; 60k ask is cheap.
    decision = evaluate_auto_spot_restore(
        group,
        buy_price=Decimal("60000"),
        min_edge_pct=Decimal("0.001"),
    )
    assert decision["ok"] is True
    assert decision["reason"] == "favorable"
    assert Decimal(decision["estimated_net_usdt"]) > 0


def test_evaluate_auto_spot_restore_skips_when_estimated_net_not_positive() -> None:
    group = _itm_sold_group()
    # Edge=0 so ask is still below breakeven 90000, but 0.1% spend cushion
    # makes estimated USDT exceed remaining proceeds.
    decision = evaluate_auto_spot_restore(
        group,
        buy_price=Decimal("89950"),
        min_edge_pct=Decimal("0"),
    )
    assert decision["ok"] is False
    assert decision["reason"] == "price_not_favorable"
    assert Decimal(decision["estimated_net_usdt"]) <= 0


def test_evaluate_auto_spot_restore_skips_when_ask_above_breakeven() -> None:
    group = _itm_sold_group()
    decision = evaluate_auto_spot_restore(
        group,
        buy_price=Decimal("90000"),
        min_edge_pct=Decimal("0.001"),
    )
    assert decision["ok"] is False
    assert decision["reason"] == "price_not_favorable"


def test_evaluate_auto_spot_restore_skips_operator_skipped_exit() -> None:
    group = _itm_sold_group(
        spot_exit_status="skipped",
        spot_exit_amount="0",
        spot_exit_quote_proceeds="0",
        spot_exit_quote_proceeds_lifetime="0",
    )
    decision = evaluate_auto_spot_restore(group, buy_price=Decimal("10000"))
    assert decision["ok"] is False
    assert decision["reason"] == "spot_exit_skipped"


def test_evaluate_auto_spot_restore_skips_pending_exit() -> None:
    group = _itm_sold_group(spot_exit_status="pending")
    decision = evaluate_auto_spot_restore(group, buy_price=Decimal("10000"))
    assert decision["ok"] is False
    assert decision["reason"] == "spot_exit_not_filled"


def test_evaluate_auto_spot_restore_skips_already_restored() -> None:
    group = _itm_sold_group(
        spot_restore_status="filled",
        spot_restore_amount="0.1",
        spot_restore_quote_spent="5000",
    )
    decision = evaluate_auto_spot_restore(group, buy_price=Decimal("10000"))
    assert decision["ok"] is False
    assert decision["reason"] == "already_restored"


def test_evaluate_auto_spot_restore_credits_child_assignment() -> None:
    parent = _itm_sold_group(
        cash_secured_group_id="0026",
        cash_secured_group_ids=["0026"],
    )
    child = TradeGroup.from_dict(
        {
            "group_id": "0026",
            "currency": "BTC",
            "status": "closed",
            "strategy": "cash_secured",
            "option_type": "put",
            "collateral_currency": "USDC",
            "quantity": "0.1",
            "short_instrument_name": "BTC_USDC-11SEP26-73000-P",
            "short_strike": "73000",
            "cash_secured_from_group_id": "0095",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "entry_credit": "5",
            "max_loss": "7300",
            "regime_at_entry": "normal",
            "spot_restore_status": "filled",
            "spot_restore_amount": "0.1",
            "spot_restore_quote_spent": "7100",
        }
    )
    alone = evaluate_auto_spot_restore(parent, buy_price=Decimal("60000"))
    assert alone["ok"] is True
    decision = evaluate_auto_spot_restore(parent, buy_price=Decimal("60000"), groups=[parent, child])
    assert decision["ok"] is False
    assert decision["reason"] in {"already_restored", "nothing_to_restore"}


def test_evaluate_auto_spot_restore_partial_uses_remaining_proceeds() -> None:
    group = _itm_sold_group(
        spot_restore_status="filled",
        spot_restore_amount="0.04",
        spot_restore_quote_spent="2800",
        spot_restore_quote_spent_lifetime="2800",
    )
    # Remaining 0.06, remaining proceeds 6200 → breakeven ~103333.
    cheap = evaluate_auto_spot_restore(group, buy_price=Decimal("70000"))
    assert cheap["ok"] is True
    expensive = evaluate_auto_spot_restore(group, buy_price=Decimal("120000"))
    assert expensive["ok"] is False
    assert expensive["reason"] == "price_not_favorable"


def test_manage_auto_restore_off_does_not_preview(tmp_path) -> None:
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=False,
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.manage(live=False)
    assert not any(
        str(action.get("action") or "").startswith("spot_restore")
        or str(action.get("action") or "").startswith("auto_spot_restore")
        for action in result["actions"]
    )


def test_evaluate_auto_spot_restore_park_ready_when_exit_filled() -> None:
    group = _itm_sold_group(
        spot_exit_quote_proceeds="5000",
        spot_exit_quote_proceeds_lifetime="5000",
    )
    decision = evaluate_auto_spot_restore_park(group, min_edge_pct=Decimal("0.001"))
    assert decision["ok"] is True
    assert decision["reason"] == "park_limit"
    assert Decimal(decision["max_buy_price"]) == Decimal("49950")


def test_manage_auto_restore_previews_parked_limit(tmp_path) -> None:
    client = FakeClient()
    # Cap = 9000 / 0.1 × 0.999 = 89910, independent of the 70000 ask.
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_auto_spot_restore_min_edge_pct=Decimal("0.001"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.manage(live=False)
    restores = [a for a in result["actions"] if a.get("action") == "spot_restore_preview"]
    assert len(restores) == 1
    assert restores[0]["group_id"] == "0095"
    assert restores[0].get("auto") is True
    assert restores[0].get("park_resting") is True
    assert restores[0]["order_type"] == "limit"
    assert Decimal(restores[0]["limit_price"]) == Decimal("89910")
    assert not client.placed_orders


def test_manage_auto_restore_parks_below_market_when_exit_was_cheap(tmp_path) -> None:
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_auto_spot_restore_min_edge_pct=Decimal("0.001"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            spot_exit_quote_proceeds="5000",
            spot_exit_quote_proceeds_lifetime="5000",
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=False)
    restores = [a for a in result["actions"] if a.get("action") == "spot_restore_preview"]
    assert len(restores) == 1
    assert restores[0].get("park_resting") is True
    assert Decimal(restores[0]["limit_price"]) == Decimal("49950")
    assert not any(a.get("action") == "auto_spot_restore_skipped" for a in result["actions"])


def test_manage_auto_restore_skips_operator_skipped_exit(tmp_path) -> None:
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            spot_exit_status="skipped",
            spot_exit_amount="0",
            spot_exit_quote_proceeds="0",
            spot_exit_quote_proceeds_lifetime="0",
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=False)
    skipped = [a for a in result["actions"] if a.get("action") == "auto_spot_restore_skipped"]
    assert skipped
    assert skipped[0]["reason"] == "spot_exit_skipped"
    assert not any(a.get("action") == "spot_restore_preview" for a in result["actions"])


def test_manage_auto_restore_live_limit_not_market(tmp_path) -> None:
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_auto_spot_restore_min_edge_pct=Decimal("0.001"),
        order_label_prefix="cc",
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.manage(live=True)
    restores = [a for a in result["actions"] if a.get("action") == "spot_restore"]
    assert len(restores) == 1
    assert restores[0]["order_type"] == "limit"
    assert Decimal(str(client.placed_orders[0]["amount"])) == Decimal("0.1")
    assert client.placed_orders[0]["order_type"] == "limit"
    assert all(o["order_type"] != "market" for o in client.placed_orders)
    assert Decimal(str(client.placed_orders[0]["price"])) == Decimal("89910")


def test_manage_auto_restore_submitted_does_not_place_second_order(tmp_path) -> None:
    client = FakeClient()
    group = _itm_sold_group(short_label="cc-btc-0095")
    label = spot_restore_order_label(group, "cc")
    client.order_scripts_by_label[label] = [
        {"order_state": "open", "filled_amount": "0", "trades": []},
    ]
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_auto_spot_restore_min_edge_pct=Decimal("0.001"),
        order_label_prefix="cc",
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(group)
    engine.state_store.save(state)

    first = engine.manage(live=True)
    submitted = [a for a in first["actions"] if a.get("action") == "spot_restore_submitted"]
    assert submitted
    reloaded = engine.state_store.load()
    parked = next(g for g in reloaded.groups if str(g.group_id) == "0095")
    assert parked.spot_restore_status == "submitted"
    assert parked.spot_restore_order_id
    assert len(client.placed_orders) == 1
    assert client.placed_orders[0]["order_type"] == "limit"

    second = engine.manage(live=True)
    assert not any(a.get("action") in {"spot_restore", "spot_restore_submitted"} for a in second["actions"])
    assert len(client.placed_orders) == 1
    assert all(o["order_type"] != "market" for o in client.placed_orders)


def test_execute_auto_restore_refuses_market_even_if_requested(tmp_path) -> None:
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="cc",
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config
    group = _itm_sold_group()
    result = execute_spot_restore_for_group(
        bot,
        group,
        live=True,
        order_type="market",
        restore_reason="auto_spot_restore",
    )
    assert result["order_type"] == "limit"
    assert result.get("park_resting") is True
    assert all(o["order_type"] != "market" for o in client.placed_orders)


def test_evaluate_auto_spot_restore_park_skips_operator_cancelled() -> None:
    group = _itm_sold_group(
        spot_restore_status="skipped",
        spot_restore_reason="auto_spot_restore_park;operator_cancelled",
    )
    decision = evaluate_auto_spot_restore_park(group, min_edge_pct=Decimal("0.001"))
    assert decision["ok"] is False
    assert decision["reason"] == "operator_cancelled"


def test_manage_auto_restore_cancelled_order_does_not_repark(tmp_path) -> None:
    client = FakeClient()
    group = _itm_sold_group(
        short_label="cc-btc-0095",
        spot_restore_status="submitted",
        spot_restore_order_id="BTC_USDT-cancelled",
        spot_restore_reason="auto_spot_restore_park",
    )
    client.order_states["BTC_USDT-cancelled"] = {
        "order_state": "cancelled",
        "filled_amount": "0",
        "trades": [],
    }
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_auto_spot_restore_min_edge_pct=Decimal("0.001"),
        order_label_prefix="cc",
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(group)
    engine.state_store.save(state)

    first = engine.manage(live=True)
    assert not any(a.get("action") in {"spot_restore", "spot_restore_submitted"} for a in first["actions"])
    skipped = [a for a in first["actions"] if a.get("reason") == "operator_cancelled"]
    assert skipped
    assert not client.placed_orders
    reloaded = engine.state_store.load()
    parked = next(g for g in reloaded.groups if str(g.group_id) == "0095")
    assert parked.spot_restore_status == "skipped"
    assert "operator_cancelled" in parked.spot_restore_reason
    assert not parked.spot_restore_order_id

    second = engine.manage(live=True)
    assert not any(a.get("action") in {"spot_restore", "spot_restore_submitted"} for a in second["actions"])
    assert not client.placed_orders
