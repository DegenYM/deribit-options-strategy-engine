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

# Adaptive pacing: per-identity elevated interval that grows on HTTP 429 and
# decays back toward the base interval on success. "" is the shared/global key.
_identity_penalty_interval: dict[str, float] = {}
_penalty_guard = threading.Lock()


def min_request_interval_seconds() -> float:
    """Minimum seconds between consecutive Deribit HTTP posts."""
    raw = os.environ.get("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.15")
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.15


def max_request_interval_seconds() -> float:
    """Cap for the adaptive interval after repeated 429s."""
    raw = os.environ.get("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "2.0")
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 2.0


def note_rate_limited(identity: str | None = None) -> None:
    """Record a 429 for ``identity`` and exponentially widen its request interval."""
    base = min_request_interval_seconds()
    if base <= 0:
        return
    cap = max_request_interval_seconds()
    if cap <= base:
        return
    key = identity or ""
    with _penalty_guard:
        current = _identity_penalty_interval.get(key, 0.0)
        widened = base * 2 if current < base else current * 2
        _identity_penalty_interval[key] = min(cap, widened)


def note_success(identity: str | None = None) -> None:
    """Record a successful response for ``identity`` and decay its interval toward base."""
    base = min_request_interval_seconds()
    key = identity or ""
    with _penalty_guard:
        current = _identity_penalty_interval.get(key, 0.0)
        if current <= 0:
            return
        decayed = current * 0.5
        if decayed <= base:
            _identity_penalty_interval.pop(key, None)
        else:
            _identity_penalty_interval[key] = decayed


def adaptive_interval_seconds(identity: str | None = None) -> float:
    """Current effective interval for ``identity`` (base widened by any 429 penalty)."""
    base = min_request_interval_seconds()
    if base <= 0:
        return base
    with _penalty_guard:
        penalty = _identity_penalty_interval.get(identity or "", 0.0)
    return max(base, penalty)


def reset_adaptive_backoff() -> None:
    """Clear all adaptive penalties (intended for tests)."""
    with _penalty_guard:
        _identity_penalty_interval.clear()


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
    interval = adaptive_interval_seconds(identity)
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
