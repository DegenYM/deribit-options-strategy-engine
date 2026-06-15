from decimal import Decimal
from unittest.mock import MagicMock

from deribit_engine.models import TradeGroup
from deribit_engine.profit_sweep_dust import (
    apply_dust_sweep_allocation,
    collect_dust_remainder_rows,
    dust_pool_sell_budget,
    dust_sweep_order_label,
    dust_sweep_trades_for_currency,
    exchange_dust_swept_native,
    reconcile_dust_sweep_from_exchange,
)
from deribit_engine.profit_sweep_ops import sync_filled_profit_sweep_amounts_to_premium


def _group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0001",
        "currency": "BTC",
        "short_instrument_name": "BTC-28MAR25-90000-C",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "closed_timestamp_ms": 10,
        "short_strike": "90000",
        "entry_credit": "30",
        "original_entry_credit": "30",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "realized_pnl_collateral_native": "0.00006",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.000054",
        "profit_sweep_quote_proceeds": "3.4",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_dust_pool_sell_budget_ignores_historical_oversell() -> None:
    assert dust_pool_sell_budget(Decimal("0.000252"), Decimal("0.0001")) == Decimal("0.0001")
    assert dust_pool_sell_budget(Decimal("0.000252"), Decimal("0")) == Decimal("0")
    assert dust_pool_sell_budget(Decimal("0.000252"), Decimal("0.0005")) == Decimal("0.000252")


def test_sync_filled_profit_sweep_amounts_to_premium() -> None:
    group = _group(profit_sweep_amount="0.000054")
    assert sync_filled_profit_sweep_amounts_to_premium([group]) == 1
    assert group.profit_sweep_amount == Decimal("0.00006")
    assert "premium_amount_synced" in group.profit_sweep_reason


def test_dust_sweep_order_label() -> None:
    assert dust_sweep_order_label("cc", "BTC") == "cc-profit-sweep-dust-btc"


def test_exchange_dust_swept_native_sums_dust_label_only() -> None:
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "d1",
                "label": "cc-profit-sweep-dust-btc",
                "direction": "sell",
                "amount": "0.0001",
            },
            {
                "trade_id": "d2",
                "label": "cc-profit-sweep-dust-btc",
                "direction": "sell",
                "amount": "0.0002",
            },
            {
                "trade_id": "g1",
                "label": "cc-profit-sweep-btc-0001",
                "direction": "sell",
                "amount": "0.001",
            },
        ]
    }
    assert exchange_dust_swept_native(client, "cc", "BTC") == Decimal("0.0003")
    assert len(dust_sweep_trades_for_currency(client, "cc", "BTC")) == 2


def test_reconcile_dust_sweep_from_exchange_backfills_unallocated(tmp_path) -> None:
    from conftest import FakeClient, make_config

    from deribit_engine.engine import DeribitOptionTrialBot

    client = FakeClient(btc_book_equity="0.5")
    client.user_trades_by_currency = {
        ("BTC", None): {
            "trades": [
                {
                    "trade_id": "d1",
                    "label": "cc-profit-sweep-dust-btc",
                    "direction": "sell",
                    "amount": "0.000012",
                    "price": "62500",
                    "instrument_name": "BTC_USDT",
                    "timestamp": 1,
                }
            ],
            "has_more": False,
        }
    }
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_profit_sweep_enabled=True,
        traded_collaterals=("BTC", "ETH", "USDT"),
        order_label_prefix="cc",
    )
    bot = DeribitOptionTrialBot(config, client)
    groups = [
        _group(profit_sweep_amount="0.000054"),
        _group(
            group_id="0002",
            realized_pnl_collateral_native="0.00005",
            profit_sweep_amount="0.000044",
            profit_sweep_quote_proceeds="2.8",
        ),
    ]
    assert reconcile_dust_sweep_from_exchange(bot, groups) == 2
    assert "dust_pool_sweep" in groups[0].profit_sweep_reason
    assert groups[0].profit_sweep_amount == Decimal("0.00006")
    assert groups[0].profit_sweep_quote_proceeds > Decimal("3.4")
    assert reconcile_dust_sweep_from_exchange(bot, groups) == 0


def test_collect_dust_remainder_rows() -> None:
    bot = MagicMock()
    bot.config.order_label_prefix = "cc"
    bot.client.get_user_trades_by_currency.return_value = {"trades": []}
    bot._covered_call_profit_sweep_instrument.side_effect = lambda c: f"{c.upper()}_USDT"
    bot._spot_min_trade_amount.return_value = (Decimal("0.0001"), Decimal("0.0001"))

    rows = collect_dust_remainder_rows(
        bot,
        [
            _group(),
            _group(
                group_id="0002",
                realized_pnl_collateral_native="0.00005",
                profit_sweep_amount="0.000044",
                profit_sweep_quote_proceeds="2.8",
            ),
        ],
    )
    assert len(rows) == 2
    assert sum(row.remainder_native for row in rows) == Decimal("0.000012")


def test_apply_dust_sweep_allocation() -> None:
    from deribit_engine.profit_sweep_dust import DustRemainderRow

    groups = [
        _group(profit_sweep_amount="0.000054"),
        _group(group_id="0002", realized_pnl_collateral_native="0.00005", profit_sweep_amount="0.000044"),
    ]
    rows = [
        DustRemainderRow("0001", "BTC", Decimal("0.000006"), 10),
        DustRemainderRow("0002", "BTC", Decimal("0.000006"), 11),
    ]
    allocated = apply_dust_sweep_allocation(
        groups,
        rows,
        sold_native=Decimal("0.000012"),
        proceeds_usdt=Decimal("0.75"),
    )
    assert allocated == ["0001", "0002"]
    assert groups[0].profit_sweep_status == "filled"
    assert groups[1].profit_sweep_status == "filled"
    assert groups[0].profit_sweep_quote_proceeds == Decimal("3.775")
    assert "dust_pool_sweep" in groups[0].profit_sweep_reason
