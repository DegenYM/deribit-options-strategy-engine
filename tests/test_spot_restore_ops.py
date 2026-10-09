from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from conftest import FakeClient, make_config

from deribit_engine.exceptions import ExchangeError
from deribit_engine.models import TradeGroup
from deribit_engine.spot_restore_ops import (
    SpotRestoreRunSummary,
    align_spot_restore_buy_amount,
    apply_spot_restore_quote_spent,
    execute_spot_restore_for_group,
    format_spot_restore_human_report,
    is_spot_restore_label,
    itm_spot_exit_net_usdt_for_total_profit,
    itm_spot_round_trip_complete,
    list_spot_restore_candidates,
    plan_spot_restore_to_cover,
    reconcile_spot_restore_from_exchange,
    resolve_spot_restore_order_size,
    spot_restore_fill_stats_for_currency,
    spot_restore_group_id_from_label,
    spot_restore_order_label,
    spot_restore_spot_instrument_name,
    unrestored_spot_exit_native,
)
from deribit_engine.utils import align_option_order_amount, ceil_to_step
from deribit_engine.wallet_ops import spot_buy_quote_spent_from_trades

ETH_ASK = Decimal("3500")
ETH_TICK = Decimal("0.05")


def _eth_ioc_limit(ask: Decimal = ETH_ASK) -> Decimal:
    return ceil_to_step(ask * Decimal("1.005"), ETH_TICK)


def _eth_group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0071",
        "currency": "ETH",
        "short_instrument_name": "ETH-28MAR25-4000-C",
        "short_label": "cc-eth-0071",
        "covered_underlying_quantity": "1",
        "quantity": "1",
        "spot_exit_amount": "1",
        "spot_exit_settlement_loss": "0",
        "short_entry_average_price": "0",
        "entry_fee_collateral": "0",
        "spot_exit_quote_proceeds": "3500",
        "spot_exit_quote_proceeds_lifetime": "3500",
    }
    payload.update(overrides)
    return _group(**payload)


def _restore_bot(tmp_path, client: FakeClient):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        order_label_prefix="covered_call",
        managed_currencies=("ETH", "BTC"),
    )
    bot = MagicMock()
    bot.client = client
    bot.config = config
    return bot


def _set_quote_balances(
    client: FakeClient,
    *,
    usdc: str,
    usdt: str | None = None,
    equity: str = "1000000",
    withdrawal: str = "0",
) -> None:
    """Equity is margin-locked. Free quote is available_funds, not balance."""

    def row(currency: str, available: str) -> dict:
        return {
            "currency": currency,
            "balance": equity,
            "equity": equity,
            "available_funds": available,
            "available_withdrawal_funds": withdrawal,
            "initial_margin": "0",
            "maintenance_margin": "0",
            "delta_total": "0",
            "options_delta": "0",
            "options_gamma": "0",
            "options_theta": "0",
        }

    rows = [row("USDC", usdc)]
    if usdt is not None:
        rows.append(row("USDT", usdt))
    client.get_account_summaries = lambda extended=False: rows


def _emergency_eth(bot, group: TradeGroup, **kwargs):
    return execute_spot_restore_for_group(
        bot,
        group,
        live=True,
        order_type="market",
        restore_reason="emergency_spot_restore",
        instrument_name="ETH_USDC",
        **kwargs,
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
        "spot_exit_quote_proceeds": "9000",
        "spot_exit_quote_proceeds_lifetime": "9000",
        "spot_exit_order_id": "spot-exit-order",
        "spot_exit_reason": "covered_call_settlement_exit",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_align_spot_restore_buy_amount_uses_spot_grid_not_option_lot() -> None:
    kwargs = dict(spot_contract_size=Decimal("0.0001"), spot_min_trade=Decimal("0.0001"), cap=Decimal("0.1"))
    # Ceil to the spot step only; a BTC 0.01 option lot must not inflate 0.0997 → 0.1.
    assert align_spot_restore_buy_amount(amount=Decimal("0.09978985"), **kwargs) == Decimal("0.0998")
    assert align_spot_restore_buy_amount(amount=Decimal("0.04"), **kwargs) == Decimal("0.04")
    assert align_spot_restore_buy_amount(amount=Decimal("0.00005"), **kwargs) == Decimal("0")
    # Never past the cover cap.
    assert align_spot_restore_buy_amount(amount=Decimal("0.12"), **kwargs) == Decimal("0.1")


def test_align_spot_restore_buy_amount_eth_tail_stays_small() -> None:
    """eugene #0020 / an #0048 tails (2026-10-09) must not become a 0.1 ETH order."""
    kwargs = dict(spot_contract_size=Decimal("0.0001"), spot_min_trade=Decimal("0.0001"), cap=Decimal("1"))
    assert align_spot_restore_buy_amount(amount=Decimal("0.00508756"), **kwargs) == Decimal("0.0051")
    assert align_spot_restore_buy_amount(amount=Decimal("0.00583848"), **kwargs) == Decimal("0.0059")


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


def test_csp_child_assignment_restore_counts_toward_parent_cover() -> None:
    parent = _group(
        group_id="0021",
        currency="ETH",
        short_instrument_name="ETH-28AUG26-2400-C",
        short_label="cc-eth-0021",
        quantity="1",
        covered_underlying_quantity="1",
        short_strike="2400",
        spot_exit_amount="0.9608",
        spot_exit_quote_proceeds="2399.43",
        spot_exit_quote_proceeds_lifetime="2399.43",
        spot_exit_settlement_loss="0.0392",
        short_entry_average_price="0.00253",
        entry_fee_collateral="0.00027",
        collateral_currency="ETH",
        spot_restore_status="skipped",
        cash_secured_group_id="0026",
        cash_secured_group_ids=["0026"],
    )
    child = TradeGroup.from_dict(
        {
            "group_id": "0026",
            "currency": "ETH",
            "short_instrument_name": "ETH_USDC-11SEP26-2400-P",
            "short_label": "csp-eth-0026",
            "status": "closed",
            "strategy": "cash_secured",
            "option_type": "put",
            "collateral_currency": "USDC",
            "quantity": "1",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "closed_timestamp_ms": 1_746_000_000_000,
            "short_strike": "2400",
            "entry_credit": "4.69",
            "original_entry_credit": "4.69",
            "max_loss": "2400",
            "regime_at_entry": "normal",
            "cash_secured_from_group_id": "0021",
            "spot_restore_status": "filled",
            "spot_restore_reason": "cash_secured_itm_assignment",
            "spot_restore_amount": "1",
            "spot_restore_quote_spent": "2405.859",
            "spot_restore_quote_spent_lifetime": "2405.859",
        }
    )
    groups = [parent, child]
    assert unrestored_spot_exit_native(parent) > Decimal("0.9")
    assert unrestored_spot_exit_native(parent, groups=groups) == Decimal("0")
    assert itm_spot_round_trip_complete(parent) is False
    assert itm_spot_round_trip_complete(parent, groups) is True
    net = itm_spot_exit_net_usdt_for_total_profit(parent, groups)
    assert net is not None
    assert abs(net - (Decimal("2399.43") - Decimal("2405.859"))) < Decimal("0.01")
    rows = list_spot_restore_candidates(groups)
    parent_row = next(row for row in rows if row.group_id == "0021")
    assert parent_row.unrestored_amount == Decimal("0")
    assert parent_row.restored_amount == Decimal("1")
    from deribit_engine.cash_secured_ops import cash_secured_target_native

    assert cash_secured_target_native(parent, groups) == Decimal("0")


def test_wheel_restore_ignores_child_overbuy_after_parent_filled() -> None:
    from deribit_engine.spot_restore_ops import (
        itm_spot_exit_net_usdt_for_total_profit,
        wheel_spot_restore_filled_native,
        wheel_spot_restore_realized_usdt,
    )

    parent = _group(
        group_id="0022",
        currency="BTC",
        quantity="0.1",
        covered_underlying_quantity="0.1",
        spot_exit_amount="0.0965",
        spot_exit_quote_proceeds="7688.8399",
        spot_exit_quote_proceeds_lifetime="7688.8399",
        spot_exit_settlement_loss="0.00340308",
        spot_restore_status="filled",
        spot_restore_amount="0.1",
        spot_restore_instrument_name="BTC_USDC",
        spot_restore_quote_spent="7856.6957",
        spot_restore_quote_spent_lifetime="7856.6957",
        cash_secured_group_id="0033",
        cash_secured_group_ids=["0033"],
        short_entry_average_price="0.0014",
        entry_fee_collateral="0.00001575",
    )
    child = TradeGroup.from_dict(
        {
            "group_id": "0033",
            "currency": "BTC",
            "status": "closed",
            "strategy": "cash_secured",
            "option_type": "put",
            "collateral_currency": "USDC",
            "quantity": "0.02",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "closed_timestamp_ms": 1_746_000_000_000,
            "short_strike": "77000",
            "entry_credit": "5.97",
            "original_entry_credit": "5.97",
            "max_loss": "1540",
            "regime_at_entry": "normal",
            "cash_secured_from_group_id": "0022",
            "spot_restore_status": "filled",
            "spot_restore_reason": "cash_secured_itm_assignment",
            "spot_restore_amount": "0.02",
            "spot_restore_quote_spent": "1539.26",
            "spot_restore_quote_spent_lifetime": "1539.26",
        }
    )
    groups = [parent, child]
    assert wheel_spot_restore_filled_native(parent, groups) == Decimal("0.1")
    assert wheel_spot_restore_realized_usdt(parent, groups) == Decimal("7856.6957")
    net = itm_spot_exit_net_usdt_for_total_profit(parent, groups)
    assert net == Decimal("7688.8399") - Decimal("7856.6957")


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


def test_spot_restore_follows_usdc_csp_after_usdt_exit() -> None:
    parent = _group(
        spot_exit_instrument_name="BTC_USDT",
        spot_restore_instrument_name="BTC_USDT",
        cash_secured_status="entered",
        cash_secured_group_id="0098",
        cash_secured_instrument_name="BTC_USDC-11SEP26-77000-P",
    )
    child = TradeGroup.from_dict(
        {
            "group_id": "0098",
            "currency": "BTC",
            "status": "open",
            "strategy": "cash_secured",
            "option_type": "put",
            "collateral_currency": "USDC",
            "quantity": "0.1",
            "short_instrument_name": "BTC_USDC-11SEP26-77000-P",
            "short_strike": "77000",
            "cash_secured_from_group_id": "0017",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "entry_credit": "5",
            "max_loss": "7700",
            "regime_at_entry": "normal",
        }
    )
    assert spot_restore_spot_instrument_name(parent) == "BTC_USDC"
    assert spot_restore_spot_instrument_name(parent, [parent, child], child=child) == "BTC_USDC"
    usdt_only = _group(spot_exit_instrument_name="BTC_USDT")
    assert spot_restore_spot_instrument_name(usdt_only) == "BTC_USDT"


def test_spot_restore_follows_usdc_exit_pair() -> None:
    parked = _group(
        spot_exit_instrument_name="BTC_USDC",
        spot_restore_instrument_name="BTC_USDT",
    )
    assert spot_restore_spot_instrument_name(parked) == "BTC_USDC"
    usdc_group = _group(
        spot_exit_instrument_name="BTC_USDC",
        spot_restore_instrument_name="BTC_USDC",
    )
    trades = [
        {
            "direction": "buy",
            "instrument_name": "BTC_USDC",
            "amount": "0.1",
            "price": "91000",
            "fee": "1",
            "fee_currency": "USDC",
        }
    ]
    spent = apply_spot_restore_quote_spent(usdc_group, trades)
    assert spent == Decimal("9101")


def test_execute_spot_restore_instrument_override_buys_usdc(tmp_path) -> None:
    group = _group(spot_exit_instrument_name="BTC_USDT")
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
    preview = execute_spot_restore_for_group(bot, group, live=False, instrument_name="BTC_USDC")
    assert preview["action"] == "spot_restore_preview"
    assert preview["instrument_name"] == "BTC_USDC"
    skipped = execute_spot_restore_for_group(bot, group, live=False, instrument_name="BTC_USD")
    assert skipped["reason"] == "invalid_spot_instrument"


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
    assert "0017" in stats["by_group"]
    assert Decimal(stats["by_group"]["0017"]["native_bought"]) == Decimal("0.1")
    assert Decimal(stats["by_group"]["0017"]["usdt_spent"]) == Decimal("9100")


def test_spot_restore_group_id_from_label() -> None:
    assert is_spot_restore_label("covered_call-spread-btc-0095-short-spot-restore")
    assert spot_restore_group_id_from_label("covered_call-spread-btc-0095-short-spot-restore") == "0095"
    assert spot_restore_group_id_from_label("cc-btc-0017-spot-restore") == "0017"
    assert spot_restore_group_id_from_label("covered_call-spot-restore-btc-0095") == "0095"
    assert spot_restore_group_id_from_label("covered_call-csp-restore-btc-0095") == "0095"
    assert not is_spot_restore_label("covered_call-spread-btc-0104-short")
    assert spot_restore_group_id_from_label("covered_call-spread-btc-0104-short") is None
    assert is_spot_restore_label("covered_call-csp-btc-0099-abort-restore")
    assert spot_restore_group_id_from_label("covered_call-csp-btc-0099-abort-restore") == "0099"
    assert not is_spot_restore_label("covered_call-csp-premium-swap-btc-0103")
    assert not is_spot_restore_label("cc-profit-sweep-buyback-btc")


def test_spot_restore_fill_stats_groups_parent_not_leftover_cc() -> None:
    client = MagicMock()
    trades = {
        "trades": [
            {
                "trade_id": "r95",
                "label": "covered_call-spread-btc-0095-short-spot-restore",
                "direction": "buy",
                "instrument_name": "BTC_USDC",
                "amount": "0.1",
                "price": "76824.581",
                "timestamp": 1_789_044_925_468,
            },
            {
                "trade_id": "cc104",
                "label": "covered_call-spread-btc-0104-short",
                "direction": "buy",
                "instrument_name": "BTC_USDC",
                "amount": "0.1",
                "price": "77000",
                "timestamp": 1_789_045_050_700,
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
    assert list(stats["by_group"]) == ["0095"]
    assert Decimal(stats["by_group"]["0095"]["usdt_spent"]) == Decimal("7682.4581")


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
    quote_for_full = unrestored * price * Decimal("1.001")

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
    # Spot grid only (BTC 0.0001); no ceil to the 0.01 USDC linear option lot.
    assert preview["buy_amount"] == "0.0943"
    assert preview["spot_min_trade_amount"] == "0.0001"
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
    assert "自動買回:" in text
    assert "買回上限:" in text
    assert preview["auto_would_buy"] is True
    assert Decimal(preview["auto_max_buy_price"]) > 0
    assert Decimal(preview["auto_ask"]) == Decimal("70000")


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
    assert preview["buy_amount"] == "0.0943"
    assert preview["estimated_usdt"] == "6601"
    assert preview["order_budget_usdt"] == "6607.601"
    assert "1.001" in preview["order_budget_usdt_meaning"]
    assert "native buy_amount" in preview["order_budget_usdt_meaning"]


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


def test_execute_spot_restore_live_market_buys_native_target(tmp_path) -> None:
    group = _group(
        spot_exit_amount="0.1",
        spot_exit_settlement_loss="0",
        short_entry_average_price="0",
        quantity="1",
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

    result = execute_spot_restore_for_group(bot, group, live=True, order_type="market")
    assert result["action"] == "spot_restore"
    assert result["order_type"] == "market"
    assert Decimal(result["filled_native"]) == Decimal("0.1")
    assert group.spot_restore_amount == Decimal("0.1")
    assert client.placed_orders
    assert client.placed_orders[0]["order_type"] == "market"
    assert Decimal(str(client.placed_orders[0]["amount"])) == Decimal("0.1")


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
    assert preview["buy_amount"] == "0.04"  # 0.03 swap + 0.01 settle; already on 0.01 lot
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


def test_execute_spot_restore_buy_amount_uses_spot_grid(tmp_path) -> None:
    group = _group(
        group_id="0095",
        short_instrument_name="BTC-28AUG26-73000-C",
        short_label="cc-btc-0095",
        short_strike="73000",
        spot_exit_amount="0.0915",
        spot_exit_quote_proceeds="7294.5676",
        spot_exit_quote_proceeds_lifetime="7294.5676",
        spot_exit_settlement_loss="0.0084211",
        short_entry_average_price="0.0015",
        entry_fee_collateral="0.00001875",
    )
    # 0.0915 + 0.0084211 − 0.00013125 = 0.09978985 → ceil to the BTC spot step 0.0001 = 0.0998
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

    preview = execute_spot_restore_for_group(bot, group, live=False, park_resting=True)
    assert preview["action"] == "spot_restore_preview"
    assert Decimal(preview["buy_amount"]) == Decimal("0.0998")
    assert "option_lot" not in preview
    assert Decimal(preview["restore_target"]) == Decimal("0.09978985")


def test_emergency_restore_full_cover_is_ioc_limit_not_market(tmp_path) -> None:
    """Restore 0.91 ETH on the spot grid (no option-lot ceil to 1); large USDC pays it in full."""
    group = _eth_group(spot_exit_amount="0.91", covered_underlying_quantity="1", quantity="1")
    client = FakeClient()
    _set_quote_balances(client, usdc="100000", usdt="100000")
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    limit = _eth_ioc_limit()
    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert order["order_type"] == "limit"
    assert order["time_in_force"] == "immediate_or_cancel"
    assert order["instrument_name"] == "ETH_USDC"
    assert Decimal(str(order["price"])) == limit
    assert Decimal(str(order["price"])) > ETH_ASK
    assert Decimal(str(order["amount"])) == Decimal("0.91")
    assert result["action"] == "spot_restore"
    assert result["order_type"] == "limit"
    assert result["time_in_force"] == "immediate_or_cancel"
    assert result["spot_restore_status"] == "filled"
    assert group.spot_restore_status == "filled"
    assert group.spot_restore_amount == Decimal("0.91")
    assert group.spot_restore_instrument_name == "ETH_USDC"
    assert group.spot_restore_quote_spent > 0
    assert "capped_to_free_quote" not in result


def test_emergency_restore_full_size_when_usdc_covers_ask_not_a_market_collar(tmp_path) -> None:
    """EUGENE: free USDC covers target × IOC limit and is far below a 2× collar."""
    group = _eth_group()
    client = FakeClient()
    limit = _eth_ioc_limit()
    needed = limit * Decimal("1")
    free = needed / Decimal("0.995") + Decimal("0.01")
    assert free * Decimal("0.995") >= needed
    assert free < needed * Decimal("2")
    _set_quote_balances(client, usdc=format(free, "f"), equity="1000000", withdrawal="0")
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert order["order_type"] == "limit"
    assert order["time_in_force"] == "immediate_or_cancel"
    assert Decimal(str(order["price"])) == limit
    assert Decimal(str(order["amount"])) == Decimal("1")
    assert result["action"] == "spot_restore"
    assert group.spot_restore_status == "filled"
    assert group.spot_restore_amount == Decimal("1")
    assert group.spot_restore_instrument_name == "ETH_USDC"


def test_emergency_restore_caps_to_free_quote_and_stays_on_planned_pair(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    limit = _eth_ioc_limit()
    free = Decimal("1800")
    _set_quote_balances(client, usdc=str(free), usdt="100000", equity="1000000", withdrawal="0")
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    budget = free * Decimal("0.995")
    expected = align_option_order_amount(budget / limit, Decimal("0.001"), Decimal("0.001"))
    assert Decimal("0") < expected < Decimal("1")
    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert order["instrument_name"] == "ETH_USDC"
    assert order["order_type"] == "limit"
    assert order["time_in_force"] == "immediate_or_cancel"
    assert Decimal(str(order["amount"])) == expected
    assert Decimal(str(order["amount"])) * limit <= budget
    assert result["action"] == "spot_restore"
    assert result.get("capped_to_free_quote") is True
    assert "capped to free" in str(result.get("message") or "")
    assert group.spot_restore_status == "filled"
    assert group.spot_restore_amount == expected
    assert group.spot_restore_instrument_name == "ETH_USDC"
    assert unrestored_spot_exit_native(group) > 0


def test_emergency_restore_uses_usdt_only_when_usdc_cannot_fund_min(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    _set_quote_balances(client, usdc="1", usdt="100000", equity="1000000")
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert order["instrument_name"] == "ETH_USDT"
    assert order["order_type"] == "limit"
    assert order["time_in_force"] == "immediate_or_cancel"
    assert Decimal(str(order["price"])) == _eth_ioc_limit()
    assert Decimal(str(order["amount"])) == Decimal("1")
    assert result["action"] == "spot_restore"
    assert group.spot_restore_status == "filled"
    assert group.spot_restore_instrument_name == "ETH_USDT"
    assert group.spot_restore_amount == Decimal("1")


def test_emergency_restore_skips_when_neither_quote_funds_min_lot(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    _set_quote_balances(client, usdc="1", usdt="1", equity="1000000")
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    assert client.placed_orders == []
    assert result["action"] == "spot_restore_skipped"
    assert result["reason"] == "not_enough_funds"
    message = str(result.get("message") or "")
    assert "not_enough_funds" not in message or "minimum" in message
    assert "ETH_USDC" in message
    assert "ETH_USDT" in message
    assert "0.001" in message
    assert "3517.5" in message
    assert "needed notional" in message
    assert "1" in message
    assert group.spot_restore_status != "filled"
    assert "jsonrpc" not in message


def test_emergency_restore_exchange_reject_tries_other_pair_once(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    _set_quote_balances(client, usdc="100000", usdt="100000")
    original = client.place_buy_order

    def wrapped(**kwargs):
        if kwargs.get("instrument_name") == "ETH_USDC":
            raise ExchangeError(
                'private/buy failed: HTTP 400 {"jsonrpc":"2.0","error":{"code":10039,'
                '"message":"not_enough_funds_in_currency"},"testnet":false}'
            )
        return original(**kwargs)

    client.place_buy_order = wrapped
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    assert len(client.placed_orders) == 1
    assert client.placed_orders[0]["instrument_name"] == "ETH_USDT"
    assert client.placed_orders[0]["order_type"] == "limit"
    assert Decimal(str(client.placed_orders[0]["amount"])) == Decimal("1")
    assert result["action"] == "spot_restore"
    assert group.spot_restore_instrument_name == "ETH_USDT"
    assert "jsonrpc" not in str(result.get("message") or "")


def test_emergency_restore_exchange_reject_without_fallback_is_skipped(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    _set_quote_balances(client, usdc="100000", usdt="1")

    def wrapped(**kwargs):
        raise ExchangeError('private/buy failed: HTTP 400 {"error":{"message":"not_enough_funds_in_currency"}}')

    client.place_buy_order = wrapped
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    assert client.placed_orders == []
    assert result["action"] == "spot_restore_skipped"
    assert result["reason"] == "not_enough_funds"
    message = str(result["message"])
    assert "not_enough_funds" in message
    assert "ETH_USDC" in message
    assert "0.001" in message
    assert "jsonrpc" not in message
    assert group.spot_restore_status != "filled"


def test_emergency_restore_mark_fallback_when_ask_missing(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    client.order_book_overrides["ETH_USDC"] = {
        "instrument_name": "ETH_USDC",
        "best_bid_price": "3490",
        "best_bid_amount": "1",
        "best_ask_price": "0",
        "best_ask_amount": "0",
        "mark_price": "3500",
        "index_price": "3500",
    }
    _set_quote_balances(client, usdc="100000")
    bot = _restore_bot(tmp_path, client)

    result = _emergency_eth(bot, group)

    assert Decimal(str(client.placed_orders[0]["price"])) == _eth_ioc_limit()
    assert result["price_source"] == "mark"
    assert result["action"] == "spot_restore"


def test_emergency_restore_other_exchange_errors_still_raise(tmp_path) -> None:
    group = _eth_group()
    client = FakeClient()
    _set_quote_balances(client, usdc="100000")

    def wrapped(**kwargs):
        raise ExchangeError("price_too_high 4000")

    client.place_buy_order = wrapped
    bot = _restore_bot(tmp_path, client)

    with pytest.raises(ExchangeError, match="price_too_high"):
        _emergency_eth(bot, group)
