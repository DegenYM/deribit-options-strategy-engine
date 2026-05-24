"""SQLite persistence for investor NAV snapshots and quarterly fee settlements."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .utils import to_decimal

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nav_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    investor_id TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL,
    total_equity_usdc TEXT NOT NULL,
    collateral_spot_usdc TEXT NOT NULL,
    nav_perf TEXT NOT NULL,
    aum_mgmt TEXT NOT NULL,
    index_btc_usd TEXT NOT NULL,
    index_eth_usd TEXT NOT NULL,
    collateral_spot_btc TEXT NOT NULL,
    collateral_spot_eth TEXT NOT NULL,
    equity_by_book_json TEXT NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_nav_snapshots_investor_ts
    ON nav_snapshots (investor_id, ts_ms);

CREATE TABLE IF NOT EXISTS hwm_state (
    investor_id TEXT PRIMARY KEY,
    hwm_nav_perf TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    last_settlement_period TEXT
);

CREATE TABLE IF NOT EXISTS fee_settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investor_id TEXT NOT NULL,
    period TEXT NOT NULL,
    period_start_ms INTEGER NOT NULL,
    period_end_ms INTEGER NOT NULL,
    hwm_start TEXT NOT NULL,
    nav_perf_start TEXT NOT NULL,
    nav_perf_end TEXT NOT NULL,
    net_flow_usdc TEXT NOT NULL,
    distributable_profit TEXT NOT NULL,
    performance_fee TEXT NOT NULL,
    hwm_end TEXT NOT NULL,
    avg_aum_mgmt TEXT NOT NULL,
    management_fee TEXT NOT NULL,
    settled_at_ms INTEGER NOT NULL,
    UNIQUE (investor_id, period)
);

CREATE TABLE IF NOT EXISTS flow_baseline (
    investor_id TEXT PRIMARY KEY,
    cumulative_net_flow_usdc TEXT NOT NULL,
    initial_hwm_nav_perf TEXT NOT NULL,
    net_flow_native_by_book_json TEXT NOT NULL,
    start_timestamp_ms INTEGER NOT NULL,
    end_timestamp_ms INTEGER NOT NULL,
    entry_count INTEGER NOT NULL,
    bootstrapped_at_ms INTEGER NOT NULL,
    source TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class NavSnapshotRow:
    id: int
    ts_ms: int
    investor_id: str
    snapshot_kind: str
    total_equity_usdc: Decimal
    collateral_spot_usdc: Decimal
    nav_perf: Decimal
    aum_mgmt: Decimal
    index_btc_usd: Decimal
    index_eth_usd: Decimal
    collateral_spot_btc: Decimal
    collateral_spot_eth: Decimal
    equity_by_book: dict[str, Decimal]
    notes: str | None


@dataclass(frozen=True)
class FlowBaselineRow:
    investor_id: str
    cumulative_net_flow_usdc: Decimal
    initial_hwm_nav_perf: Decimal
    net_flow_native_by_book: dict[str, Decimal]
    start_timestamp_ms: int
    end_timestamp_ms: int
    entry_count: int
    bootstrapped_at_ms: int
    source: str


@dataclass(frozen=True)
class FeeSettlementRow:
    id: int
    investor_id: str
    period: str
    period_start_ms: int
    period_end_ms: int
    hwm_start: Decimal
    nav_perf_start: Decimal
    nav_perf_end: Decimal
    net_flow_usdc: Decimal
    distributable_profit: Decimal
    performance_fee: Decimal
    hwm_end: Decimal
    avg_aum_mgmt: Decimal
    management_fee: Decimal
    settled_at_ms: int


def fee_ledger_db_path(repo_root: Path, investor_id: str) -> Path:
    return repo_root / "data" / "fee_ledger" / investor_id / "snapshots.db"


class FeeSnapshotStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()

    def append_snapshot(
        self,
        *,
        ts_ms: int,
        investor_id: str,
        snapshot_kind: str,
        total_equity_usdc: Decimal,
        collateral_spot_usdc: Decimal,
        nav_perf: Decimal,
        aum_mgmt: Decimal,
        index_btc_usd: Decimal,
        index_eth_usd: Decimal,
        collateral_spot_btc: Decimal,
        collateral_spot_eth: Decimal,
        equity_by_book: dict[str, Decimal],
        notes: str | None = None,
    ) -> int:
        equity_json = json.dumps({k: str(v) for k, v in sorted(equity_by_book.items())})
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO nav_snapshots (
                        ts_ms, investor_id, snapshot_kind,
                        total_equity_usdc, collateral_spot_usdc, nav_perf, aum_mgmt,
                        index_btc_usd, index_eth_usd,
                        collateral_spot_btc, collateral_spot_eth,
                        equity_by_book_json, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts_ms,
                        investor_id,
                        snapshot_kind,
                        str(total_equity_usdc),
                        str(collateral_spot_usdc),
                        str(nav_perf),
                        str(aum_mgmt),
                        str(index_btc_usd),
                        str(index_eth_usd),
                        str(collateral_spot_btc),
                        str(collateral_spot_eth),
                        equity_json,
                        notes,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

    def load_hwm(self, investor_id: str) -> Decimal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT hwm_nav_perf FROM hwm_state WHERE investor_id = ?",
                (investor_id,),
            ).fetchone()
        if row is None:
            return None
        return to_decimal(row["hwm_nav_perf"])

    def save_hwm(
        self,
        *,
        investor_id: str,
        hwm_nav_perf: Decimal,
        updated_at_ms: int,
        last_settlement_period: str | None = None,
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO hwm_state (investor_id, hwm_nav_perf, updated_at_ms, last_settlement_period)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(investor_id) DO UPDATE SET
                        hwm_nav_perf = excluded.hwm_nav_perf,
                        updated_at_ms = excluded.updated_at_ms,
                        last_settlement_period = excluded.last_settlement_period
                    """,
                    (investor_id, str(hwm_nav_perf), updated_at_ms, last_settlement_period),
                )
                conn.commit()

    def load_flow_baseline(self, investor_id: str) -> FlowBaselineRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM flow_baseline WHERE investor_id = ?",
                (investor_id,),
            ).fetchone()
        return _row_to_flow_baseline(row) if row else None

    def save_flow_baseline(
        self,
        *,
        investor_id: str,
        cumulative_net_flow_usdc: Decimal,
        initial_hwm_nav_perf: Decimal,
        net_flow_native_by_book: dict[str, Decimal],
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        entry_count: int,
        bootstrapped_at_ms: int,
        source: str,
    ) -> None:
        flow_json = json.dumps({k: str(v) for k, v in sorted(net_flow_native_by_book.items())})
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO flow_baseline (
                        investor_id, cumulative_net_flow_usdc, initial_hwm_nav_perf,
                        net_flow_native_by_book_json, start_timestamp_ms, end_timestamp_ms,
                        entry_count, bootstrapped_at_ms, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(investor_id) DO UPDATE SET
                        cumulative_net_flow_usdc = excluded.cumulative_net_flow_usdc,
                        initial_hwm_nav_perf = excluded.initial_hwm_nav_perf,
                        net_flow_native_by_book_json = excluded.net_flow_native_by_book_json,
                        start_timestamp_ms = excluded.start_timestamp_ms,
                        end_timestamp_ms = excluded.end_timestamp_ms,
                        entry_count = excluded.entry_count,
                        bootstrapped_at_ms = excluded.bootstrapped_at_ms,
                        source = excluded.source
                    """,
                    (
                        investor_id,
                        str(cumulative_net_flow_usdc),
                        str(initial_hwm_nav_perf),
                        flow_json,
                        start_timestamp_ms,
                        end_timestamp_ms,
                        entry_count,
                        bootstrapped_at_ms,
                        source,
                    ),
                )
                conn.commit()

    def latest_snapshot(self, investor_id: str) -> NavSnapshotRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM nav_snapshots
                WHERE investor_id = ?
                ORDER BY ts_ms DESC, id DESC
                LIMIT 1
                """,
                (investor_id,),
            ).fetchone()
        return _row_to_snapshot(row) if row else None

    def latest_snapshot_before(
        self,
        investor_id: str,
        *,
        before_ts_ms: int,
    ) -> NavSnapshotRow | None:
        """Most recent snapshot strictly before ``before_ts_ms``."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM nav_snapshots
                WHERE investor_id = ? AND ts_ms < ?
                ORDER BY ts_ms DESC, id DESC
                LIMIT 1
                """,
                (investor_id, before_ts_ms),
            ).fetchone()
        return _row_to_snapshot(row) if row else None

    def snapshot_nearest(
        self,
        investor_id: str,
        *,
        target_ts_ms: int,
        max_delta_ms: int | None = None,
    ) -> NavSnapshotRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM nav_snapshots
                WHERE investor_id = ?
                ORDER BY ABS(ts_ms - ?) ASC, id DESC
                LIMIT 1
                """,
                (investor_id, target_ts_ms),
            ).fetchone()
        snap = _row_to_snapshot(row) if row else None
        if snap is None or max_delta_ms is None:
            return snap
        if abs(snap.ts_ms - target_ts_ms) > max_delta_ms:
            return None
        return snap

    def snapshots_in_range(
        self,
        investor_id: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[NavSnapshotRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM nav_snapshots
                WHERE investor_id = ? AND ts_ms >= ? AND ts_ms <= ?
                ORDER BY ts_ms ASC, id ASC
                """,
                (investor_id, start_ms, end_ms),
            ).fetchall()
        return [_row_to_snapshot(row) for row in rows]

    def list_settlements(self, investor_id: str) -> list[FeeSettlementRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM fee_settlements
                WHERE investor_id = ?
                ORDER BY period ASC
                """,
                (investor_id,),
            ).fetchall()
        return [_row_to_settlement(row) for row in rows]

    def settlement_for_period(self, investor_id: str, period: str) -> FeeSettlementRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM fee_settlements
                WHERE investor_id = ? AND period = ?
                """,
                (investor_id, period),
            ).fetchone()
        return _row_to_settlement(row) if row else None

    def save_settlement(self, row: dict[str, Any]) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO fee_settlements (
                        investor_id, period, period_start_ms, period_end_ms,
                        hwm_start, nav_perf_start, nav_perf_end, net_flow_usdc,
                        distributable_profit, performance_fee, hwm_end,
                        avg_aum_mgmt, management_fee, settled_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(investor_id, period) DO UPDATE SET
                        period_start_ms = excluded.period_start_ms,
                        period_end_ms = excluded.period_end_ms,
                        hwm_start = excluded.hwm_start,
                        nav_perf_start = excluded.nav_perf_start,
                        nav_perf_end = excluded.nav_perf_end,
                        net_flow_usdc = excluded.net_flow_usdc,
                        distributable_profit = excluded.distributable_profit,
                        performance_fee = excluded.performance_fee,
                        hwm_end = excluded.hwm_end,
                        avg_aum_mgmt = excluded.avg_aum_mgmt,
                        management_fee = excluded.management_fee,
                        settled_at_ms = excluded.settled_at_ms
                    """,
                    (
                        row["investor_id"],
                        row["period"],
                        row["period_start_ms"],
                        row["period_end_ms"],
                        str(row["hwm_start"]),
                        str(row["nav_perf_start"]),
                        str(row["nav_perf_end"]),
                        str(row["net_flow_usdc"]),
                        str(row["distributable_profit"]),
                        str(row["performance_fee"]),
                        str(row["hwm_end"]),
                        str(row["avg_aum_mgmt"]),
                        str(row["management_fee"]),
                        row["settled_at_ms"],
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)


def _row_to_snapshot(row: sqlite3.Row) -> NavSnapshotRow:
    equity_raw = json.loads(row["equity_by_book_json"] or "{}")
    equity_by_book = {str(k).upper(): to_decimal(v) for k, v in equity_raw.items()}
    return NavSnapshotRow(
        id=int(row["id"]),
        ts_ms=int(row["ts_ms"]),
        investor_id=str(row["investor_id"]),
        snapshot_kind=str(row["snapshot_kind"]),
        total_equity_usdc=to_decimal(row["total_equity_usdc"]),
        collateral_spot_usdc=to_decimal(row["collateral_spot_usdc"]),
        nav_perf=to_decimal(row["nav_perf"]),
        aum_mgmt=to_decimal(row["aum_mgmt"]),
        index_btc_usd=to_decimal(row["index_btc_usd"]),
        index_eth_usd=to_decimal(row["index_eth_usd"]),
        collateral_spot_btc=to_decimal(row["collateral_spot_btc"]),
        collateral_spot_eth=to_decimal(row["collateral_spot_eth"]),
        equity_by_book=equity_by_book,
        notes=row["notes"],
    )


def _row_to_flow_baseline(row: sqlite3.Row) -> FlowBaselineRow:
    native_raw = json.loads(row["net_flow_native_by_book_json"] or "{}")
    native_by_book = {str(k).upper(): to_decimal(v) for k, v in native_raw.items()}
    return FlowBaselineRow(
        investor_id=str(row["investor_id"]),
        cumulative_net_flow_usdc=to_decimal(row["cumulative_net_flow_usdc"]),
        initial_hwm_nav_perf=to_decimal(row["initial_hwm_nav_perf"]),
        net_flow_native_by_book=native_by_book,
        start_timestamp_ms=int(row["start_timestamp_ms"]),
        end_timestamp_ms=int(row["end_timestamp_ms"]),
        entry_count=int(row["entry_count"]),
        bootstrapped_at_ms=int(row["bootstrapped_at_ms"]),
        source=str(row["source"]),
    )


def _row_to_settlement(row: sqlite3.Row) -> FeeSettlementRow:
    return FeeSettlementRow(
        id=int(row["id"]),
        investor_id=str(row["investor_id"]),
        period=str(row["period"]),
        period_start_ms=int(row["period_start_ms"]),
        period_end_ms=int(row["period_end_ms"]),
        hwm_start=to_decimal(row["hwm_start"]),
        nav_perf_start=to_decimal(row["nav_perf_start"]),
        nav_perf_end=to_decimal(row["nav_perf_end"]),
        net_flow_usdc=to_decimal(row["net_flow_usdc"]),
        distributable_profit=to_decimal(row["distributable_profit"]),
        performance_fee=to_decimal(row["performance_fee"]),
        hwm_end=to_decimal(row["hwm_end"]),
        avg_aum_mgmt=to_decimal(row["avg_aum_mgmt"]),
        management_fee=to_decimal(row["management_fee"]),
        settled_at_ms=int(row["settled_at_ms"]),
    )
