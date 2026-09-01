from decimal import Decimal

from conftest import FakeClient, make_config

from deribit_engine.csp_premium_swap_ops import (
    csp_premium_net_usdc,
    csp_premium_swap_base_filled_from_trades,
    csp_premium_swap_order_label,
    csp_premium_swap_ready,
    csp_premium_swap_target_is_spot,
    schedule_csp_premium_swap,
)
from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import StrategyState, TradeGroup


def _csp_child(**overrides) -> TradeGroup:
    payload = {
        "group_id": "9101",
        "currency": "BTC",
        "strategy": "cash_secured",
        "collateral_currency": "USDC",
        "quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "short_instrument_name": "BTC_USDC-14APR30-60000-P",
        "short_strike": "60000",
        "entry_credit": "120",
        "original_entry_credit": "120",
        "max_loss": "6000",
        "regime_at_entry": "normal",
        "cash_secured_from_group_id": "0095",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_target_is_spot() -> None:
    assert csp_premium_swap_target_is_spot("spot") is True
    assert csp_premium_swap_target_is_spot("SPOT") is True
    assert csp_premium_swap_target_is_spot("usdc") is False
    assert csp_premium_swap_target_is_spot("") is False


def test_net_usdc_is_entry_credit() -> None:
    assert csp_premium_net_usdc(_csp_child(entry_credit="120")) == Decimal("120")
    assert csp_premium_net_usdc(_csp_child(entry_credit="0")) == Decimal("0")


def test_ready_requires_cash_secured_and_premium() -> None:
    ready, reason = csp_premium_swap_ready(_csp_child())
    assert ready is True and reason == ""

    ok, reason = csp_premium_swap_ready(_csp_child(entry_credit="0"))
    assert ok is False and reason == "no_premium"

    naked = _csp_child(strategy="naked_short", cash_secured_from_group_id="")
    ok, reason = csp_premium_swap_ready(naked)
    assert ok is False and reason == "not_cash_secured"


def test_ready_blocks_terminal_and_in_flight() -> None:
    assert csp_premium_swap_ready(_csp_child(csp_premium_swap_status="filled")) == (False, "already_filled")
    assert csp_premium_swap_ready(_csp_child(csp_premium_swap_status="skipped")) == (False, "already_skipped")
    assert csp_premium_swap_ready(_csp_child(csp_premium_swap_status="submitted")) == (False, "already_submitted")


def test_schedule_sets_pending_amount() -> None:
    group = _csp_child()
    amount = schedule_csp_premium_swap(group, reason="csp_premium_to_spot")
    assert amount == Decimal("120")
    assert group.csp_premium_swap_status == "pending"
    assert group.csp_premium_swap_amount == Decimal("120")
    assert group.csp_premium_swap_reason == "csp_premium_to_spot"


def test_schedule_reschedules_failed_but_not_terminal() -> None:
    failed = _csp_child(csp_premium_swap_status="failed")
    assert schedule_csp_premium_swap(failed, reason="retry") == Decimal("120")
    assert failed.csp_premium_swap_status == "pending"

    done = _csp_child(csp_premium_swap_status="filled")
    assert schedule_csp_premium_swap(done, reason="retry") == Decimal("0")
    assert done.csp_premium_swap_status == "filled"


def test_order_label() -> None:
    assert csp_premium_swap_order_label("trial", _csp_child()) == "trial-csp-premium-swap-btc-9101"


def test_base_filled_from_trades_sums_buys() -> None:
    trades = [
        {"direction": "buy", "amount": "0.001"},
        {"direction": "buy", "amount": "0.0005"},
        {"direction": "sell", "amount": "0.01"},
    ]
    assert csp_premium_swap_base_filled_from_trades(trades) == Decimal("0.0015")
    assert csp_premium_swap_base_filled_from_trades([]) == Decimal("0")
    assert csp_premium_swap_base_filled_from_trades(None) == Decimal("0")


def _swap_engine(tmp_path, client, *, target="spot"):
    return DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            covered_call_spot_exit_enabled=True,
            covered_call_itm_to_cash_secured_enabled=True,
            covered_call_csp_premium_target=target,
            covered_call_spot_order_type="market",
        ),
        client,
    )


def test_manage_swaps_csp_premium_to_spot(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(_csp_child())
    engine.state_store.save(state)

    result = engine.manage(live=True)
    swaps = [a for a in result["actions"] if a.get("action") == "csp_premium_swap"]
    assert len(swaps) == 1
    assert swaps[0]["instrument_name"] == "BTC_USDC"
    assert swaps[0]["csp_premium_swap_status"] == "filled"

    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.csp_premium_swap_status == "filled"
    assert child.csp_premium_swap_native > 0
    buy_orders = [o for o in client.placed_orders if o["instrument_name"] == "BTC_USDC"]
    assert buy_orders and buy_orders[0]["direction"] == "buy"


def test_manage_does_not_swap_when_target_usdc(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="usdc")
    state = StrategyState()
    state.groups.append(_csp_child())
    engine.state_store.save(state)

    result = engine.manage(live=True)
    assert not [a for a in result["actions"] if str(a.get("action", "")).startswith("csp_premium_swap")]
    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.csp_premium_swap_status == ""


def test_manage_swap_idempotent_after_fill(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(_csp_child())
    engine.state_store.save(state)

    engine.manage(live=True)
    orders_after_first = len(client.placed_orders)
    second = engine.manage(live=True)
    assert not [a for a in second["actions"] if a.get("action") == "csp_premium_swap"]
    assert len(client.placed_orders) == orders_after_first
