"""Shared BTC/ETH spot + macro snapshots for dashboard / investor portal caches."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .utils import to_decimal, utc_now_ms

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    btc_usd TEXT NOT NULL,
    eth_usd TEXT NOT NULL,
    btc_change_24h_pct TEXT,
    eth_change_24h_pct TEXT,
    iv_rank_btc_pct TEXT,
    iv_rank_eth_pct TEXT,
    dvol_btc TEXT,
    dvol_eth TEXT,
    source TEXT NOT NULL DEFAULT 'deribit_public'
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_ts
    ON market_snapshots (ts_ms DESC);
"""


@dataclass(frozen=True)
class MarketSnapshotRow:
    id: int
    ts_ms: int
    btc_usd: Decimal
    eth_usd: Decimal
    btc_change_24h_pct: Decimal | None
    eth_change_24h_pct: Decimal | None
    iv_rank_btc_pct: Decimal | None
    iv_rank_eth_pct: Decimal | None
    dvol_btc: Decimal | None
    dvol_eth: Decimal | None
    source: str

    def to_spot_api_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "BTC": str(self.btc_usd),
            "ETH": str(self.eth_usd),
        }
        change: dict[str, Any] = {}
        if self.btc_change_24h_pct is not None:
            change["BTC"] = str(self.btc_change_24h_pct)
        if self.eth_change_24h_pct is not None:
            change["ETH"] = str(self.eth_change_24h_pct)
        if change:
            out["price_change_pct_24h"] = change
        iv_rank: dict[str, Any] = {}
        if self.iv_rank_btc_pct is not None:
            iv_rank["BTC"] = str(self.iv_rank_btc_pct)
        if self.iv_rank_eth_pct is not None:
            iv_rank["ETH"] = str(self.iv_rank_eth_pct)
        if iv_rank:
            out["iv_rank_pct"] = iv_rank
        dvol: dict[str, Any] = {}
        if self.dvol_btc is not None:
            dvol["BTC"] = str(self.dvol_btc)
        if self.dvol_eth is not None:
            dvol["ETH"] = str(self.dvol_eth)
        if dvol:
            out["dvol"] = dvol
        return out


def _signed_decimal(value: Any) -> Decimal | None:
    """Parse a decimal that may be negative (e.g. 24h index % change)."""
    if value is None or value == "":
        return None
    try:
        return to_decimal(value)
    except Exception:
        return None


def _non_negative_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    n = to_decimal(value)
    return n if n >= 0 else None


def _row_from_db(row: sqlite3.Row) -> MarketSnapshotRow:
    return MarketSnapshotRow(
        id=int(row["id"]),
        ts_ms=int(row["ts_ms"]),
        btc_usd=to_decimal(row["btc_usd"]),
        eth_usd=to_decimal(row["eth_usd"]),
        btc_change_24h_pct=_signed_decimal(row["btc_change_24h_pct"]),
        eth_change_24h_pct=_signed_decimal(row["eth_change_24h_pct"]),
        iv_rank_btc_pct=_non_negative_decimal(row["iv_rank_btc_pct"]),
        iv_rank_eth_pct=_non_negative_decimal(row["iv_rank_eth_pct"]),
        dvol_btc=_non_negative_decimal(row["dvol_btc"]),
        dvol_eth=_non_negative_decimal(row["dvol_eth"]),
        source=str(row["source"] or "deribit_public"),
    )


class MarketSnapshotStore:
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

    def append_from_spot_payload(self, spot_payload: dict[str, Any], *, source: str = "deribit_public") -> int:
        btc = to_decimal(spot_payload.get("BTC"))
        eth = to_decimal(spot_payload.get("ETH"))
        if btc <= 0 or eth <= 0:
            raise ValueError("spot payload missing BTC/ETH index prices")
        change = spot_payload.get("price_change_pct_24h") or {}
        iv_rank = spot_payload.get("iv_rank_pct") or {}
        dvol = spot_payload.get("dvol") or {}
        with self._lock:
            with self._connect() as conn:
                max_row = conn.execute("SELECT MAX(ts_ms) AS max_ts FROM market_snapshots").fetchone()
                max_ts = int(max_row["max_ts"]) if max_row and max_row["max_ts"] is not None else 0
                ts_ms = max(utc_now_ms(), max_ts + 1)
                cur = conn.execute(
                    """
                    INSERT INTO market_snapshots (
                        ts_ms, btc_usd, eth_usd,
                        btc_change_24h_pct, eth_change_24h_pct,
                        iv_rank_btc_pct, iv_rank_eth_pct,
                        dvol_btc, dvol_eth, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts_ms,
                        str(btc),
                        str(eth),
                        str(change["BTC"]) if change.get("BTC") is not None else None,
                        str(change["ETH"]) if change.get("ETH") is not None else None,
                        str(iv_rank["BTC"]) if iv_rank.get("BTC") is not None else None,
                        str(iv_rank["ETH"]) if iv_rank.get("ETH") is not None else None,
                        str(dvol["BTC"]) if dvol.get("BTC") is not None else None,
                        str(dvol["ETH"]) if dvol.get("ETH") is not None else None,
                        source,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

    def latest(self) -> MarketSnapshotRow | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM market_snapshots ORDER BY ts_ms DESC, id DESC LIMIT 1").fetchone()
        return _row_from_db(row) if row else None

    def get(self, snapshot_id: int) -> MarketSnapshotRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM market_snapshots WHERE id = ?",
                (int(snapshot_id),),
            ).fetchone()
        return _row_from_db(row) if row else None

    def nearest_at_or_before(self, ts_ms: int) -> MarketSnapshotRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_snapshots
                WHERE ts_ms <= ?
                ORDER BY ts_ms DESC, id DESC
                LIMIT 1
                """,
                (int(ts_ms),),
            ).fetchone()
        return _row_from_db(row) if row else None

    def purge_older_than(self, *, cutoff_ms: int) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM market_snapshots WHERE ts_ms < ?", (cutoff_ms,))
                conn.commit()
                return int(cur.rowcount)
