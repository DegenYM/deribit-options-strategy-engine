from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from deribit_demo.models import TradeGroup
from deribit_demo.trade_journal import TradeJournalStore, scope_key_for_state
from deribit_demo.trade_journal_backfill import (
    _backfill_group_from_state,
    _parse_bot_label,
    load_trade_groups,
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
