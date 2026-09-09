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


def _seed_fills(store: TradeJournalStore, scope: str, group_id: str, count: int, *, base_ts: int = 1_000_000) -> None:
    for i in range(count):
        store.record_fill(
            scope_key=scope,
            event_type="close",
            source_action="test",
            instrument_name="BTC-28MAR25-80000-P",
            direction="buy",
            amount=Decimal("1"),
            price=Decimal(str(10 + i)),
            group_id=group_id,
            trade_id=f"{group_id}-{i}",
            ts_ms=base_ts + i,
        )


def test_list_executions_by_groups_buckets_caps_and_matches_single_reader(tmp_path, monkeypatch):
    from deribit_engine import trade_journal

    store = TradeJournalStore(tmp_path / "journal.sqlite3")
    scope = "acct"
    _seed_fills(store, scope, "g1", 7)
    _seed_fills(store, scope, "g2", 3)
    # Same timestamps for g3: tie-break must be id DESC (insertion order reversed).
    for i in range(4):
        store.record_fill(
            scope_key=scope,
            event_type="close",
            source_action="test",
            instrument_name="BTC-28MAR25-80000-P",
            direction="buy",
            amount=Decimal("1"),
            price=Decimal(str(i)),
            group_id="g3",
            trade_id=f"g3-{i}",
            ts_ms=5,
        )
    # Another scope with the same group id must not leak in.
    _seed_fills(store, "other", "g1", 2, base_ts=9_000_000)

    out = store.list_executions_by_groups(scope, ["g1", "g2", "g3", "missing", "", "g1"], per_group_limit=5)

    assert set(out) == {"g1", "g2", "g3", "missing", ""}
    assert out["missing"] == [] and out[""] == []
    assert len(out["g1"]) == 5  # capped
    assert [row["trade_id"] for row in out["g1"]] == [f"g1-{i}" for i in range(6, 1, -1)]
    assert len(out["g2"]) == 3
    assert [row["trade_id"] for row in out["g3"]] == ["g3-3", "g3-2", "g3-1", "g3-0"]
    for gid in ("g1", "g2", "g3"):
        assert out[gid] == store.list_executions(scope, group_id=gid, limit=5)
    assert all(row["group_id"] == "g1" for row in out["g1"])

    # Empty / falsy input short-circuits without touching the database.
    assert store.list_executions_by_groups(scope, []) == {}
    assert store.list_executions_by_groups(scope, [""]) == {"": []}

    # Chunking: more ids than one IN() chunk still returns every bucket.
    monkeypatch.setattr(trade_journal, "EXECUTIONS_IN_CLAUSE_CHUNK", 2)
    many = store.list_executions_by_groups(scope, ["g1", "g2", "g3", "x", "y"], per_group_limit=1)
    assert {k: len(v) for k, v in many.items()} == {"g1": 1, "g2": 1, "g3": 1, "x": 0, "y": 0}
    assert many["g1"][0]["trade_id"] == "g1-6"
