"""Pacing for Deribit REST calls to reduce HTTP 429 bursts."""

from __future__ import annotations

import os
import threading
import time

_global_lock = threading.Lock()
_global_last_request_monotonic = 0.0
_identity_locks: dict[str, threading.Lock] = {}
_identity_last_request_monotonic: dict[str, float] = {}
_identity_lock_guard = threading.Lock()


def min_request_interval_seconds() -> float:
    """Minimum seconds between consecutive Deribit HTTP posts."""
    raw = os.environ.get("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.15")
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.15


def _identity_lock(identity: str | None) -> tuple[threading.Lock, str]:
    if not identity:
        return _global_lock, ""
    with _identity_lock_guard:
        lock = _identity_locks.get(identity)
        if lock is None:
            lock = threading.Lock()
            _identity_locks[identity] = lock
        return lock, identity


def pace_exchange_request(identity: str | None = None) -> None:
    """Block until the next Deribit request slot for ``identity`` (no-op when interval is 0)."""
    interval = min_request_interval_seconds()
    if interval <= 0:
        return
    lock, key = _identity_lock(identity)
    with lock:
        now = time.monotonic()
        if key:
            last = _identity_last_request_monotonic.get(key, 0.0)
        else:
            global _global_last_request_monotonic
            last = _global_last_request_monotonic
        wait = interval - (now - last)
        if wait > 0:
            time.sleep(wait)
        now = time.monotonic()
        if key:
            _identity_last_request_monotonic[key] = now
        else:
            _global_last_request_monotonic = now
