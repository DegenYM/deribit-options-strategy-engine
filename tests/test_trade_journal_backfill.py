from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from deribit_demo.models import TradeGroup
from deribit_demo.trade_journal import TradeJournalStore, scope_key_for_state
from deribit_demo.trade_journal_backfill import (
    _backfill_group_from_state,
    _parse_bot_label,
    backfill_closed_group_stats_in_state,
)


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

    from deribit_demo.models import StrategyState
    from deribit_demo.state import StrategyStateStore

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
    from deribit_demo.models import StrategyState
    from deribit_demo.state import StrategyStateStore

    StrategyStateStore(state_file).save(StrategyState(groups=[group]))

    summary = backfill_closed_group_stats_in_state(state_file)
    assert summary.entry_apr_updated == 1
    assert summary.saved is True

    updated = StrategyStateStore(state_file).load().groups[0]
    assert updated.entry_net_apr < Decimal("0.25")
    assert updated.entry_net_apr > Decimal("0.15")
