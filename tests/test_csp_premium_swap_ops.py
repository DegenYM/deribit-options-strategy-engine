from decimal import Decimal

from conftest import FakeClient, make_config

from deribit_engine.csp_premium_swap_ops import (
    apply_csp_premium_swap_fill,
    csp_premium_disposition_split,
    csp_premium_leftover_usdc,
    csp_premium_net_usdc,
    csp_premium_realized_usdc,
    csp_premium_swap_base_filled_from_trades,
    csp_premium_swap_order_label,
    csp_premium_swap_ready,
    csp_premium_swap_remaining_usdc,
    csp_premium_swap_spent_usdc,
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
        "status": "open",
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


def _closed_csp_child(**overrides) -> TradeGroup:
    payload = {
        "status": "closed",
        "close_reason": "reconciled_expiry",
        "realized_close_debit": "0",
        "realized_close_fee": "0",
    }
    payload.update(overrides)
    return _csp_child(**payload)


def test_target_is_spot() -> None:
    assert csp_premium_swap_target_is_spot("spot") is True
    assert csp_premium_swap_target_is_spot("SPOT") is True
    assert csp_premium_swap_target_is_spot("usdc") is False
    assert csp_premium_swap_target_is_spot("") is False


def test_realized_zero_while_open_full_after_otm_expiry() -> None:
    assert csp_premium_realized_usdc(_csp_child()) == Decimal("0")
    assert csp_premium_net_usdc(_csp_child()) == Decimal("0")
    assert csp_premium_realized_usdc(_closed_csp_child()) == Decimal("120")
    bought_back = _closed_csp_child(realized_close_debit="40", realized_close_fee="1")
    assert csp_premium_realized_usdc(bought_back) == Decimal("79")


def test_ready_waits_until_closed() -> None:
    assert csp_premium_swap_ready(_csp_child()) == (False, "not_closed_yet")
    ready, reason = csp_premium_swap_ready(_closed_csp_child())
    assert ready is True and reason == ""

    waiting = _closed_csp_child(spot_restore_status="pending")
    assert csp_premium_swap_ready(waiting) == (False, "waiting_cover_restore")

    no_edge = _closed_csp_child(realized_close_debit="120")
    assert csp_premium_swap_ready(no_edge) == (False, "no_realized_premium")


def test_ready_blocks_terminal_and_in_flight() -> None:
    assert csp_premium_swap_ready(_closed_csp_child(csp_premium_swap_status="filled")) == (
        False,
        "already_filled",
    )
    assert csp_premium_swap_ready(_closed_csp_child(csp_premium_swap_status="skipped")) == (
        False,
        "already_skipped",
    )
    assert csp_premium_swap_ready(_closed_csp_child(csp_premium_swap_status="submitted")) == (
        False,
        "already_submitted",
    )


def test_schedule_sets_pending_without_resetting_spent() -> None:
    group = _closed_csp_child()
    amount = schedule_csp_premium_swap(group, reason="csp_premium_to_spot_after_close")
    assert amount == Decimal("120")
    assert group.csp_premium_swap_status == "pending"
    assert group.csp_premium_swap_amount == Decimal("0")
    assert group.csp_premium_swap_reason == "csp_premium_to_spot_after_close"


def test_schedule_ignores_open_groups() -> None:
    open_g = _csp_child()
    assert schedule_csp_premium_swap(open_g, reason="too_early") == Decimal("0")
    assert open_g.csp_premium_swap_status == ""


def test_schedule_reschedules_failed_but_not_terminal() -> None:
    failed = _closed_csp_child(csp_premium_swap_status="failed")
    assert schedule_csp_premium_swap(failed, reason="retry") == Decimal("120")
    assert failed.csp_premium_swap_status == "pending"

    done = _closed_csp_child(csp_premium_swap_status="filled", csp_premium_swap_amount="120")
    assert schedule_csp_premium_swap(done, reason="retry") == Decimal("0")
    assert done.csp_premium_swap_status == "filled"


def test_remaining_caps_retry_after_partial_fill() -> None:
    group = _closed_csp_child(csp_premium_swap_status="pending", csp_premium_swap_amount="40")
    assert csp_premium_swap_spent_usdc(group) == Decimal("40")
    assert csp_premium_swap_remaining_usdc(group) == Decimal("80")
    assert csp_premium_swap_ready(group) == (True, "")

    remaining = apply_csp_premium_swap_fill(group, spent_usdc=Decimal("80"), native_bought=Decimal("0.001"))
    assert remaining == Decimal("0")
    assert group.csp_premium_swap_status == "filled"
    assert group.csp_premium_swap_amount == Decimal("120")
    assert csp_premium_swap_ready(group) == (False, "already_filled")


def test_legacy_pending_target_amount_counts_as_unspent() -> None:
    group = _closed_csp_child(csp_premium_swap_status="pending", csp_premium_swap_amount="120")
    assert csp_premium_swap_spent_usdc(group) == Decimal("0")
    assert csp_premium_swap_remaining_usdc(group) == Decimal("120")


def test_below_min_notional_marks_dust_complete_after_partial() -> None:
    from deribit_engine.csp_premium_swap_ops import (
        is_csp_premium_swap_below_min_notional,
        mark_csp_premium_swap_dust_complete,
    )

    assert is_csp_premium_swap_below_min_notional(
        "Spend 1.3457 below minimum notional at price 80725 (min base=0.0001)"
    )
    group = _closed_csp_child(
        csp_premium_swap_status="pending",
        csp_premium_swap_amount="40",
        csp_premium_swap_native="0.0005",
    )
    mark_csp_premium_swap_dust_complete(group, reason="Spend 1.3457 below minimum notional at price 80725")
    assert group.csp_premium_swap_status == "filled"
    assert "dust_below_min_omitted" in str(group.csp_premium_swap_reason)
    assert csp_premium_swap_ready(group) == (False, "already_filled")


def test_below_min_notional_skips_when_nothing_bought() -> None:
    from deribit_engine.csp_premium_swap_ops import mark_csp_premium_swap_dust_complete

    group = _closed_csp_child(csp_premium_swap_status="pending")
    mark_csp_premium_swap_dust_complete(group, reason="below minimum notional")
    assert group.csp_premium_swap_status == "skipped"
    assert csp_premium_swap_ready(group) == (False, "already_skipped")


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


def test_manage_does_not_swap_open_csp(tmp_path) -> None:
    from deribit_engine.utils import utc_now_ms

    client = FakeClient(btc_book_equity="0.2")
    instr = "BTC_USDC-14APR30-60000-P"
    client.positions = [
        {
            "instrument_name": instr,
            "direction": "sell",
            "kind": "option",
            "size": "-0.1",
            "size_currency": "-0.1",
            "mark_price": "500",
            "average_price": "1200",
            "floating_profit_loss": "0",
            "delta": "-0.20",
        }
    ]
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(
        _csp_child(
            status="open",
            short_instrument_name=instr,
            expiration_timestamp_ms=utc_now_ms() + 10 * 86_400_000,
            entry_timestamp_ms=utc_now_ms() - 86_400_000,
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=True)
    assert not [a for a in result["actions"] if a.get("action") == "csp_premium_swap"]
    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.status == "open"
    assert child.csp_premium_swap_status == ""
    assert not [o for o in client.placed_orders if "csp-premium-swap" in str(o.get("label") or "")]


def test_manage_swaps_csp_premium_after_close(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(_closed_csp_child())
    engine.state_store.save(state)

    result = engine.manage(live=True)
    swaps = [a for a in result["actions"] if a.get("action") == "csp_premium_swap"]
    assert len(swaps) == 1
    assert swaps[0]["instrument_name"] == "BTC_USDC"
    assert swaps[0]["csp_premium_swap_status"] == "filled"
    assert swaps[0]["reason"] == "csp_premium_to_spot_after_close"

    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.csp_premium_swap_status == "filled"
    assert child.csp_premium_swap_native > 0
    buy_orders = [o for o in client.placed_orders if o["instrument_name"] == "BTC_USDC"]
    assert buy_orders and buy_orders[0]["direction"] == "buy"


def test_manage_waits_for_cover_restore_before_swap(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(_closed_csp_child(spot_restore_status="pending"))
    engine.state_store.save(state)

    result = engine.manage(live=True)
    assert not [a for a in result["actions"] if a.get("action") == "csp_premium_swap"]
    # May attempt cover restore with FakeClient USDC; premium swap must stay idle.
    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.csp_premium_swap_status in {"", "pending", "skipped"}
    assert child.csp_premium_swap_status != "filled" or child.spot_restore_status != "pending"


def test_manage_does_not_swap_when_target_usdc(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="usdc")
    state = StrategyState()
    state.groups.append(_closed_csp_child())
    engine.state_store.save(state)

    result = engine.manage(live=True)
    assert not [a for a in result["actions"] if str(a.get("action", "")).startswith("csp_premium_swap")]
    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.csp_premium_swap_status == ""


def test_manage_swap_idempotent_after_fill(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(_closed_csp_child())
    engine.state_store.save(state)

    engine.manage(live=True)
    orders_after_first = len(client.placed_orders)
    second = engine.manage(live=True)
    assert not [a for a in second["actions"] if a.get("action") == "csp_premium_swap"]
    assert len(client.placed_orders) == orders_after_first


def test_manage_swap_failed_retry_only_spends_remaining(tmp_path) -> None:
    """A failed-after-fill style retry must not re-buy the full premium."""
    client = FakeClient(btc_book_equity="0.2")
    engine = _swap_engine(tmp_path, client, target="spot")
    state = StrategyState()
    state.groups.append(
        _closed_csp_child(
            csp_premium_swap_status="failed",
            csp_premium_swap_amount="100",
            csp_premium_swap_native="0.001",
            csp_premium_swap_reason="csp_premium_to_spot: timeout",
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=True)
    swaps = [a for a in result["actions"] if a.get("action") == "csp_premium_swap"]
    assert len(swaps) == 1
    assert Decimal(str(swaps[0]["amount_usdc"])) <= Decimal("20.0001")
    child = next(g for g in engine.state_store.load().groups if g.group_id == "9101")
    assert child.csp_premium_swap_amount <= Decimal("120.0001")


def test_disposition_split_keeps_dust_usdc_and_bought_native() -> None:
    filled = _closed_csp_child(
        currency="BTC",
        entry_credit="53.90595884",
        csp_premium_swap_status="filled",
        csp_premium_swap_amount="48.492",
        csp_premium_swap_native="0.0006",
    )
    leftover = csp_premium_leftover_usdc(filled)
    assert abs(leftover - Decimal("5.41395884")) < Decimal("0.00000001")
    split = csp_premium_disposition_split(filled)
    assert split is not None
    assert split["spot_book"] == "BTC"
    assert split["spot_native"] == Decimal("0.0006")
    assert abs(split["remaining_usdc"] - leftover) < Decimal("0.00000001")

    skipped = _closed_csp_child(
        currency="BTC",
        entry_credit="5.6875",
        csp_premium_swap_status="skipped",
        csp_premium_swap_reason="dust_below_min_omitted",
    )
    skip_split = csp_premium_disposition_split(skipped)
    assert skip_split is not None
    assert skip_split["remaining_usdc"] == Decimal("5.6875")
    assert skip_split["spot_native"] == Decimal("0")
    assert skip_split["spot_book"] == ""
