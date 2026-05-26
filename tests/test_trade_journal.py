from __future__ import annotations

from decimal import Decimal

from deribit_engine.trade_journal import (
    TradeJournalStore,
    ingest_engine_action,
    journal_db_path_for_state,
    scope_key_for_state,
)


def test_trade_journal_records_open_and_close(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    db = journal_db_path_for_state(state_file)
    scope = scope_key_for_state(state_file)
    store = TradeJournalStore(db)

    open_action = {
        "action": "naked_put_entered",
        "group": {"group_id": "0001", "strategy": "naked_short"},
        "trades": {
            "short_leg": [
                {
                    "trade_id": "t-open-1",
                    "order_id": "o1",
                    "instrument_name": "BTC-28MAR25-80000-P",
                    "direction": "sell",
                    "amount": "1",
                    "price": "0.01",
                    "fee": "0.0001",
                    "fee_currency": "BTC",
                    "index_price": "70000",
                    "timestamp": 1000,
                }
            ]
        },
    }
    assert ingest_engine_action(store, scope_key=scope, action=open_action) == 1

    close_action = {
        "action": "close_group",
        "group_id": "0001",
        "reason": "take_profit",
        "trades": {
            "short_leg": [
                {
                    "trade_id": "t-close-1",
                    "order_id": "o2",
                    "instrument_name": "BTC-28MAR25-80000-P",
                    "direction": "buy",
                    "amount": "1",
                    "price": "0.004",
                    "fee": "0.5",
                    "fee_currency": "USDC",
                    "timestamp": 2000,
                }
            ]
        },
    }
    assert ingest_engine_action(store, scope_key=scope, action=close_action) == 1

    rows = store.list_executions(scope, limit=10)
    assert len(rows) == 2
    assert {row["event_type"] for row in rows} == {"open", "close"}

    # Dedupe same trade_id
    assert ingest_engine_action(store, scope_key=scope, action=open_action) == 0


def test_trade_group_stats_open_and_close(tmp_path):
    state_file = tmp_path / "bot.json"
    state_file.write_text("{}", encoding="utf-8")
    store = TradeJournalStore(journal_db_path_for_state(state_file))
    scope = scope_key_for_state(state_file)

    store.record_group_stats_open(
        scope_key=scope,
        group_id="0001",
        collateral_book="USDC",
        opened_ts_ms=1000,
        entry_book_equity=Decimal("10000"),
        entry_net_apr=Decimal("0.12"),
        entry_credit_usdc=Decimal("50"),
    )
    store.record_group_stats_close(
        scope_key=scope,
        group_id="0001",
        collateral_book="USDC",
        closed_ts_ms=2000,
        close_book_equity=Decimal("10050"),
        realized_pnl_usdc=Decimal("19.26"),
        realized_apr_on_equity=Decimal("0.25"),
        holding_days=Decimal("2.8332"),
    )
    row = store.get_group_stats(scope, "0001")
    assert row is not None
    assert row["entry_book_equity"] == "10000"
    assert row["realized_apr_on_equity"] == "0.25"
    assert row["close_book_equity"] == "10050"
