"""Per-investor portal dashboard snapshots (disk + live) for fast first paint."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import utc_now_ms

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    investor_id TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL,
    market_snapshot_id INTEGER,
    payload_json TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portal_investor_ts
    ON portal_snapshots (investor_id, ts_ms DESC);

CREATE INDEX IF NOT EXISTS idx_portal_investor_kind_ts
    ON portal_snapshots (investor_id, snapshot_kind, ts_ms DESC);
"""


@dataclass(frozen=True)
class PortalSnapshotRow:
    id: int
    ts_ms: int
    investor_id: str
    snapshot_kind: str
    market_snapshot_id: int | None
    payload: dict[str, Any]
    content_fingerprint: str


class PortalSnapshotStore:
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

    def append(
        self,
        *,
        investor_id: str,
        snapshot_kind: str,
        payload: dict[str, Any],
        content_fingerprint: str,
        market_snapshot_id: int | None = None,
        ts_ms: int | None = None,
    ) -> int | None:
        bucket_ms = int(ts_ms or utc_now_ms())
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    """
                    SELECT id FROM portal_snapshots
                    WHERE investor_id = ? AND snapshot_kind = ?
                      AND content_fingerprint = ?
                      AND ts_ms >= ?
                    LIMIT 1
                    """,
                    (investor_id, snapshot_kind, content_fingerprint, bucket_ms - 240_000),
                ).fetchone()
                if existing is not None:
                    return None
                cur = conn.execute(
                    """
                    INSERT INTO portal_snapshots (
                        ts_ms, investor_id, snapshot_kind,
                        market_snapshot_id, payload_json, content_fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bucket_ms,
                        investor_id,
                        snapshot_kind,
                        market_snapshot_id,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        content_fingerprint,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

    def latest(self, investor_id: str, *, snapshot_kind: str | None = None) -> PortalSnapshotRow | None:
        with self._connect() as conn:
            if snapshot_kind is None:
                row = conn.execute(
                    """
                    SELECT * FROM portal_snapshots
                    WHERE investor_id = ?
                    ORDER BY ts_ms DESC, id DESC
                    LIMIT 1
                    """,
                    (investor_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM portal_snapshots
                    WHERE investor_id = ? AND snapshot_kind = ?
                    ORDER BY ts_ms DESC, id DESC
                    LIMIT 1
                    """,
                    (investor_id, snapshot_kind),
                ).fetchone()
        return self._row_from_db(row) if row else None

    def _row_from_db(self, row: sqlite3.Row) -> PortalSnapshotRow:
        return PortalSnapshotRow(
            id=int(row["id"]),
            ts_ms=int(row["ts_ms"]),
            investor_id=str(row["investor_id"]),
            snapshot_kind=str(row["snapshot_kind"]),
            market_snapshot_id=int(row["market_snapshot_id"]) if row["market_snapshot_id"] is not None else None,
            payload=json.loads(row["payload_json"]),
            content_fingerprint=str(row["content_fingerprint"]),
        )

    def purge_older_than(self, *, investor_id: str, snapshot_kind: str, cutoff_ms: int) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM portal_snapshots
                    WHERE investor_id = ? AND snapshot_kind = ? AND ts_ms < ?
                    """,
                    (investor_id, snapshot_kind, cutoff_ms),
                )
                conn.commit()
                return int(cur.rowcount)
