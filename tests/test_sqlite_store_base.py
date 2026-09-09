"""SqliteStoreBase plumbing + the new purge_older_than methods (FIX 4)."""

from __future__ import annotations

import sqlite3
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.fee_snapshot_store import FeeSnapshotStore
from deribit_engine.market_snapshot_store import MarketSnapshotStore
from deribit_engine.metrics_store import MetricsStore
from deribit_engine.models import TransactionEntry
from deribit_engine.portal_snapshot_store import PortalSnapshotStore
from deribit_engine.sqlite_store_base import SqliteStoreBase
from deribit_engine.trade_journal import TradeJournalStore
from deribit_engine.transfer_store import TransferStore


class _ToyStore(SqliteStoreBase):
    _schema = """
    CREATE TABLE IF NOT EXISTS toys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_ms INTEGER NOT NULL,
        name TEXT NOT NULL
    );
    """

    def _migrate(self, conn: sqlite3.Connection) -> None:
        self.added = self._ensure_columns("toys", {"colour": "TEXT", "name": "TEXT"}, conn=conn)

    def add(self, ts_ms: int, name: str) -> None:
        with self._transaction() as conn:
            conn.execute("INSERT INTO toys (ts_ms, name) VALUES (?, ?)", (ts_ms, name))


def test_base_creates_parent_dir_schema_and_pragmas(tmp_path: Path) -> None:
    store = _ToyStore(tmp_path / "nested" / "toys.db")
    assert store.db_path.exists()
    conn = store._connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
        cols = {row[1] for row in conn.execute("PRAGMA table_info(toys)")}
    finally:
        conn.close()
    assert cols == {"id", "ts_ms", "name", "colour"}
    # Only the missing column was added; re-opening is a no-op.
    assert store.added == ["colour"]
    assert _ToyStore(tmp_path / "nested" / "toys.db").added == []


def test_ensure_columns_without_conn_opens_its_own(tmp_path: Path) -> None:
    store = _ToyStore(tmp_path / "toys.db")
    assert store._ensure_columns("toys", {"size": "INTEGER DEFAULT 0"}) == ["size"]
    assert store._ensure_columns("toys", {"size": "INTEGER DEFAULT 0"}) == []


def test_transaction_commits_and_rolls_back(tmp_path: Path) -> None:
    store = _ToyStore(tmp_path / "toys.db")
    store.add(1, "a")
    with pytest.raises(RuntimeError):
        with store._transaction() as conn:
            conn.execute("INSERT INTO toys (ts_ms, name) VALUES (2, 'b')")
            raise RuntimeError("boom")
    assert store._table_row_count("toys") == 1
    assert store._delete_where("toys", "ts_ms < ?", (5,)) == 1
    assert store._table_row_count("toys") == 0


def test_transaction_serializes_threads(tmp_path: Path) -> None:
    store = _ToyStore(tmp_path / "toys.db")

    def _worker(offset: int) -> None:
        for i in range(20):
            store.add(offset + i, f"t{offset}")

    threads = [threading.Thread(target=_worker, args=(k * 100,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store._table_row_count("toys") == 80


def test_row_factory_default_and_override(tmp_path: Path) -> None:
    assert MarketSnapshotStore(tmp_path / "m.db").row_factory is sqlite3.Row
    assert PortalSnapshotStore(tmp_path / "p.db").row_factory is sqlite3.Row
    assert FeeSnapshotStore(tmp_path / "f.db").row_factory is sqlite3.Row
    assert TransferStore(tmp_path / "t.db").row_factory is sqlite3.Row
    assert MetricsStore(tmp_path / "me.db").row_factory is None
    assert TradeJournalStore(tmp_path / "tj.db").row_factory is None
    for cls in (
        MarketSnapshotStore,
        PortalSnapshotStore,
        FeeSnapshotStore,
        TransferStore,
        MetricsStore,
        TradeJournalStore,
    ):
        assert issubclass(cls, SqliteStoreBase)


def test_legacy_fee_db_gets_wallet_column_migrated(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE nav_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts_ms INTEGER NOT NULL, investor_id TEXT NOT NULL,
                snapshot_kind TEXT NOT NULL, total_equity_usdc TEXT NOT NULL, collateral_spot_usdc TEXT NOT NULL,
                nav_perf TEXT NOT NULL, aum_mgmt TEXT NOT NULL, index_btc_usd TEXT NOT NULL, index_eth_usd TEXT NOT NULL,
                collateral_spot_btc TEXT NOT NULL, collateral_spot_eth TEXT NOT NULL, equity_by_book_json TEXT NOT NULL,
                notes TEXT
            )
            """
        )
    FeeSnapshotStore(path)
    with sqlite3.connect(path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(nav_snapshots)")}
    assert "wallet_native_by_book_json" in cols


# ---- purge_older_than ------------------------------------------------------------


def _nav(store: FeeSnapshotStore, ts_ms: int, investor: str = "alice") -> int:
    one = Decimal("1")
    return store.append_snapshot(
        ts_ms=ts_ms,
        investor_id=investor,
        snapshot_kind="daily",
        total_equity_usdc=one,
        collateral_spot_usdc=one,
        nav_perf=one,
        aum_mgmt=one,
        index_btc_usd=one,
        index_eth_usd=one,
        collateral_spot_btc=one,
        collateral_spot_eth=one,
        equity_by_book={"USDC": one},
    )


def test_fee_snapshot_purge_only_touches_nav_snapshots(tmp_path: Path) -> None:
    store = FeeSnapshotStore(tmp_path / "f.db")
    _nav(store, 1_000)
    _nav(store, 2_000)
    _nav(store, 3_000, investor="bob")
    store.save_hwm(investor_id="alice", hwm_nav_perf=Decimal("1"), updated_at_ms=1)
    store.save_settlement(
        {
            "investor_id": "alice",
            "period": "2025Q1",
            "period_start_ms": 0,
            "period_end_ms": 1,
            "hwm_start": Decimal("1"),
            "nav_perf_start": Decimal("1"),
            "nav_perf_end": Decimal("1"),
            "net_flow_usdc": Decimal("0"),
            "distributable_profit": Decimal("0"),
            "performance_fee": Decimal("0"),
            "hwm_end": Decimal("1"),
            "avg_aum_mgmt": Decimal("1"),
            "management_fee": Decimal("0"),
            "settled_at_ms": 1,
        }
    )

    assert store.purge_older_than(cutoff_ms=2_500, investor_id="bob") == 0
    assert store.purge_older_than(cutoff_ms=2_500) == 2
    assert store.latest_snapshot("alice") is None
    assert store.latest_snapshot("bob") is not None
    assert store.load_hwm("alice") == Decimal("1")
    assert len(store.list_settlements("alice")) == 1


def _transfer(ts: int, tid: int) -> TransactionEntry:
    return TransactionEntry(
        id=tid, timestamp=ts, type="transfer", currency="USDC", amount=Decimal("1"), balance=None, info=""
    )


def test_transfer_store_purge_keeps_sync_meta(tmp_path: Path) -> None:
    store = TransferStore(tmp_path / "t.db")
    store.upsert_row("s1", "USDC", _transfer(1_000, 1))
    store.upsert_row("s1", "USDC", _transfer(5_000, 2))
    store.upsert_row("s2", "BTC", _transfer(1_000, 3))
    store.touch_sync_meta("s1", "USDC", synced_through_ms=5_000)

    assert store.purge_older_than(cutoff_ms=2_000, scope_key="s1", book="usdc") == 1
    assert store.row_count("s1", "USDC") == 1
    assert store.row_count("s2", "BTC") == 1
    assert store.purge_older_than(cutoff_ms=2_000) == 1
    assert store.row_count("s2", "BTC") == 0
    with sqlite3.connect(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM transfer_sync_meta").fetchone()[0] == 1


def test_trade_journal_purge_keeps_open_group_stats(tmp_path: Path) -> None:
    store = TradeJournalStore(tmp_path / "tj.db")
    for i, ts in enumerate((1_000, 2_000, 9_000)):
        store.record_fill(
            scope_key="scope",
            event_type="open",
            source_action="entered",
            instrument_name="BTC-1",
            direction="sell",
            amount=Decimal("1"),
            price=Decimal("0.01"),
            trade_id=f"t{i}",
            ts_ms=ts,
        )
    store.record_group_stats_open(
        scope_key="scope",
        group_id="open-g",
        collateral_book="USDC",
        opened_ts_ms=500,
        entry_book_equity=Decimal("1"),
        entry_net_apr=Decimal("0.1"),
        entry_credit_usdc=Decimal("1"),
    )
    store.record_group_stats_close(
        scope_key="scope",
        group_id="closed-g",
        collateral_book="USDC",
        closed_ts_ms=1_500,
        close_book_equity=Decimal("1"),
        realized_pnl_usdc=Decimal("1"),
        realized_apr_on_equity=Decimal("0.1"),
        holding_days=Decimal("1"),
    )

    assert store.purge_older_than(cutoff_ms=3_000, scope_key="other") == 0
    # 2 fills (1000, 2000) + 1 closed stats row.
    assert store.purge_older_than(cutoff_ms=3_000) == 3
    assert store.execution_count("scope") == 1
    assert store.get_group_stats("scope", "open-g") is not None
    assert store.get_group_stats("scope", "closed-g") is None
