"""Cross-process TTL cache for Deribit *public* reads.

Deribit rate-limits unauthenticated requests **per IP**, not per sub-account.
Every investor on this host therefore shares one public budget, and every one of
them asks for byte-identical data: the option chain, book summaries, order books
and index feeds do not depend on whose API key is used. With one live bot and
one frontend per investor that is a dozen processes duplicating the same reads.

The client already keeps these in per-process dicts; this module is the second
tier behind them, so a read paid for by one investor's process satisfies the
other eleven. It is deliberately best-effort: any sqlite problem falls through
to the network rather than failing the caller.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_DEFAULT_REL_PATH = "data/public_read_cache.db"
_conn_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_disabled = False

# Eviction. Rows carry no per-row TTL (the caller supplies ttl on read), so the
# table only ever grew: every distinct order-book / instrument key ever asked
# for stayed forever. Every ``_EVICT_EVERY_N_WRITES`` writes we drop rows older
# than ``PUBLIC_CACHE_MAX_AGE_SECONDS`` and trim to ``PUBLIC_CACHE_MAX_ROWS``
# (oldest ``stored_ms`` first). The longest read TTL in the client is well under
# a day, so 24h is a safe upper bound for anything still being served.
DEFAULT_MAX_AGE_SECONDS = 86_400
DEFAULT_MAX_ROWS = 5_000
_EVICT_EVERY_N_WRITES = 50
_write_counter = 0


def enabled() -> bool:
    """Off switch: ``DERIBIT_PUBLIC_CACHE=0`` restores per-process-only caching."""
    raw = os.environ.get("DERIBIT_PUBLIC_CACHE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(int(float(raw)), minimum)
    except (TypeError, ValueError):
        return default


def max_age_seconds() -> int:
    return _env_int("PUBLIC_CACHE_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS, minimum=60)


def max_rows() -> int:
    return _env_int("PUBLIC_CACHE_MAX_ROWS", DEFAULT_MAX_ROWS, minimum=100)


def evict(conn: sqlite3.Connection, *, now_ms: int | None = None) -> int:
    """Drop expired rows, then trim to the row cap. Returns rows deleted.

    Caller holds ``_conn_lock``. Errors propagate so the caller's existing
    best-effort handling logs and moves on — eviction must never break a write.
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff = now - max_age_seconds() * 1000
    deleted = conn.execute("DELETE FROM public_reads WHERE stored_ms < ?", (cutoff,)).rowcount
    cap = max_rows()
    row = conn.execute("SELECT COUNT(*) FROM public_reads").fetchone()
    count = int(row[0]) if row else 0
    if count > cap:
        excess = count - cap
        cur = conn.execute(
            "DELETE FROM public_reads WHERE key IN ("
            "  SELECT key FROM public_reads ORDER BY stored_ms ASC, key ASC LIMIT ?"
            ")",
            (excess,),
        )
        deleted += cur.rowcount
    conn.commit()
    return int(deleted)


def cache_path() -> Path:
    raw = os.environ.get("DERIBIT_PUBLIC_CACHE_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / _DEFAULT_REL_PATH


def _connect() -> sqlite3.Connection | None:
    global _conn, _conn_path, _disabled
    if _disabled or not enabled():
        return None
    path = str(cache_path())
    if _conn is not None and _conn_path == path:
        return _conn
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=2.0, check_same_thread=False)
        # WAL lets the eleven readers proceed while one process writes.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public_reads ("
            "  key TEXT PRIMARY KEY,"
            "  stored_ms INTEGER NOT NULL,"
            "  payload TEXT NOT NULL"
            ")"
        )
        # Eviction orders by stored_ms; without this the trim is a full scan.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_public_reads_stored_ms ON public_reads (stored_ms)")
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — cache must never break a read.
        LOGGER.debug("public cache unavailable (%s); using per-process cache only", exc)
        _disabled = True
        return None
    _conn = conn
    _conn_path = path
    return conn


def read(key: str, ttl_seconds: float) -> tuple[bool, Any]:
    """Return ``(hit, value)``. ``hit`` is False when absent, stale or unusable."""
    if ttl_seconds <= 0:
        return False, None
    with _conn_lock:
        conn = _connect()
        if conn is None:
            return False, None
        try:
            row = conn.execute("SELECT stored_ms, payload FROM public_reads WHERE key = ?", (key,)).fetchone()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("public cache read failed for %s: %s", key, exc)
            return False, None
    if row is None:
        return False, None
    stored_ms, payload = row
    if (time.time() * 1000 - float(stored_ms)) / 1000.0 >= ttl_seconds:
        return False, None
    try:
        return True, json.loads(payload)
    except Exception:  # noqa: BLE001 — a corrupt row is just a miss.
        return False, None


def write(key: str, value: Any) -> None:
    global _write_counter
    if value is None:
        return
    try:
        payload = json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    with _conn_lock:
        conn = _connect()
        if conn is None:
            return
        now_ms = int(time.time() * 1000)
        try:
            conn.execute(
                "INSERT INTO public_reads (key, stored_ms, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET stored_ms = excluded.stored_ms, payload = excluded.payload",
                (key, now_ms, payload),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("public cache write failed for %s: %s", key, exc)
            return
        _write_counter += 1
        if _write_counter % _EVICT_EVERY_N_WRITES != 0:
            return
        try:
            deleted = evict(conn, now_ms=now_ms)
        except Exception as exc:  # noqa: BLE001 — eviction is housekeeping only.
            LOGGER.debug("public cache eviction failed: %s", exc)
            return
        if deleted:
            LOGGER.debug("public cache evicted %s rows", deleted)


def row_count() -> int:
    """Rows currently stored (0 when the cache is unavailable). Test/ops helper."""
    with _conn_lock:
        conn = _connect()
        if conn is None:
            return 0
        try:
            row = conn.execute("SELECT COUNT(*) FROM public_reads").fetchone()
        except Exception:  # noqa: BLE001
            return 0
    return int(row[0]) if row else 0


def reset_for_tests(path: str | None = None) -> None:
    """Drop the cached connection so a test can point at a fresh database."""
    global _conn, _conn_path, _disabled, _write_counter
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn = None
        _conn_path = None
        _disabled = False
        _write_counter = 0
    if path is not None:
        os.environ["DERIBIT_PUBLIC_CACHE_PATH"] = path
