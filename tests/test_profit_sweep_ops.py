from decimal import Decimal

from deribit_engine.models import TradeGroup
from deribit_engine.profit_sweep_ops import (
    exchange_swept_native_for_group,
    guard_profit_sweep_against_oversell,
    list_remaining_profit_sweeps,
    native_profit_for_group,
    remaining_spot_profit_native,
    reschedule_failed_profit_sweeps,
    to_sweep_native,
)


def _group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "g1",
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
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_native_profit_prefers_ledger_over_inflated_stored() -> None:
    """Profit sweep uses premium ledger, not a larger stale stored native."""
    group = _group(
        short_entry_average_price="0.006",
        short_close_average_price="0.001",
        entry_fee_collateral="0.00003",
        close_fee_collateral="0.00003",
        entry_index_usd="70000",
        close_index_usd="70000",
        realized_pnl_collateral_native="0.01",
    )
    native = native_profit_for_group(group)
    assert native == Decimal("0.00044")
    assert native < Decimal("0.01")


def test_native_profit_ignores_inflated_stored_when_ledger_shows_loss() -> None:
    """Do not sweep when premium ledger shows zero/loss even if stored native is positive."""
    group = _group(
        short_entry_average_price="0.00135",
        short_close_average_price="0.00085",
        entry_fee_collateral="0.00003",
        close_fee_collateral="0.00003",
        entry_index_usd="70000",
        close_index_usd="70000",
        realized_pnl_collateral_native="0.01",
    )
    assert native_profit_for_group(group) is None


def test_remaining_spot_profit_native_unswept() -> None:
    group = _group()
    assert remaining_spot_profit_native(group) == Decimal("0.001")
    assert to_sweep_native(group) == Decimal("0.001")


def test_remaining_spot_profit_native_filled_partial() -> None:
    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.000914",
        profit_sweep_quote_proceeds="95.5",
    )
    assert remaining_spot_profit_native(group) == Decimal("0.000086")
    assert to_sweep_native(group) == Decimal("0.000086")


def test_to_sweep_native_pending_full_queue() -> None:
    group = _group(
        profit_sweep_status="pending",
        profit_sweep_amount="0.001",
    )
    assert remaining_spot_profit_native(group) == Decimal("0")
    assert to_sweep_native(group) == Decimal("0.001")


def test_exchange_remaining_native_pending_full_queue() -> None:
    from deribit_engine.profit_sweep_ops import _exchange_remaining_native

    group = _group(
        profit_sweep_status="pending",
        profit_sweep_amount="0.001",
        profit_sweep_reason="manual_sweep",
    )
    assert _exchange_remaining_native(None, group, "cc") == Decimal("0.001")


def test_list_remaining_profit_sweeps_excludes_fully_swept() -> None:
    rows = list_remaining_profit_sweeps(
        [
            _group(
                profit_sweep_status="filled",
                profit_sweep_amount="0.001",
                profit_sweep_quote_proceeds="95.5",
            )
        ]
    )
    assert rows == []


def test_list_remaining_profit_sweeps_includes_partial_remainder() -> None:
    rows = list_remaining_profit_sweeps([_group(profit_sweep_status="filled", profit_sweep_amount="0.000914")])
    assert len(rows) == 1
    assert rows[0].kind == "remainder"
    assert rows[0].to_sweep_native == Decimal("0.000086")


def test_reschedule_failed_profit_sweeps_resets_to_pending(tmp_path) -> None:
    from conftest import FakeClient, make_config

    from deribit_engine.engine import DeribitOptionTrialBot

    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_profit_sweep_enabled=True,
        traded_collaterals=("BTC", "ETH", "USDT"),
    )
    engine = DeribitOptionTrialBot(config, client)
    group = _group(
        profit_sweep_status="failed",
        profit_sweep_reason="take_profit",
        profit_sweep_amount="0.0003",
        profit_sweep_order_id="BTC_USDT-old",
    )

    assert reschedule_failed_profit_sweeps(engine, [group]) == 1
    assert group.profit_sweep_status == "pending"
    assert group.profit_sweep_amount == Decimal("0.001")


def test_reschedule_failed_profit_sweeps_ignores_skipped(tmp_path) -> None:
    from conftest import FakeClient, make_config

    from deribit_engine.engine import DeribitOptionTrialBot

    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        covered_call_profit_sweep_enabled=True,
    )
    engine = DeribitOptionTrialBot(config, client)
    group = _group(
        profit_sweep_status="skipped",
        profit_sweep_reason="amount_below_min_or_unavailable",
    )

    assert reschedule_failed_profit_sweeps(engine, [group]) == 0
    assert group.profit_sweep_status == "skipped"


def test_dry_run_does_not_persist_state(tmp_path) -> None:
    from conftest import FakeClient, make_config

    from deribit_engine.engine import DeribitOptionTrialBot
    from deribit_engine.models import StrategyState
    from deribit_engine.profit_sweep_ops import run_remaining_profit_sweeps

    client = FakeClient(btc_book_equity="0.5")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_profit_sweep_enabled=True,
        traded_collaterals=("BTC", "ETH", "USDT"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.0008",
        profit_sweep_quote_proceeds="60",
        realized_pnl_collateral_native="0.001",
    )
    state.groups.append(group)
    engine.state_store.save(state)

    summary = run_remaining_profit_sweeps(engine, live=False)

    assert summary.saved is False
    assert any(action.get("action") == "covered_call_profit_sweep_preview" for action in summary.actions)
    saved = engine.state_store.load().groups[0]
    assert saved.profit_sweep_status == "filled"
    assert saved.profit_sweep_amount == Decimal("0.0008")


def test_profit_sweep_trade_cache_fetches_once_per_currency() -> None:
    from unittest.mock import MagicMock

    from deribit_engine.profit_sweep_ops import ProfitSweepTradeCache

    client = MagicMock()
    client.get_user_trades_by_currency.side_effect = [
        {
            "trades": [
                {
                    "trade_id": "t1",
                    "label": "cc-profit-sweep-btc-0001",
                    "direction": "sell",
                    "amount": "0.001",
                    "timestamp": 1,
                },
                {
                    "trade_id": "t2",
                    "label": "cc-profit-sweep-btc-0002",
                    "direction": "sell",
                    "amount": "0.002",
                    "timestamp": 2,
                },
            ]
        },
        {"trades": []},
    ]
    cache = ProfitSweepTradeCache(client)
    g1 = _group(group_id="0001")
    g2 = _group(group_id="0002")
    assert len(cache.trades_for_group(g1, "cc")) == 1
    assert len(cache.trades_for_group(g2, "cc")) == 1
    assert client.get_user_trades_by_currency.call_count == 1
    cache.trades_for_group(_group(currency="ETH"), "cc")
    assert client.get_user_trades_by_currency.call_count == 2
    from unittest.mock import MagicMock

    group = _group(
        profit_sweep_status="pending",
        profit_sweep_amount="0.001",
    )
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-g1",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.001",
                "price": "90000",
                "timestamp": 1,
                "order_id": "o1",
            }
        ]
    }
    blocked = guard_profit_sweep_against_oversell(group, client, "cc")
    assert blocked is True
    assert group.profit_sweep_status == "filled"
    assert group.profit_sweep_amount == Decimal("0.001")
    assert list_remaining_profit_sweeps([group]) == []


def test_heal_reconciled_proceeds_drift_reconciles_labeled_pool() -> None:
    from unittest.mock import MagicMock, patch

    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.001",
        profit_sweep_quote_proceeds="45",
        realized_pnl_collateral_native="0.001",
        profit_sweep_reason="proceeds_reconciled",
        close_index_usd="90000",
        entry_index_usd="90000",
    )
    bot = MagicMock()
    bot.config.order_label_prefix = "cc"
    bot.client = MagicMock()

    from deribit_engine.profit_sweep_ops import heal_reconciled_proceeds_drift

    def _fake_reconcile(groups, client, order_label_prefix, *, apply, target_total_usdt):
        if apply:
            groups[0].profit_sweep_quote_proceeds = target_total_usdt
        return {"updated_groups": 1 if apply else 0}

    with (
        patch(
            "deribit_engine.trade_journal_backfill.repair_unlabeled_profit_sweeps_in_groups",
            return_value=0,
        ),
        patch(
            "deribit_engine.profit_sweep_repair.actual_premium_sweep_usdt_net",
            return_value=Decimal("90"),
        ),
        patch(
            "deribit_engine.profit_sweep_repair.reconcile_premium_proceeds_to_groups",
            side_effect=_fake_reconcile,
        ),
    ):
        assert heal_reconciled_proceeds_drift(bot, [group]) is True
    assert group.profit_sweep_quote_proceeds == Decimal("90")


def test_heal_reconciled_proceeds_drift_skips_when_unlabeled_matches_wallet() -> None:
    from unittest.mock import MagicMock, patch

    unlabeled = _group(
        group_id="0001",
        profit_sweep_status="filled",
        profit_sweep_amount="0.0002",
        profit_sweep_quote_proceeds="14.616",
        profit_sweep_quote_proceeds_lifetime="14.616",
        realized_pnl_collateral_native="0.0002",
        profit_sweep_reason="proceeds_reconciled; unlabeled_premium_reconciled",
        close_index_usd="73080",
        entry_index_usd="73080",
    )
    labeled = _group(
        group_id="0002",
        profit_sweep_status="filled",
        profit_sweep_amount="0.00011",
        profit_sweep_quote_proceeds="161.9496",
        profit_sweep_quote_proceeds_lifetime="161.9496",
        realized_pnl_collateral_native="0.00011",
        profit_sweep_reason="proceeds_reconciled",
        close_index_usd="90000",
        entry_index_usd="90000",
    )
    bot = MagicMock()
    bot.config.order_label_prefix = "cc"
    bot.client = MagicMock()

    from deribit_engine.profit_sweep_ops import heal_reconciled_proceeds_drift

    with (
        patch(
            "deribit_engine.trade_journal_backfill.repair_unlabeled_profit_sweeps_in_groups",
            return_value=0,
        ),
        patch(
            "deribit_engine.profit_sweep_repair.actual_premium_sweep_usdt_net",
            return_value=Decimal("161.9496"),
        ),
        patch(
            "deribit_engine.profit_sweep_repair.reconcile_premium_proceeds_to_groups",
        ) as reconcile,
    ):
        assert heal_reconciled_proceeds_drift(bot, [unlabeled, labeled]) is False
        reconcile.assert_not_called()


def test_guard_skips_locked_reconciled_groups() -> None:
    from unittest.mock import MagicMock

    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.0009",
        profit_sweep_quote_proceeds="90",
        realized_pnl_collateral_native="0.001",
        profit_sweep_reason="proceeds_reconciled; premium_amount_synced",
    )
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-g1",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.0005",
                "price": "90000",
                "timestamp": 1,
                "order_id": "o1",
            }
        ]
    }
    blocked = guard_profit_sweep_against_oversell(group, client, "cc")
    assert blocked is True
    assert group.profit_sweep_amount == Decimal("0.0009")
    assert group.profit_sweep_quote_proceeds == Decimal("90")
    client.get_user_trades_by_currency.assert_not_called()


def test_guard_allows_partial_exchange_remainder() -> None:
    from unittest.mock import MagicMock

    group = _group(
        profit_sweep_status="",
        profit_sweep_amount="0",
    )
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-g1",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.000914",
                "price": "90000",
                "timestamp": 1,
                "order_id": "o1",
            }
        ]
    }
    blocked = guard_profit_sweep_against_oversell(group, client, "cc")
    assert blocked is False
    assert group.profit_sweep_amount == Decimal("0.000914")
    assert exchange_swept_native_for_group(client, group, "cc") == Decimal("0.000914")
    rows = list_remaining_profit_sweeps([group])
    assert len(rows) == 1
    assert rows[0].to_sweep_native == Decimal("0.000086")


def test_reschedule_ledger_only_profit_sweeps_requeues_proceeds_reconciled() -> None:
    from unittest.mock import MagicMock

    from deribit_engine.profit_sweep_ops import (
        list_remaining_profit_sweeps,
        reschedule_ledger_only_profit_sweeps,
    )

    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.000406",
        profit_sweep_quote_proceeds="14.08540529",
        realized_pnl_collateral_native="0.000406",
        profit_sweep_reason="take_profit; proceeds_reconciled",
    )
    bot = MagicMock()
    bot.config.order_label_prefix = "cc"
    bot.client = MagicMock()
    bot.client.get_user_trades_by_currency.return_value = {"trades": []}

    assert reschedule_ledger_only_profit_sweeps(bot, [group]) == 1
    assert group.profit_sweep_status == "pending"
    assert group.profit_sweep_amount == Decimal("0.000406")
    assert group.profit_sweep_quote_proceeds == Decimal("0")
    assert "proceeds_reconciled" not in group.profit_sweep_reason
    rows = list_remaining_profit_sweeps([group])
    assert len(rows) == 1
    assert rows[0].to_sweep_native == Decimal("0.000406")


def test_refresh_profit_sweep_exchange_native_from_labeled_trades() -> None:
    from unittest.mock import MagicMock

    from deribit_engine.profit_sweep_ops import refresh_profit_sweep_exchange_native

    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.000526",
        profit_sweep_quote_proceeds="31.691",
        profit_sweep_order_id="BTC_USDT-8509193211",
        realized_pnl_collateral_native="0.000526",
        profit_sweep_reason="take_profit; premium_amount_synced; proceeds_reconciled",
    )
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-g1",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.0005",
                "price": "63382",
                "timestamp": 1,
                "order_id": "BTC_USDT-8509193211",
            }
        ]
    }
    assert refresh_profit_sweep_exchange_native(group, client, "cc") is True
    assert group.profit_sweep_exchange_native == Decimal("0.0005")
    assert group.profit_sweep_exchange_quote_proceeds == Decimal("31.691")
    assert refresh_profit_sweep_exchange_native(group, client, "cc") is False


def test_attributed_profit_sweep_ignores_later_resweep_day() -> None:
    from unittest.mock import MagicMock

    from deribit_engine.profit_sweep_ops import attributed_profit_sweep_fill_for_group

    group = _group(
        currency="ETH",
        collateral_currency="ETH",
        short_instrument_name="ETH-26JUN26-2000-C",
        profit_sweep_status="filled",
        profit_sweep_amount="0.00236",
        profit_sweep_quote_proceeds="3.77752",
        profit_sweep_order_id="ETH_USDT-8436355552",
        realized_pnl_collateral_native="0.00236",
        profit_sweep_reason="take_profit; exchange_fully_swept",
    )
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-eth-g1",
                "direction": "sell",
                "instrument_name": "ETH_USDT",
                "amount": "0.0023",
                "price": "1642.4",
                "timestamp": 1_781_028_380_462,
                "order_id": "ETH_USDT-8436355552",
            },
            {
                "trade_id": "t2",
                "label": "cc-profit-sweep-eth-g1",
                "direction": "sell",
                "instrument_name": "ETH_USDT",
                "amount": "0.0023",
                "price": "1637.3",
                "timestamp": 1_781_057_152_746,
                "order_id": "ETH_USDT-8439450948",
            },
        ]
    }
    native, quote = attributed_profit_sweep_fill_for_group(group, client, "cc")
    assert native == Decimal("0.0023")
    assert quote == Decimal("3.77752")


def test_guard_locked_group_still_records_exchange_native() -> None:
    from unittest.mock import MagicMock

    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.0009",
        profit_sweep_quote_proceeds="90",
        profit_sweep_order_id="BTC_USDT-o1",
        realized_pnl_collateral_native="0.001",
        profit_sweep_reason="proceeds_reconciled; premium_amount_synced",
    )
    client = MagicMock()
    client.get_user_trades_by_currency.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "label": "cc-profit-sweep-btc-g1",
                "direction": "sell",
                "instrument_name": "BTC_USDT",
                "amount": "0.0005",
                "price": "90000",
                "timestamp": 1,
                "order_id": "o1",
            }
        ]
    }
    blocked = guard_profit_sweep_against_oversell(group, client, "cc")
    assert blocked is True
    assert group.profit_sweep_exchange_native == Decimal("0.0005")
    assert group.profit_sweep_amount == Decimal("0.0009")
