"""Persistent Deribit transfer rows for incremental dashboard sync."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import TransactionEntry
from .sqlite_store_base import SqliteStoreBase
from .utils import utc_now_ms

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_rows (
    scope_key TEXT NOT NULL,
    book TEXT NOT NULL,
    transfer_id INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    amount_native TEXT NOT NULL,
    info TEXT NOT NULL,
    balance_after TEXT,
    PRIMARY KEY (scope_key, book, transfer_id)
);

CREATE INDEX IF NOT EXISTS idx_transfer_rows_scope_book_ts
    ON transfer_rows (scope_key, book, timestamp_ms DESC);

CREATE TABLE IF NOT EXISTS transfer_sync_meta (
    scope_key TEXT NOT NULL,
    book TEXT NOT NULL,
    last_synced_through_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    PRIMARY KEY (scope_key, book)
);
"""

DEFAULT_TRANSFER_SYNC_OVERLAP_MS = 3_600_000


def transfers_db_path_for_accounts(accounts: list[Any]) -> Path:
    """Pick a shared sqlite path beside dashboard ledger roots."""
    from .frontend_server.constants import LEDGER_DIR

    roots = [getattr(account, "ledger_root", None) for account in accounts]
    roots = [root for root in roots if isinstance(root, Path)]
    if not roots:
        return LEDGER_DIR / "transfers.db"
    parents = {root.parent for root in roots}
    if len(parents) == 1:
        return next(iter(parents)) / "transfers.db"
    if len(roots) == 1:
        return roots[0] / "transfers.db"
    return LEDGER_DIR / "transfers.db"


class TransferStore(SqliteStoreBase):
    _schema = _SCHEMA

    @property
    def path(self) -> Path:
        return self._path

    def purge_older_than(self, *, cutoff_ms: int, scope_key: str | None = None, book: str | None = None) -> int:
        """Delete ``transfer_rows`` with ``timestamp_ms < cutoff_ms``. Returns rows deleted.

        ``transfer_sync_meta`` is left alone so incremental sync does not
        re-fetch the purged window. Not called automatically anywhere: transfer
        history feeds fee / NAV flow reconciliation, so retention is an operator
        decision.
        """
        clauses = ["timestamp_ms < ?"]
        params: list[Any] = [int(cutoff_ms)]
        if scope_key is not None:
            clauses.append("scope_key = ?")
            params.append(scope_key)
        if book is not None:
            clauses.append("book = ?")
            params.append(book.upper())
        return self._delete_where("transfer_rows", " AND ".join(clauses), tuple(params))

    def max_timestamp_ms(self, scope_key: str, book: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(timestamp_ms) AS max_ts
                FROM transfer_rows
                WHERE scope_key = ? AND book = ?
                """,
                (scope_key, book.upper()),
            ).fetchone()
        if not row or row["max_ts"] is None:
            return None
        return int(row["max_ts"])

    def upsert_row(self, scope_key: str, book: str, entry: TransactionEntry) -> bool:
        row = (
            scope_key,
            book.upper(),
            int(entry.id),
            int(entry.timestamp),
            str(entry.amount),
            entry.info,
            str(entry.balance) if entry.balance is not None else None,
        )
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO transfer_rows (
                        scope_key, book, transfer_id, timestamp_ms,
                        amount_native, info, balance_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scope_key, book, transfer_id) DO NOTHING
                    """,
                    row,
                )
                conn.commit()
                return int(cur.rowcount) > 0

    def touch_sync_meta(self, scope_key: str, book: str, *, synced_through_ms: int) -> None:
        now = utc_now_ms()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO transfer_sync_meta (
                        scope_key, book, last_synced_through_ms, updated_ts_ms
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(scope_key, book) DO UPDATE SET
                        last_synced_through_ms = excluded.last_synced_through_ms,
                        updated_ts_ms = excluded.updated_ts_ms
                    """,
                    (scope_key, book.upper(), int(synced_through_ms), now),
                )
                conn.commit()

    def list_rows(
        self,
        scope_key: str,
        book: str,
        *,
        since_ms: int,
        until_ms: int | None = None,
        limit: int | None = None,
    ) -> list[TransactionEntry]:
        clauses = ["scope_key = ?", "book = ?", "timestamp_ms >= ?"]
        params: list[Any] = [scope_key, book.upper(), int(since_ms)]
        if until_ms is not None:
            clauses.append("timestamp_ms <= ?")
            params.append(int(until_ms))
        sql = f"""
            SELECT transfer_id, timestamp_ms, amount_native, info, balance_after
            FROM transfer_rows
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp_ms DESC
        """
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[TransactionEntry] = []
        for row in rows:
            balance_raw = row["balance_after"]
            out.append(
                TransactionEntry(
                    id=int(row["transfer_id"]),
                    timestamp=int(row["timestamp_ms"]),
                    type="transfer",
                    currency=book.upper(),
                    amount=Decimal(str(row["amount_native"])),
                    balance=Decimal(str(balance_raw)) if balance_raw is not None else None,
                    info=str(row["info"] or ""),
                )
            )
        return out

    def row_count(self, scope_key: str, book: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM transfer_rows WHERE scope_key = ? AND book = ?",
                (scope_key, book.upper()),
            ).fetchone()
        return int(row["cnt"]) if row else 0
