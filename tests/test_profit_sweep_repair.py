from decimal import Decimal
from unittest.mock import MagicMock

from deribit_engine.models import TradeGroup
from deribit_engine.profit_sweep_repair import (
    _group_id_from_label,
    apply_profit_sweep_state_repairs,
    build_premium_alignment_plan,
    build_profit_sweep_repair_plan,
    reconcile_premium_proceeds_to_groups,
)


def _group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0017",
        "currency": "BTC",
        "short_instrument_name": "BTC-28MAR25-90000-C",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "short_strike": "90000",
        "entry_credit": "30",
        "original_entry_credit": "30",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "realized_pnl_collateral_native": "0.001",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.002",
        "profit_sweep_quote_proceeds": "200",
        "profit_sweep_order_id": "dup-order",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_premium_sweep_fill_stats_vwap() -> None:
    client = MagicMock()
    btc_trades = {
        "trades": [
            {
                "trade_id": "s1",
                "label": "cc-profit-sweep-btc-0001",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.01",
                "price": "62000",
                "timestamp": 1_746_000_000_000,
            },
            {
                "trade_id": "b1",
                "label": "cc-profit-sweep-buyback-btc",
                "direction": "buy",
                "instrument_name": "BTC_USDT",
                "amount": "0.004",
                "price": "63000",
                "timestamp": 1_746_000_100_000,
            },
        ],
        "has_more": False,
    }

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        if currency == "BTC":
            return btc_trades
        return {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch

    from deribit_engine.profit_sweep_repair import premium_sweep_fill_stats_for_currency

    stats = premium_sweep_fill_stats_for_currency(client, "cc", "BTC")
    assert Decimal(stats["gross_native_sold"]) == Decimal("0.01")
    assert Decimal(stats["gross_usdt"]) == Decimal("620")
    assert Decimal(stats["gross_avg_price_usd"]) == Decimal("62000")
    assert Decimal(stats["net_native_sold"]) == Decimal("0.006")
    assert Decimal(stats["net_usdt"]) == Decimal("620") - Decimal("252")
    assert Decimal(stats["net_avg_price_usd"]) == Decimal("61333.33")
    assert Decimal(stats["unlabeled_native_sold"]) == Decimal("0")
    assert Decimal(stats["unlabeled_usdt"]) == Decimal("0")
    assert Decimal(stats["display_usdt"]) == Decimal(stats["net_usdt"])
    assert Decimal(stats["display_native_sold"]) == Decimal(stats["net_native_sold"])
    assert _group_id_from_label("covered_call-profit-sweep-btc-0017") == "0017"
    assert _group_id_from_label("covered_call-profit-sweep-dust-btc") is None
    assert _group_id_from_label("covered_call-profit-sweep-buyback-btc") is None


def test_build_repair_plan_uses_first_day_only() -> None:
    client = MagicMock()
    btc_trades = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-0017",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "90000",
                "timestamp": 1_746_000_000_000,
                "order_id": "o1",
            },
            {
                "trade_id": "t2",
                "label": "cc-profit-sweep-btc-0017",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "91000",
                "timestamp": 1_748_000_000_000,
                "order_id": "o2",
            },
        ],
        "has_more": False,
    }

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        if currency == "BTC":
            return btc_trades
        return {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch
    group = _group()
    plan = build_profit_sweep_repair_plan(client, [group])
    assert plan.ledgers[0].has_duplicate
    assert plan.buyback_native["BTC"] == Decimal("0.001")
    assert plan.state_proceeds_before == Decimal("200")
    assert plan.state_proceeds_after == Decimal("90")

    apply_profit_sweep_state_repairs([group], plan)
    assert group.profit_sweep_amount == Decimal("0.001")
    assert group.profit_sweep_quote_proceeds == Decimal("90")
    assert group.profit_sweep_order_id == "o1"
    assert "duplicate_sweep_repaired" in group.profit_sweep_reason


def test_build_repair_plan_skips_buyback_when_already_repaired() -> None:
    client = MagicMock()
    btc_trades = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-0017",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "90000",
                "timestamp": 1_746_000_000_000,
                "order_id": "o1",
            },
            {
                "trade_id": "t2",
                "label": "cc-profit-sweep-btc-0017",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "91000",
                "timestamp": 1_748_000_000_000,
                "order_id": "o2",
            },
        ],
        "has_more": False,
    }

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        if currency == "BTC":
            return btc_trades
        return {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch
    group = _group(profit_sweep_reason="take_profit; duplicate_sweep_repaired")
    plan = build_profit_sweep_repair_plan(client, [group])
    assert plan.buyback_native.get("BTC", Decimal(0)) == Decimal(0)


def test_build_premium_alignment_plan_buyback_excess_only() -> None:
    client = MagicMock()
    btc_trades = {
        "trades": [
            {
                "trade_id": "s1",
                "label": "cc-profit-sweep-btc-0001",
                "direction": "sell",
                "amount": "0.01",
            },
            {
                "trade_id": "b1",
                "label": "cc-profit-sweep-buyback-btc",
                "direction": "buy",
                "amount": "0.003",
            },
        ]
    }

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        if currency == "BTC":
            return btc_trades
        return {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch
    group = _group(
        realized_pnl_collateral_native="0.006",
        profit_sweep_amount="0.006",
        profit_sweep_quote_proceeds="500",
    )
    plan = build_premium_alignment_plan(client, [group])
    assert plan.premium_native["BTC"] == Decimal("0.006")
    assert plan.net_sold_native["BTC"] == Decimal("0.007")
    assert plan.buyback_native["BTC"] == Decimal("0.001")
    assert plan.buyback_native.get("ETH", Decimal(0)) == Decimal(0)
    assert plan.sell_native.get("BTC", Decimal(0)) == Decimal(0)


def test_build_premium_alignment_plan_sell_deficit() -> None:
    client = MagicMock()

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        if currency == "ETH":
            return {
                "trades": [
                    {
                        "trade_id": "s1",
                        "label": "cc-profit-sweep-eth-0002",
                        "direction": "sell",
                        "amount": "0.05",
                    }
                ],
                "has_more": False,
            }
        return {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch
    group = _group(
        group_id="0002",
        currency="ETH",
        collateral_currency="ETH",
        realized_pnl_collateral_native="0.08",
        profit_sweep_amount="0.08",
        profit_sweep_quote_proceeds="200",
    )
    plan = build_premium_alignment_plan(client, [group])
    assert plan.premium_native["ETH"] == Decimal("0.08")
    assert plan.net_sold_native["ETH"] == Decimal("0.05")
    assert plan.sell_native["ETH"] == Decimal("0.03")


def test_reconcile_premium_proceeds_allocates_by_premium_share() -> None:
    client = MagicMock()
    btc_trades = {
        "trades": [
            {
                "trade_id": "s1",
                "label": "cc-profit-sweep-btc-0001",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.002",
                "price": "100000",
                "timestamp": 1_746_000_000_000,
            }
        ],
        "has_more": False,
    }

    def _fetch(currency: str, **kwargs):
        if kwargs.get("historical") is False:
            return {"trades": [], "has_more": False}
        if currency == "BTC":
            return btc_trades
        return {"trades": [], "has_more": False}

    client.get_user_trades_by_currency.side_effect = _fetch

    g1 = _group(
        group_id="0001",
        realized_pnl_collateral_native="0.002",
        profit_sweep_amount="0.002",
        profit_sweep_quote_proceeds="100",
    )
    g2 = _group(
        group_id="0003",
        realized_pnl_collateral_native="0.004",
        profit_sweep_amount="0.004",
        profit_sweep_quote_proceeds="50",
    )
    summary = reconcile_premium_proceeds_to_groups(
        [g1, g2],
        client,
        "cc",
        apply=True,
        target_total_usdt=Decimal("200"),
    )
    assert summary["updated_groups"] == 2
    assert g1.profit_sweep_quote_proceeds == Decimal("200")
    assert g2.profit_sweep_quote_proceeds == Decimal("0")
    assert g1.profit_sweep_quote_proceeds_lifetime == g1.profit_sweep_quote_proceeds
    assert g1.profit_sweep_amount == Decimal("0.002")
    assert "proceeds_reconciled" in g1.profit_sweep_reason
