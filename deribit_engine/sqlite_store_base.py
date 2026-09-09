"""Shared SQLite plumbing for the small per-investor stores.

Six stores (market / portal / fee snapshots, transfers, trade journal, metrics)
used to carry identical ``__init__`` / ``_connect`` / ``_init_db`` / ``Lock``
boilerplate. They now inherit :class:`SqliteStoreBase`, which owns:

- one ``threading.Lock`` per store instance (writers serialize in-process;
  cross-process safety comes from SQLite's own locking + WAL),
- ``_connect()`` with the WAL / ``synchronous=NORMAL`` / ``busy_timeout``
  pragmas every store already applied,
- ``_init_db()`` that runs the subclass ``_schema`` script then the optional
  ``_migrate()`` hook,
- ``_ensure_columns()`` for the ad-hoc ``PRAGMA table_info`` → ``ALTER TABLE``
  migrations,
- ``_transaction()`` for new code paths that want commit / rollback / close
  handled for them.

``PRAGMA foreign_keys`` is intentionally *not* enabled: none of the schemas
declare foreign keys today, and flipping it on retroactively could reject
writes on legacy databases.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_BUSY_TIMEOUT_MS = 30_000


class SqliteStoreBase:
    """Base class for file-backed SQLite stores.

    Subclasses set ``_schema`` (a ``CREATE TABLE IF NOT EXISTS ...`` script) and
    may override ``_migrate(conn)`` for column additions. ``row_factory`` defaults
    to :class:`sqlite3.Row`; stores that index rows positionally set it to
    ``None`` to keep their historical tuple semantics.
    """

    _schema: str = ""
    row_factory: Any = sqlite3.Row
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self._path

    # ---- connections ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=self.connect_timeout_seconds)
        # WAL lets concurrent readers (frontend, CLI) proceed while one process writes.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        if self.row_factory is not None:
            conn.row_factory = self.row_factory
        return conn

    @contextlib.contextmanager
    def _transaction(self, *, locked: bool = True) -> Iterator[sqlite3.Connection]:
        """Yield a connection; commit on success, roll back on error, always close.

        ``locked=True`` (default) also holds the store's in-process lock so
        concurrent writers from different threads serialize.
        """
        lock_ctx = self._lock if locked else contextlib.nullcontext()
        with lock_ctx:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()

    # ---- schema ---------------------------------------------------------------

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                if self._schema:
                    conn.executescript(self._schema)
                self._migrate(conn)
                conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Hook for additive migrations; runs inside ``_init_db`` after the schema."""
        return None

    def _ensure_columns(
        self,
        table: str,
        columns: dict[str, str],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> list[str]:
        """Add any missing ``columns`` (``{name: ddl}``) to ``table``. Returns names added.

        ``ddl`` is the column type + constraints, e.g. ``"TEXT NOT NULL DEFAULT '{}'"``.
        Pass ``conn`` when already inside ``_init_db`` / a transaction; otherwise a
        short-lived locked connection is used.
        """
        if conn is not None:
            return self._ensure_columns_on(conn, table, columns)
        with self._transaction() as own:
            return self._ensure_columns_on(own, table, columns)

    @staticmethod
    def _ensure_columns_on(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> list[str]:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        added: list[str] = []
        for name, ddl in columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.append(name)
        return added

    # ---- helpers --------------------------------------------------------------

    def _delete_where(self, table: str, where: str, params: tuple[Any, ...]) -> int:
        """Locked ``DELETE FROM table WHERE ...``; returns rows deleted."""
        with self._transaction() as conn:
            cur = conn.execute(f"DELETE FROM {table} WHERE {where}", params)
            return int(cur.rowcount)

    def _table_row_count(self, table: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        finally:
            conn.close()
        return int(row[0]) if row else 0
