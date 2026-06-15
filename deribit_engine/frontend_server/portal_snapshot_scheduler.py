"""Background schedulers for market + investor portal disk snapshots."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from ..frontend_server.constants import (
    DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC,
    DEFAULT_PORTAL_SNAPSHOT_DISK_INTERVAL_SEC,
)
from ..frontend_server.types import SnapshotState
from ..portal_snapshot_service import PortalSnapshotService
from ..utils import utc_now_ms

LOGGER = logging.getLogger(__name__)


class MarketSnapshotScheduler:
    def __init__(
        self,
        *,
        capture_fn: Callable[[], None],
        interval_sec: int | None = None,
        retention_fn: Callable[[], None] | None = None,
    ) -> None:
        self._capture_fn = capture_fn
        self._retention_fn = retention_fn
        self._interval_sec = max(
            60,
            int(interval_sec or os.environ.get("MARKET_SNAPSHOT_INTERVAL_SEC", DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC)),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_retention_ms = 0
        self.state = SnapshotState()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="market-snapshot", daemon=True)
        self.state.running = True
        self._thread.start()
        LOGGER.info("market snapshot scheduler started (interval=%ss)", self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.state.running = False

    def _loop(self) -> None:
        self._tick()
        while not self._stop.wait(self._interval_sec):
            self._tick()

    def _tick(self) -> None:
        self.state.last_attempt_ms = utc_now_ms()
        try:
            self._capture_fn()
            now_ms = utc_now_ms()
            if self._retention_fn is not None and (now_ms - self._last_retention_ms) >= 86400_000:
                self._retention_fn()
                self._last_retention_ms = now_ms
            self.state.last_success_ms = utc_now_ms()
            self.state.last_error = None
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("market snapshot tick failed: %s", exc)
            self.state.last_error = str(exc)


class PortalDiskSnapshotScheduler:
    def __init__(
        self,
        *,
        capture_fn: Callable[[], None],
        interval_sec: int | None = None,
        retention_fn: Callable[[], None] | None = None,
    ) -> None:
        self._capture_fn = capture_fn
        self._retention_fn = retention_fn
        self._interval_sec = max(
            60,
            int(
                interval_sec
                or os.environ.get("PORTAL_SNAPSHOT_DISK_INTERVAL_SEC", DEFAULT_PORTAL_SNAPSHOT_DISK_INTERVAL_SEC)
            ),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_retention_ms = 0
        self.state = SnapshotState()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="portal-disk-snapshot", daemon=True)
        self.state.running = True
        self._thread.start()
        LOGGER.info("portal disk snapshot scheduler started (interval=%ss)", self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.state.running = False

    def _loop(self) -> None:
        self._tick()
        while not self._stop.wait(self._interval_sec):
            self._tick()

    def _tick(self) -> None:
        self.state.last_attempt_ms = utc_now_ms()
        try:
            self._capture_fn()
            now_ms = utc_now_ms()
            if self._retention_fn is not None and (now_ms - self._last_retention_ms) >= 86400_000:
                self._retention_fn()
                self._last_retention_ms = now_ms
            self.state.last_success_ms = utc_now_ms()
            self.state.last_error = None
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("portal disk snapshot tick failed: %s", exc)
            self.state.last_error = str(exc)


def make_portal_snapshot_service(repo_root: Path | None, investor_id: str | None) -> PortalSnapshotService | None:
    if repo_root is None or not investor_id:
        return None
    return PortalSnapshotService(repo_root=Path(repo_root), investor_id=investor_id)
