from decimal import Decimal
from unittest.mock import MagicMock

from deribit_engine.models import TradeGroup
from deribit_engine.spot_restore_ops import (
    apply_spot_restore_quote_spent,
    list_spot_restore_candidates,
    reconcile_spot_restore_from_exchange,
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
