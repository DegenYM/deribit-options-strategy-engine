"""SQLite cache for dashboard performance series (daily realized PnL).

Rolling APR and cumulative PnL charts only need per-day aggregates. This store
rebuilds those buckets when strategy state files change, so API handlers avoid
re-scanning every closed trade group on each request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .utils import to_decimal

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scope_meta (
    scope_key TEXT PRIMARY KEY,
    source_fingerprint TEXT NOT NULL,
    closed_count INTEGER NOT NULL,
    synced_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_realized_pnl (
    scope_key TEXT NOT NULL,
    utc_day TEXT NOT NULL,
    book TEXT NOT NULL,
    pnl_usdc TEXT NOT NULL,
    PRIMARY KEY (scope_key, utc_day, book)
);

CREATE INDEX IF NOT EXISTS idx_daily_pnl_scope_day
    ON daily_realized_pnl (scope_key, utc_day);
"""


def performance_scope_key(accounts: list[Any]) -> str:
    """Stable id for a dashboard account set (multi-account aggregate)."""
    parts = sorted((str(getattr(a, "name", "")), str(getattr(a, "state_path", ""))) for a in accounts)
    digest = hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def fingerprint_from_cache_key(cache_key: tuple[Any, ...]) -> str:
    return json.dumps(cache_key, sort_keys=True, default=str)


class MetricsStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()

    def is_synced(self, scope_key: str, source_fingerprint: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_fingerprint FROM scope_meta WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        return row is not None and row[0] == source_fingerprint

    def sync_from_closed(
        self,
        scope_key: str,
        source_fingerprint: str,
        closed: list[dict[str, Any]],
        *,
        synced_at_ms: int,
    ) -> None:
        daily_by_book: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        daily_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        realized_count = 0
        for group in closed:
            ts_raw = group.get("closed_timestamp_ms")
            pnl_raw = group.get("realized_pnl")
            if ts_raw is None or pnl_raw is None:
                continue
            realized_count += 1
            day = datetime.fromtimestamp(int(ts_raw) / 1000, tz=UTC).strftime("%Y-%m-%d")
            pnl = to_decimal(pnl_raw)
            book = str(group.get("collateral_currency") or group.get("currency") or "USDC").upper()
            daily_by_book[book][day] += pnl
            daily_total[day] += pnl

        rows: list[tuple[str, str, str, str]] = []
        for day, pnl in daily_total.items():
            rows.append((scope_key, day, "TOTAL", str(pnl)))
        for book, by_day in daily_by_book.items():
            for day, pnl in by_day.items():
                rows.append((scope_key, day, book, str(pnl)))

        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM daily_realized_pnl WHERE scope_key = ?", (scope_key,))
                conn.executemany(
                    """
                    INSERT INTO daily_realized_pnl (scope_key, utc_day, book, pnl_usdc)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.execute(
                    """
                    INSERT INTO scope_meta (scope_key, source_fingerprint, closed_count, synced_at_ms)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(scope_key) DO UPDATE SET
                        source_fingerprint = excluded.source_fingerprint,
                        closed_count = excluded.closed_count,
                        synced_at_ms = excluded.synced_at_ms
                    """,
                    (scope_key, source_fingerprint, realized_count, synced_at_ms),
                )
                conn.commit()
        LOGGER.debug(
            "metrics store synced scope=%s closed=%s days=%s",
            scope_key[:8],
            realized_count,
            len(daily_total),
        )

    def load_daily_totals(self, scope_key: str) -> dict[date, Decimal]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT utc_day, pnl_usdc FROM daily_realized_pnl
                WHERE scope_key = ? AND book = 'TOTAL'
                ORDER BY utc_day
                """,
                (scope_key,),
            )
            rows = cur.fetchall()
        out: dict[date, Decimal] = {}
        for day_str, pnl_str in rows:
            out[datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=UTC).date()] = to_decimal(pnl_str)
        return out

    def closed_count(self, scope_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT closed_count FROM scope_meta WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        return int(row[0]) if row else 0

    def load_daily_by_book(self, scope_key: str) -> tuple[dict[str, dict[str, Decimal]], dict[str, Decimal]]:
        """Return (per-book daily pnl, portfolio daily total) keyed by YYYY-MM-DD string."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT book, utc_day, pnl_usdc FROM daily_realized_pnl
                WHERE scope_key = ? AND book != 'TOTAL'
                ORDER BY utc_day, book
                """,
                (scope_key,),
            )
            rows = cur.fetchall()
            total_cur = conn.execute(
                """
                SELECT utc_day, pnl_usdc FROM daily_realized_pnl
                WHERE scope_key = ? AND book = 'TOTAL'
                ORDER BY utc_day
                """,
                (scope_key,),
            )
            total_rows = total_cur.fetchall()
        daily_by_book: dict[str, dict[str, Decimal]] = defaultdict(dict)
        for book, day, pnl_str in rows:
            daily_by_book[book][day] = to_decimal(pnl_str)
        daily_total = {day: to_decimal(pnl) for day, pnl in total_rows}
        return dict(daily_by_book), daily_total
