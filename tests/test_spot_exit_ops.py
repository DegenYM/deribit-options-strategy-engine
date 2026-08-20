from decimal import Decimal
from unittest.mock import MagicMock

from deribit_engine.models import TradeGroup
from deribit_engine.spot_exit_ops import (
    apply_spot_exit_quote_proceeds,
    filter_unlabeled_trades_excluding_spot_exits,
    reconcile_spot_exit_from_exchange,
    reschedule_incomplete_spot_exits,
    spot_exit_fill_stats_for_currency,
    spot_exit_filled_native,
    spot_exit_realized_usdt,
)


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
        "spot_exit_order_id": "spot-exit-order",
        "spot_exit_reason": "covered_call_settlement_exit",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_apply_spot_exit_quote_proceeds_records_lifetime() -> None:
    group = _group(spot_exit_quote_proceeds="0", spot_exit_quote_proceeds_lifetime="0")
    trades = [
        {
            "direction": "sell",
            "instrument_name": "BTC_USDT",
            "amount": "0.1",
            "price": "90000",
            "fee": "1",
            "fee_currency": "USDT",
        }
    ]
    proceeds = apply_spot_exit_quote_proceeds(group, trades)
    assert proceeds == Decimal("8999")
    assert group.spot_exit_quote_proceeds == Decimal("8999")
    assert group.spot_exit_quote_proceeds_lifetime == Decimal("8999")
    assert spot_exit_realized_usdt(group) == Decimal("8999")


def test_spot_exit_fill_stats_excludes_profit_sweep_labels() -> None:
    client = MagicMock()
    trades = {
        "trades": [
            {
                "trade_id": "e1",
                "label": "cc-btc-0017-spot-exit",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.1",
                "price": "90000",
                "timestamp": 1_746_000_000_000,
            },
            {
                "trade_id": "p1",
                "label": "cc-profit-sweep-btc-0017",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "91000",
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
    stats = spot_exit_fill_stats_for_currency(client, "BTC")
    assert Decimal(stats["native_sold"]) == Decimal("0.1")
    assert Decimal(stats["usdt"]) == Decimal("9000")
    assert Decimal(stats["avg_price_usd"]) == Decimal("90000")


def test_filter_unlabeled_excludes_attributed_spot_exit_order() -> None:
    group = _group()
    trades = [
        {
            "trade_id": "u1",
            "order_id": "spot-exit-order",
            "direction": "sell",
            "instrument_name": "BTC_USDT",
            "amount": "0.1",
            "price": "90000",
            "timestamp": 1_746_000_050_000,
        },
        {
            "trade_id": "u2",
            "order_id": "premium-order",
            "direction": "sell",
            "instrument_name": "BTC_USDT",
            "amount": "0.001",
            "price": "91000",
            "timestamp": 1_746_000_100_000,
        },
    ]
    filtered = filter_unlabeled_trades_excluding_spot_exits(trades, currency="BTC", groups=[group])
    assert [t["trade_id"] for t in filtered] == ["u2"]


def test_reconcile_spot_exit_from_exchange_by_order_id() -> None:
    group = _group(spot_exit_quote_proceeds="0", spot_exit_quote_proceeds_lifetime="0")
    client = MagicMock()
    client.get_user_trades_by_order.return_value = [
        {
            "direction": "sell",
            "instrument_name": "BTC_USDT",
            "amount": "0.1",
            "price": "88000",
            "order_id": "spot-exit-order",
            "timestamp": 1_746_000_000_000,
        }
    ]
    assert reconcile_spot_exit_from_exchange(group, client=client) is True
    assert group.spot_exit_quote_proceeds == Decimal("8800")
    assert group.spot_exit_quote_proceeds_lifetime == Decimal("8800")


def test_reconcile_spot_exit_repairs_failed_partial_fill() -> None:
    """Cancelled order with partial fill stays pending so manage can sell the remainder."""
    group = _group(
        spot_exit_status="failed",
        spot_exit_amount="0.9845",
        spot_exit_quote_proceeds="0",
        spot_exit_quote_proceeds_lifetime="0",
        spot_exit_order_id="ETH_USDT-partial",
    )
    client = MagicMock()
    client.get_user_trades_by_order.return_value = [
        {
            "direction": "sell",
            "instrument_name": "ETH_USDT",
            "amount": "0.0564",
            "price": "1886.198",
            "order_id": "ETH_USDT-partial",
            "timestamp": 1_784_275_200_000,
        }
    ]
    assert reconcile_spot_exit_from_exchange(group, client=client) is True
    assert group.spot_exit_status == "pending"
    assert group.spot_exit_amount == Decimal("0.0564")
    assert group.spot_exit_quote_proceeds == Decimal("0.0564") * Decimal("1886.198")
    assert group.spot_exit_quote_proceeds_lifetime == group.spot_exit_quote_proceeds
    assert spot_exit_filled_native(group) == Decimal("0.0564")
    assert spot_exit_realized_usdt(group) == group.spot_exit_quote_proceeds


def test_reschedule_incomplete_spot_exits_requeues_filled_partial() -> None:
    group = _group(
        spot_exit_status="filled",
        spot_exit_amount="0.9355",
        spot_exit_quote_proceeds="1700",
        spot_exit_quote_proceeds_lifetime="1700",
    )
    assert reschedule_incomplete_spot_exits([group], remaining_native_for_group=lambda _g: Decimal("0.049")) == 1
    assert group.spot_exit_status == "pending"


def test_reschedule_incomplete_spot_exits_keeps_complete_filled() -> None:
    group = _group(spot_exit_status="filled", spot_exit_amount="0.9845")
    assert reschedule_incomplete_spot_exits([group], remaining_native_for_group=lambda _g: Decimal("0")) == 0
    assert group.spot_exit_status == "filled"


def test_reschedule_incomplete_spot_exits_skips_after_restore() -> None:
    """AN 0035: after restore-to-cover, do not re-sell structural remainder (e.g. 0.0582)."""
    group = _group(
        spot_exit_status="filled",
        spot_exit_amount="0.9355",
        spot_exit_quote_proceeds="1700",
        spot_exit_quote_proceeds_lifetime="1700",
        spot_restore_status="filled",
        spot_restore_amount="0.9441",
    )
    assert reschedule_incomplete_spot_exits([group], remaining_native_for_group=lambda _g: Decimal("0.0582")) == 0
    assert group.spot_exit_status == "filled"


def test_reschedule_incomplete_spot_exits_skips_while_restore_pending() -> None:
    group = _group(
        spot_exit_status="filled",
        spot_exit_amount="0.9355",
        spot_restore_status="pending",
        spot_restore_amount="0.9441",
    )
    assert reschedule_incomplete_spot_exits([group], remaining_native_for_group=lambda _g: Decimal("0.0582")) == 0
    assert group.spot_exit_status == "filled"


def test_spot_exit_filled_native_ignores_pending_plan_amount() -> None:
    group = _group(
        spot_exit_status="pending",
        spot_exit_amount="0.9845",
        spot_exit_order_id="",
        spot_exit_quote_proceeds="0",
        spot_exit_quote_proceeds_lifetime="0",
    )
    assert spot_exit_filled_native(group) == Decimal("0")


def test_spot_exit_filled_native_ignores_unfilled_pending_order() -> None:
    """not_enough_funds plan + order id is not a cover sale (Youming #0082)."""
    group = _group(
        spot_exit_status="pending",
        spot_exit_amount="0.9845",
        spot_exit_order_id="ETH_USDT-failed",
        spot_exit_quote_proceeds="0",
        spot_exit_quote_proceeds_lifetime="0",
        spot_exit_reason="not_enough_funds",
    )
    assert spot_exit_filled_native(group) == Decimal("0")
