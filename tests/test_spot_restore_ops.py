from decimal import Decimal
from unittest.mock import MagicMock

from conftest import FakeClient, make_config

from deribit_engine.models import TradeGroup
from deribit_engine.spot_restore_ops import (
    SpotRestoreRunSummary,
    apply_spot_restore_quote_spent,
    execute_spot_restore_for_group,
    format_spot_restore_human_report,
    itm_spot_round_trip_complete,
    list_spot_restore_candidates,
    plan_spot_restore_to_cover,
    reconcile_spot_restore_from_exchange,
    resolve_spot_restore_order_size,
    spot_restore_fill_stats_for_currency,
    spot_restore_order_label,
    unrestored_spot_exit_native,
)
from deribit_engine.wallet_ops import spot_buy_quote_spent_from_trades


def _group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0017",
        "currency": "BTC",
        "short_instrument_name": "BTC-28MAR25-90000-C",
        "short_label": "cc-btc-0017",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "covered_underlying_quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "closed_timestamp_ms": 1_746_000_000_000,
        "short_strike": "90000",
        "entry_credit": "30",
        "original_entry_credit": "30",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "spot_exit_status": "filled",
        "spot_exit_amount": "0.1",
        "spot_exit_quote_proceeds": "9000",
        "spot_exit_quote_proceeds_lifetime": "9000",
        "spot_exit_order_id": "spot-exit-order",
        "spot_exit_reason": "covered_call_settlement_exit",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_spot_buy_quote_spent_from_trades_adds_fees() -> None:
    trades = [
        {
            "direction": "buy",
            "instrument_name": "BTC_USDT",
            "amount": "0.1",
            "price": "91000",
            "fee": "2",
            "fee_currency": "USDT",
        }
    ]
    assert spot_buy_quote_spent_from_trades(trades) == Decimal("9102")


def test_unrestored_and_candidates() -> None:
    group = _group()
    # swap only (no settle / entry fee) → restore the sold amount
    assert unrestored_spot_exit_native(group) == Decimal("0.1")
    rows = list_spot_restore_candidates([group])
    assert len(rows) == 1
    assert rows[0].unrestored_amount == Decimal("0.1")
    assert spot_restore_order_label(group, "covered_call") == "cc-btc-0017-spot-restore"

    restored = _group(
        spot_restore_status="filled",
        spot_restore_amount="0.04",
        spot_restore_quote_spent="3700",
    )
    assert unrestored_spot_exit_native(restored) == Decimal("0.06")


def test_plan_spot_restore_is_swap_plus_settle_minus_premium() -> None:
    group = _group(
        spot_exit_amount="0.085",
        spot_exit_settlement_loss="0.012",
        short_entry_average_price="0.003",
        quantity="1",
        entry_fee_collateral="0.0003",
        covered_underlying_quantity="0.1",
    )
    # net premium = 0.003 - 0.0003 = 0.0027 (qty=1 on entry prices; cover size separate)
    plan = plan_spot_restore_to_cover(group)
    assert plan["swap"] == Decimal("0.085")
    assert plan["settle"] == Decimal("0.012")
    assert plan["premium"] == Decimal("0.0027")
    assert plan["target"] == Decimal("0.0943")  # 0.085+0.012-0.0027
    assert plan["premium_still_held_est"] is True
    assert unrestored_spot_exit_native(group) == Decimal("0.0943")

    partial_restore = _group(
        spot_exit_amount="0.085",
        spot_exit_settlement_loss="0.012",
        short_entry_average_price="0.003",
        quantity="1",
        entry_fee_collateral="0.0003",
        covered_underlying_quantity="0.1",
        spot_restore_status="filled",
        spot_restore_amount="0.04",
    )
    assert unrestored_spot_exit_native(partial_restore) == Decimal("0.0543")


def test_plan_spot_restore_honours_partial_pending_swap() -> None:
    """Only part of the ITM spot exit filled — restore must not invent the unsold remainder."""
    group = _group(
        spot_exit_status="pending",
        spot_exit_amount="0.03",
        spot_exit_order_id="partial-exit",
        spot_exit_quote_proceeds="2100",
        spot_exit_settlement_loss="0.01",
        short_entry_average_price="0.002",
        quantity="1",
        entry_fee_collateral="0.0002",
        covered_underlying_quantity="0.1",
    )
    plan = plan_spot_restore_to_cover(group)
    assert plan["swap"] == Decimal("0.03")
    assert plan["settle"] == Decimal("0.01")
    assert plan["premium"] == Decimal("0.0018")
    # 0.03+0.01-0.0018 = 0.0382 — must NOT pad up to cover 0.1
    assert plan["target"] == Decimal("0.0382")
    assert unrestored_spot_exit_native(group) == Decimal("0.0382")


def test_plan_spot_restore_an_0035_style_premium_still_held() -> None:
    """Profit-sweep on, but ITM only sold cover−settle — premium stays native."""
    group = _group(
        spot_exit_status="filled",
        spot_exit_amount="0.9355",
        spot_exit_settlement_loss="0.0645",
        short_entry_average_price="0.0095",
        quantity="1",
        entry_fee_collateral="0.00027",
        covered_underlying_quantity="1",
    )
    plan = plan_spot_restore_to_cover(group)
    assert plan["premium"] == Decimal("0.00923")
    assert plan["premium_still_held_est"] is True
    assert plan["target"] == Decimal("0.99077")  # back to cover; keep ~0.00923 premium


def test_apply_spot_restore_quote_spent_records_lifetime() -> None:
    group = _group()
    trades = [
        {
            "direction": "buy",
            "instrument_name": "BTC_USDT",
            "amount": "0.1",
            "price": "91000",
            "fee": "1",
            "fee_currency": "USDT",
        }
    ]
    spent = apply_spot_restore_quote_spent(group, trades)
    assert spent == Decimal("9101")
    assert group.spot_restore_quote_spent == Decimal("9101")
    assert group.spot_restore_quote_spent_lifetime == Decimal("9101")


def test_spot_restore_fill_stats_only_restore_buys() -> None:
    client = MagicMock()
    trades = {
        "trades": [
            {
                "trade_id": "r1",
                "label": "cc-btc-0017-spot-restore",
                "direction": "buy",
                "instrument_name": "BTC_USDT",
                "amount": "0.1",
                "price": "91000",
                "timestamp": 1_746_000_000_000,
            },
            {
                "trade_id": "b1",
                "label": "cc-profit-sweep-buyback-btc",
                "direction": "buy",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "90000",
                "timestamp": 1_746_000_100_000,
            },
        ],
        "has_more": False,
    }

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        return trades if currency == "BTC" else {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch
    stats = spot_restore_fill_stats_for_currency(client, "BTC")
    assert Decimal(stats["native_bought"]) == Decimal("0.1")
    assert Decimal(stats["usdt_spent"]) == Decimal("9100")


def test_reconcile_spot_restore_from_exchange_by_order_id() -> None:
    group = _group(spot_restore_order_id="restore-order")
    client = MagicMock()
    client.get_user_trades_by_order.return_value = [
        {
            "direction": "buy",
            "instrument_name": "BTC_USDT",
            "amount": "0.1",
            "price": "90500",
            "order_id": "restore-order",
            "timestamp": 1_746_000_000_000,
        }
    ]
    assert reconcile_spot_restore_from_exchange(group, client=client, order_label_prefix="cc") is True
    assert group.spot_restore_status == "filled"
    assert group.spot_restore_amount == Decimal("0.1")
    assert group.spot_restore_quote_spent == Decimal("9050")


def test_resolve_spot_restore_order_size_usdt_and_native() -> None:
    unrestored = Decimal("0.1")
    price = Decimal("70000")
    quote_for_full = unrestored * price * Decimal("1.005")

    by_usdt = resolve_spot_restore_order_size(
        unrestored=unrestored,
        amount=None,
        quote_usdt=Decimal("3500"),
        trade_price=price,
        quote_budget_for_unrestored=quote_for_full,
    )
    assert by_usdt["ok"] is True
    assert by_usdt["size_mode"] == "usdt"
    assert by_usdt["quote_budget"] == Decimal("3500")
    assert abs(by_usdt["target"] - Decimal("3500") / price) < Decimal("1e-12")

    capped = resolve_spot_restore_order_size(
        unrestored=unrestored,
        amount=None,
        quote_usdt=Decimal("999999"),
        trade_price=price,
        quote_budget_for_unrestored=quote_for_full,
    )
    assert capped["ok"] is True
    assert capped["quote_budget"] == quote_for_full
    assert capped["usdt_capped_to_unrestored"] is True

    both = resolve_spot_restore_order_size(
        unrestored=unrestored,
        amount=Decimal("0.05"),
        quote_usdt=Decimal("3500"),
        trade_price=price,
        quote_budget_for_unrestored=quote_for_full,
    )
    assert both["ok"] is False
    assert both["reason"] == "amount_and_usdt_mutually_exclusive"

    by_amount = resolve_spot_restore_order_size(
        unrestored=unrestored,
        amount=Decimal("0.05"),
        quote_usdt=None,
        trade_price=price,
        quote_budget_for_unrestored=quote_for_full,
    )
    assert by_amount["ok"] is True
    assert by_amount["size_mode"] == "native"
    assert by_amount["target"] == Decimal("0.05")


def test_execute_spot_restore_preview_accepts_usdt(tmp_path) -> None:
    group = _group()
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    preview = execute_spot_restore_for_group(
        bot,
        group,
        quote_usdt=Decimal("3500"),
        live=False,
    )
    assert preview["action"] == "spot_restore_preview"
    assert preview["size_mode"] == "usdt"
    assert Decimal(preview["requested_usdt"]) == Decimal("3500")
    assert Decimal(preview["quote_budget_usdt"]) == Decimal("3500")
    assert Decimal(preview["restore_amount"]) == Decimal("3500") / Decimal("70000")


def test_execute_spot_restore_default_targets_swap_settle_minus_premium(tmp_path) -> None:
    group = _group(
        spot_exit_amount="0.085",
        spot_exit_settlement_loss="0.012",
        spot_exit_settlement_loss_source="transaction_log",
        short_entry_average_price="0.003",
        quantity="1",
        entry_fee_collateral="0.0003",
        covered_underlying_quantity="0.1",
    )
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    preview = execute_spot_restore_for_group(bot, group, live=False)
    assert preview["action"] == "spot_restore_preview"
    assert preview["swap_amount"] == "0.085"
    assert preview["spot_exit_filled_native"] == "0.085"
    assert preview["settlement_loss"] == "0.012"
    assert preview["premium_native"] == "0.0027"
    assert preview["restore_target"] == "0.0943"
    assert preview["buy_amount"] == "0.0943"
    assert preview["buy_currency"] == "BTC"
    assert preview["current_price"] == "70000"
    assert preview["order_type"] == "limit"
    assert preview["post_only"] is True
    assert preview["wait_seconds"] == 120
    assert preview["current_price_source"] == "best_bid"
    assert preview["estimated_usdt"] == "6601"
    assert preview["order_budget_usdt"] == "6601"
    assert preview["limit_price"] == "70000"
    comp = preview["buy_amount_composition"]
    assert comp["swap_sold"] == "0.085"
    assert comp["settlement_loss"] == "0.012"
    assert comp["premium_native"] == "0.0027"
    assert comp["this_order_buy_amount"] == "0.0943"
    assert "premium" in comp["expression"]
    assert preview["preview"]["buy_amount"] == "0.0943"
    assert preview["unrestored_amount"] == "0.0943"

    report = format_spot_restore_human_report(SpotRestoreRunSummary(live=False, actions=[preview]))
    text = "\n".join(report)
    assert "預計買回: 0.0943 BTC" in text
    assert "組成:" in text
    assert "當前價格: 70000 USDT" in text
    assert "預計花費: 6601 USDT" in text
    assert "limit@bid GTC" in text
    assert "wait=120s" in text
    assert "spot_exit: status=filled  filled=0.085 BTC" in text


def test_execute_spot_restore_market_preview_uses_ask_buffer(tmp_path) -> None:
    group = _group(
        spot_exit_amount="0.085",
        spot_exit_settlement_loss="0.012",
        spot_exit_settlement_loss_source="transaction_log",
        short_entry_average_price="0.003",
        quantity="1",
        entry_fee_collateral="0.0003",
        covered_underlying_quantity="0.1",
    )
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    preview = execute_spot_restore_for_group(bot, group, live=False, order_type="market")
    assert preview["order_type"] == "market"
    assert preview["current_price_source"] == "best_ask"
    assert preview["estimated_usdt"] == "6601"
    assert preview["order_budget_usdt"] == "6634.005"
    assert "1.005" in preview["order_budget_usdt_meaning"]


def test_execute_spot_restore_live_limit_fills_and_records(tmp_path) -> None:
    group = _group(
        spot_exit_amount="0.1",
        spot_exit_settlement_loss="0",
        short_entry_average_price="0",
        quantity="1",
        covered_underlying_quantity="0.1",
    )
    client = FakeClient()
    # Ensure USDT book has funds for sanity; FakeClient fills limit immediately.
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
        spot_restore_wait_seconds=2,
        order_poll_seconds=1,
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    result = execute_spot_restore_for_group(
        bot,
        group,
        live=True,
        order_type="limit",
        wait_seconds=2,
        sleep_fn=lambda _s: None,
    )
    assert result["action"] == "spot_restore"
    assert result["order_type"] == "limit"
    assert Decimal(result["filled_native"]) == Decimal("0.1")
    assert group.spot_restore_status == "filled"
    assert group.spot_restore_amount == Decimal("0.1")
    assert client.placed_orders
    assert client.placed_orders[0]["order_type"] == "limit"
    assert client.placed_orders[0]["post_only"] is True
    assert client.placed_orders[0]["time_in_force"] == "good_til_cancelled"


def test_execute_spot_restore_live_limit_times_out_unfilled(tmp_path) -> None:
    group = _group(
        spot_exit_amount="0.1",
        spot_exit_settlement_loss="0",
        short_entry_average_price="0",
        quantity="1",
        covered_underlying_quantity="0.1",
    )
    client = FakeClient()
    label = spot_restore_order_label(group, "covered_call")
    # Enough open scripts for place + any reprice attempts inside the wait window.
    client.order_scripts_by_label[label] = [
        {"order_state": "open", "filled_amount": "0", "average_price": "0", "trades": []},
        {"order_state": "open", "filled_amount": "0", "average_price": "0", "trades": []},
        {"order_state": "open", "filled_amount": "0", "average_price": "0", "trades": []},
    ]
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
        order_poll_seconds=1,
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    result = execute_spot_restore_for_group(
        bot,
        group,
        live=True,
        order_type="limit",
        wait_seconds=1,
        sleep_fn=lambda _s: None,
    )
    assert result["action"] == "spot_restore_skipped"
    assert result["reason"] == "timed_out"
    assert not group.spot_restore_status
    assert client.cancelled_orders


def test_execute_spot_restore_preview_partial_pending_swap(tmp_path) -> None:
    group = _group(
        spot_exit_status="pending",
        spot_exit_amount="0.03",
        spot_exit_order_id="partial-exit",
        spot_exit_quote_proceeds="2100",
        spot_exit_settlement_loss="0.01",
        short_entry_average_price="0",
        entry_fee_collateral="0",
        covered_underlying_quantity="0.1",
    )
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    preview = execute_spot_restore_for_group(bot, group, live=False)
    assert preview["action"] == "spot_restore_preview"
    assert preview["spot_exit_status"] == "pending"
    assert preview["buy_amount"] == "0.04"  # 0.03 swap + 0.01 settle − 0 premium
    assert preview["estimated_usdt"] == "2800"


def test_execute_spot_restore_omits_dust_below_min_not_round_up(tmp_path) -> None:
    """Remainder below ETH_USDT min (0.001) is omitted — never rounded up past cover."""
    group = _group(
        group_id="0070",
        currency="ETH",
        short_instrument_name="ETH-31JUL26-1900-C",
        short_label="cc-eth-0070",
        covered_underlying_quantity="2",
        quantity="2",
        spot_exit_amount="2.0156",
        spot_exit_settlement_loss="0.00381376",
        spot_exit_settlement_loss_source="intrinsic",
        spot_exit_quote_proceeds="3839.52123",
        short_entry_average_price="0.01",
        entry_fee_collateral="0.00054",
        spot_restore_status="filled",
        spot_restore_amount="1.9999",
        spot_restore_quote_spent="3800",
        spot_restore_quote_spent_lifetime="3800",
    )
    # unrestored ≈ 2.0156 + 0.00381376 − 0.01946 − 1.9999 = 0.00005376 < 0.001
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
        managed_currencies=("ETH",),
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config

    preview = execute_spot_restore_for_group(bot, group, live=False)
    assert preview["action"] == "spot_restore_skipped"
    assert preview["reason"] == "dust_below_min"
    assert preview["dust_policy"] == "omit_not_round_up"
    assert Decimal(preview["buy_amount"]) < Decimal(preview["min_trade_amount"])
    assert "marked_complete" not in preview

    live = execute_spot_restore_for_group(bot, group, live=True)
    assert live["action"] == "spot_restore_skipped"
    assert live["reason"] == "dust_below_min"
    assert live["marked_complete"] is True
    assert "dust_below_min_omitted" in group.spot_restore_reason
    assert group.spot_restore_amount == Decimal("1.9999")  # unchanged — not rounded up
    assert not client.placed_orders
    assert itm_spot_round_trip_complete(group) is True

    report = "\n".join(format_spot_restore_human_report(SpotRestoreRunSummary(live=True, actions=[live])))
    assert "dust_below_min" in report
    assert "omit" in report
