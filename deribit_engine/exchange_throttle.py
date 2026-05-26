"""Global pacing for Deribit REST calls to reduce HTTP 429 bursts."""

from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_last_request_monotonic = 0.0


def min_request_interval_seconds() -> float:
    """Minimum seconds between consecutive Deribit HTTP posts (process-wide)."""
    raw = os.environ.get("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.15")
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.15


def pace_exchange_request() -> None:
    """Block until the next Deribit request slot (no-op when interval is 0)."""
    interval = min_request_interval_seconds()
    if interval <= 0:
        return
    global _last_request_monotonic
    with _lock:
        now = time.monotonic()
        wait = interval - (now - _last_request_monotonic)
        if wait > 0:
            time.sleep(wait)
        _last_request_monotonic = time.monotonic()
