from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from deribit_engine.models import TradeGroup
from deribit_engine.trade_journal import TradeJournalStore, scope_key_for_state
from deribit_engine.trade_journal_backfill import (
    _backfill_group_from_state,
    _parse_bot_label,
    backfill_closed_group_stats_in_state,
    infer_bot_income_exit_close_reason,
    reconcile_profit_sweep_from_exchange,
    repair_reconciled_bot_income_exit_group,
)


def test_infer_bot_income_exit_close_reason_from_journal():
    executions = [
        {
            "event_type": "open",
            "source_action": "backfill_state",
            "label": "covered_call-spread-btc-0048-short",
            "order_id": "",
        },
        {
            "event_type": "close",
            "source_action": "backfill_api",
            "label": "covered_call-spread-btc-0048-short-close",
            "order_id": "157671270848",
            "reason": "deribit_user_trades",
        },
        {
            "event_type": "close",
            "source_action": "reconcile_external",
            "label": "",
            "order_id": "",
            "reason": "reconciled_external",
        },
    ]
    assert infer_bot_income_exit_close_reason(executions, order_label_prefix="covered_call") == "take_profit"
    assert infer_bot_income_exit_close_reason(executions, order_label_prefix="naked_short") is None


def test_repair_reconciled_bot_income_exit_group(tmp_path):
    group = TradeGroup(
        group_id="0048",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        covered_underlying_quantity=Decimal("0.1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC-26JUN26-80000-C",
        short_strike=Decimal("80000"),
        entry_credit=Decimal("86"),
        original_entry_credit=Decimal("86"),
        max_loss=Decimal("0"),
        regime_at_entry="normal",
        short_entry_average_price=Decimal("0.012"),
        strategy="covered_call",
        option_type="call",
        status="closed",
        closed_timestamp_ms=3,
        close_reason="reconciled_external",
        realized_pnl=Decimal("32"),
    )
    executions = [
        {
            "event_type": "close",
            "source_action": "backfill_api",
            "leg": "short",
            "instrument_name": "BTC-26JUN26-80000-C",
            "direction": "buy",
            "amount": "0.1",
            "price": "0.0075",
            "label": "covered_call-spread-btc-0048-short-close",
            "order_id": "157671270848",
            "reason": "deribit_user_trades",
        },
    ]
    assert repair_reconciled_bot_income_exit_group(
        group,
        executions,
        order_label_prefix="covered_call",
        profit_sweep_enabled=True,
    )
    assert group.close_reason == "take_profit"
    assert group.short_close_average_price == Decimal("0.0075")
    assert group.profit_sweep_status == "pending"
    assert group.realized_pnl_collateral_native is not None
    assert group.realized_pnl_collateral_native > 0


def test_reconcile_profit_sweep_from_exchange_pending_to_filled():
    group = TradeGroup(
        group_id="0048",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        covered_underlying_quantity=Decimal("0.1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC-26JUN26-80000-C",
        short_strike=Decimal("80000"),
        entry_credit=Decimal("86"),
        original_entry_credit=Decimal("86"),
        max_loss=Decimal("0"),
        regime_at_entry="normal",
        short_entry_average_price=Decimal("0.012"),
        strategy="covered_call",
        option_type="call",
        status="closed",
        closed_timestamp_ms=3,
        close_reason="take_profit",
        profit_sweep_status="pending",
        profit_sweep_amount=Decimal("0.00039"),
        profit_sweep_reason="take_profit",
    )

    class SweepClient:
        def get_order_state_by_label(self, currency, label):
            assert label == "covered_call-profit-sweep-btc-0048"
            return [
                {
                    "label": label,
                    "order_id": "BTC_USDT-8336763035",
                    "instrument_name": "BTC_USDT",
                    "amount": Decimal("0.0003"),
                    "filled_amount": Decimal("0.0003"),
                    "average_price": Decimal("73015"),
                    "order_state": "filled",
                }
            ]

        def get_user_trades_by_order(self, order_id, *, historical=False):
            assert order_id == "BTC_USDT-8336763035"
            return [
                {
                    "direction": "sell",
                    "amount": Decimal("0.0003"),
                    "price": Decimal("73015"),
                    "fee": Decimal("0"),
                    "fee_currency": "USDT",
                }
            ]

    assert reconcile_profit_sweep_from_exchange(
        group,
        client=SweepClient(),
        order_label_prefix="covered_call",
    )
    assert group.profit_sweep_status == "filled"
    assert group.profit_sweep_amount == Decimal("0.0003")
    assert group.profit_sweep_order_id == "BTC_USDT-8336763035"
    assert group.profit_sweep_quote_proceeds == Decimal("21.9045")


def test_parse_bot_label():
    assert _parse_bot_label("naked_short-spread-btc-0001-short", label_prefix="naked_short") == (
        "0001",
        "short",
        "open",
    )
    assert _parse_bot_label("naked_short-spread-btc-0001-short-close", label_prefix="naked_short") == (
        "0001",
        "short",
        "close",
    )
    assert _parse_bot_label("other-spread-btc-0001-short", label_prefix="naked_short") is None


def test_backfill_group_from_state(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    store = TradeJournalStore(state_file.with_name("bot.trade_journal.db"))
    scope = scope_key_for_state(state_file)
    group = TradeGroup(
        group_id="0001",
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("1"),
        entry_timestamp_ms=int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000),
        expiration_timestamp_ms=int(datetime(2024, 2, 1, tzinfo=UTC).timestamp() * 1000),
        short_instrument_name="BTC_USDC-1FEB24-40000-P",
        short_strike=Decimal("40000"),
        entry_credit=Decimal("90"),
        original_entry_credit=Decimal("90"),
        max_loss=Decimal("500"),
        regime_at_entry="normal",
        entry_fee=Decimal("10"),
        status="closed",
        closed_timestamp_ms=int(datetime(2024, 1, 15, tzinfo=UTC).timestamp() * 1000),
        close_reason="take_profit",
        realized_close_debit=Decimal("40"),
        realized_pnl=Decimal("50"),
        short_label="pfx-spread-btc-0001-short",
        strategy="naked_short",
    )
    n = _backfill_group_from_state(store, scope_key=scope, group=group, skip_if_journal_exists=False)
    assert n == 2
    rows = store.list_executions(scope)
    assert len(rows) == 2
    assert {r["event_type"] for r in rows} == {"open", "close"}


def test_backfill_closed_group_stats_covered_call_fractional_apr(tmp_path: Path):
    state_file = tmp_path / "covered_call.json"
    journal = TradeJournalStore(state_file.with_name("covered_call.trade_journal.db"))
    scope = scope_key_for_state(state_file)

    entry_ms = 1_000_000
    closed_ms = entry_ms + int(Decimal("3") * 86_400_000)
    group = TradeGroup(
        group_id="0037",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        covered_underlying_quantity=Decimal("0.1"),
        entry_timestamp_ms=entry_ms,
        expiration_timestamp_ms=entry_ms + 21 * 86_400_000,
        short_instrument_name="BTC-5JUN26-85000-C",
        short_strike=Decimal("85000"),
        entry_credit=Decimal("81.24"),
        original_entry_credit=Decimal("81.24"),
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        entry_fee=Decimal("2.39"),
        status="closed",
        closed_timestamp_ms=closed_ms,
        close_reason="take_profit",
        realized_close_debit=Decimal("33.17"),
        realized_close_fee=Decimal("2.31"),
        realized_pnl=Decimal("48.06"),
        realized_annualized_return=Decimal("7.177"),
        strategy="covered_call",
        option_type="call",
    )
    journal.record_fill(
        scope_key=scope,
        event_type="open",
        source_action="test",
        instrument_name=group.short_instrument_name,
        direction="sell",
        amount=group.quantity,
        price=Decimal("0.0105"),
        fee_usdc=group.entry_fee,
        group_id=group.group_id,
        trade_id="t-open",
        ts_ms=entry_ms,
    )
    journal.record_fill(
        scope_key=scope,
        event_type="close",
        source_action="test",
        instrument_name=group.short_instrument_name,
        direction="buy",
        amount=group.quantity,
        price=Decimal("0.004"),
        fee_usdc=group.realized_close_fee,
        group_id=group.group_id,
        trade_id="t-close",
        ts_ms=closed_ms,
    )

    from deribit_engine.models import StrategyState
    from deribit_engine.state import StrategyStateStore

    StrategyStateStore(state_file).save(StrategyState(groups=[group]))

    summary = backfill_closed_group_stats_in_state(state_file)
    assert summary.apr_updated == 1
    assert summary.saved is True

    updated = StrategyStateStore(state_file).load().groups[0]
    assert updated.realized_pnl_collateral_native is not None
    assert updated.entry_index_usd > 0
    assert updated.close_index_usd is not None and updated.close_index_usd > 0
    assert updated.realized_apr_on_equity is not None
    assert updated.realized_apr_on_equity < Decimal("1")
    assert updated.realized_apr_on_equity > Decimal("0.5")
    assert updated.realized_annualized_return == updated.realized_apr_on_equity


def test_backfill_entry_net_apr_fractional_covered_call(tmp_path: Path):
    state_file = tmp_path / "covered_call.json"
    entry_ms = 1_000_000
    group = TradeGroup(
        group_id="0099",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        covered_underlying_quantity=Decimal("0.1"),
        entry_timestamp_ms=entry_ms,
        expiration_timestamp_ms=entry_ms + 20 * 86_400_000,
        short_instrument_name="BTC-5JUN26-85000-C",
        short_strike=Decimal("85000"),
        entry_credit=Decimal("78.85"),
        original_entry_credit=Decimal("78.85"),
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        entry_fee=Decimal("2.39"),
        entry_index_usd=Decimal("79647"),
        short_entry_average_price=Decimal("0.0102"),
        entry_net_apr=Decimal("1.74"),
        strategy="covered_call",
        option_type="call",
        status="closed",
        closed_timestamp_ms=entry_ms + 3 * 86_400_000,
        realized_pnl=Decimal("45"),
    )
    from deribit_engine.models import StrategyState
    from deribit_engine.state import StrategyStateStore

    StrategyStateStore(state_file).save(StrategyState(groups=[group]))

    summary = backfill_closed_group_stats_in_state(state_file)
    assert summary.entry_apr_updated == 1
    assert summary.saved is True

    updated = StrategyStateStore(state_file).load().groups[0]
    assert updated.entry_net_apr < Decimal("0.25")
    assert updated.entry_net_apr > Decimal("0.15")


def test_backfill_entry_net_apr_borrows_peer_instrument_fill(tmp_path: Path):
    """Legacy synthetic rows may store USDC/qty; borrow a plausible open fill for same instrument."""
    state_file = tmp_path / "covered_call.json"
    journal = TradeJournalStore(state_file.with_name("covered_call.trade_journal.db"))
    scope = scope_key_for_state(state_file)
    instrument = "BTC-22MAY26-85000-C"
    entry_ms = 1_700_000_000_000

    journal.record_fill(
        scope_key=scope,
        event_type="open",
        source_action="deribit_api",
        instrument_name=instrument,
        direction="sell",
        amount=Decimal("0.1"),
        price=Decimal("0.011"),
        fee_usdc=Decimal("2.43"),
        group_id="0028",
        trade_id="peer-open",
        ts_ms=entry_ms - 60_000,
        extra={"source": "deribit_api"},
    )
    journal.record_fill(
        scope_key=scope,
        event_type="open",
        source_action="backfill_state",
        instrument_name=instrument,
        direction="sell",
        amount=Decimal("0.1"),
        price=Decimal("891.071170"),
        fee_usdc=Decimal("2.43"),
        group_id="0031",
        trade_id="bad-open",
        ts_ms=entry_ms,
        extra={"synthetic": True, "source": "strategy_state"},
    )

    group = TradeGroup(
        group_id="0031",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        covered_underlying_quantity=Decimal("0.1"),
        entry_timestamp_ms=entry_ms,
        expiration_timestamp_ms=entry_ms + 15 * 86_400_000,
        short_instrument_name=instrument,
        short_strike=Decimal("85000"),
        entry_credit=Decimal("86.6769229"),
        original_entry_credit=Decimal("86.6769229"),
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        entry_fee=Decimal("2.4301941"),
        strategy="covered_call",
        option_type="call",
        status="closed",
        closed_timestamp_ms=entry_ms + 8 * 86_400_000,
        close_reason="reconciled_external",
        realized_close_debit=Decimal("46.8618134"),
        realized_pnl=Decimal("39.8151095"),
    )

    from deribit_engine.models import StrategyState
    from deribit_engine.state import StrategyStateStore

    StrategyStateStore(state_file).save(StrategyState(groups=[group]))

    summary = backfill_closed_group_stats_in_state(state_file)
    assert summary.entry_apr_updated == 1
    assert summary.saved is True

    updated = StrategyStateStore(state_file).load().groups[0]
    assert updated.entry_index_usd > Decimal("80000")
    assert updated.short_entry_average_price == Decimal("0.011")
    assert updated.entry_net_apr > Decimal("0.15")
