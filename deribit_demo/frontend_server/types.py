from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import BotConfig
from ..engine import DeribitOptionTrialBot
from ..utils import utc_now_ms

LOGGER = logging.getLogger(__name__)


@dataclass
class SnapshotState:
    last_attempt_ms: int | None = None
    last_success_ms: int | None = None
    last_error: str | None = None
    running: bool = False


@dataclass(frozen=True)
class DashboardAccount:
    name: str
    env_file: Path
    config: BotConfig
    state_path: Path
    ledger_root: Path


class EquitySnapshotScheduler:
    """Tiny self-contained scheduler.

    APScheduler would work too, but a single daemon thread keeps the
    dependency surface small (no extra package, no signal handling).
    """

    def __init__(
        self,
        *,
        account_name: str,
        bot_factory: Callable[[], DeribitOptionTrialBot],
        interval_sec: int,
        ledger_root: Path,
        config: BotConfig,
    ) -> None:
        self._account_name = account_name
        self._bot_factory = bot_factory
        self._interval_sec = max(30, int(interval_sec))
        self._ledger_root = ledger_root
        self._config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = SnapshotState()

    def start(self) -> None:
        from .helpers import _has_private_creds

        if not _has_private_creds(self._config):
            LOGGER.info("snapshot scheduler disabled: no private creds in env")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="equity-snapshot", daemon=True)
        self.state.running = True
        self._thread.start()
        LOGGER.info("snapshot scheduler started (interval=%ss)", self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.state.running = False

    def _loop(self) -> None:
        # Take an immediate snapshot so the ledger gets a row even on first
        # boot, then sleep on the configured cadence.
        self._tick()
        while not self._stop.wait(self._interval_sec):
            self._tick()

    def _tick(self) -> None:
        from .helpers import _append_ledger

        self.state.last_attempt_ms = utc_now_ms()
        try:
            bot = self._bot_factory()
            context = bot._load_runtime()
            snapshot = context.snapshot
            row = {
                "ts_ms": utc_now_ms(),
                "account_name": self._account_name,
                "env": self._config.env,
                "option_strategy": self._config.option_strategy,
                "state_file": str(self._config.state_file),
                "regime": snapshot.regime.value,
                "total_equity_usdc": str(snapshot.total_equity_usdc),
                "day_start_equity_usdc": str(snapshot.day_start_equity_usdc),
                "day_net_flow_usdc": str(snapshot.day_net_flow_usdc),
                "day_pnl_usdc_ex_flow": str(snapshot.day_pnl_usdc_ex_flow),
                "day_pnl_usdc_ex_flow_ex_spot": str(snapshot.day_pnl_usdc_ex_flow_ex_spot),
                "day_drawdown_pct": str(snapshot.day_drawdown_pct),
                "open_max_loss_usdc": str(snapshot.open_max_loss),
                "initial_margin_ratio": str(snapshot.initial_margin_ratio),
                "maintenance_margin_ratio": str(snapshot.maintenance_margin_ratio),
                "equity_by_book": {k: str(v) for k, v in snapshot.equity_by_book.items()},
                "day_start_equity_by_book": {k: str(v) for k, v in snapshot.day_start_equity_by_book.items()},
                "day_net_flow_usdc_by_book": {k: str(v) for k, v in snapshot.day_net_flow_usdc_by_book.items()},
                "day_pnl_usdc_ex_flow_by_book": {k: str(v) for k, v in snapshot.day_pnl_usdc_ex_flow_by_book.items()},
                "day_pnl_usdc_ex_flow_ex_spot_by_book": {
                    k: str(v) for k, v in snapshot.day_pnl_usdc_ex_flow_ex_spot_by_book.items()
                },
                "delta_totals_by_currency": {k: str(v) for k, v in snapshot.delta_totals_by_currency.items()},
                "regime_by_currency": {k: v.value for k, v in snapshot.regime_by_currency.items()},
                "halt_new_entries": snapshot.halt_new_entries,
                "hard_derisk": snapshot.hard_derisk,
            }
            _append_ledger(self._ledger_root, row)
            self.state.last_success_ms = utc_now_ms()
            self.state.last_error = None
        except Exception as exc:  # noqa: BLE001 — scheduler must not crash the server.
            LOGGER.warning("equity snapshot failed: %s", exc)
            self.state.last_error = str(exc)


class TradeJournalSyncState:
    last_attempt_ms: int | None = None
    last_success_ms: int | None = None
    last_error: str | None = None
    last_inserted: int = 0
    running: bool = False


class TradeJournalSyncScheduler:
    """Background incremental sync of Deribit fills into trade_journal.db."""

    def __init__(
        self,
        *,
        accounts: list[DashboardAccount],
        interval_sec: int,
    ) -> None:
        self._accounts = accounts
        self._interval_sec = max(60, int(interval_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = TradeJournalSyncState()

    def start(self) -> None:
        import deribit_demo.frontend_server as pkg

        if not any(pkg._has_private_creds(account.config) for account in self._accounts):
            LOGGER.info("trade journal sync scheduler disabled: no private creds")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="trade-journal-sync", daemon=True)
        self.state.running = True
        self._thread.start()
        LOGGER.info("trade journal sync scheduler started (interval=%ss)", self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.state.running = False

    def run_once(self) -> dict[str, Any]:
        """Run a single sync pass (used by scheduler tick and manual API)."""
        self.state.last_attempt_ms = utc_now_ms()
        results: list[dict[str, Any]] = []
        total_inserted = 0
        errors: list[str] = []
        for account in self._accounts:
            import deribit_demo.frontend_server as pkg

            if not pkg._has_private_creds(account.config):
                results.append({"account": account.name, "skipped": True, "reason": "no_credentials"})
                continue
            try:
                row = pkg.sync_incremental_journal(account.env_file)
                total_inserted += int(row.get("api_inserted") or 0)
                row["account"] = account.name
                results.append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{account.name}: {exc}")
                results.append({"account": account.name, "error": str(exc)})
        self.state.last_inserted = total_inserted
        if errors:
            self.state.last_error = "; ".join(errors)
            LOGGER.warning("trade journal sync had errors: %s", self.state.last_error)
        else:
            self.state.last_success_ms = utc_now_ms()
            self.state.last_error = None
            if total_inserted:
                LOGGER.info("trade journal sync inserted %s fill(s)", total_inserted)
        return {"accounts": results, "api_inserted": total_inserted}

    def _loop(self) -> None:
        self._tick()
        while not self._stop.wait(self._interval_sec):
            self._tick()

    def _tick(self) -> None:
        try:
            self.run_once()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("trade journal sync failed: %s", exc)
            self.state.last_error = str(exc)


class _TtlCache:
    """Trivial TTL cache — just enough to avoid hammering Deribit."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[Any, tuple[float, Any]] = {}
        self._inflight: dict[Any, threading.Event] = {}

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                leader = True
            else:
                leader = False
        if not leader:
            event.wait(timeout=120)
            with self._lock:
                cached = self._store.get(key)
                if cached is not None:
                    return cached[1]
            return factory()
        try:
            value = factory()
            with self._lock:
                self._store[key] = (time.monotonic(), value)
            return value
        finally:
            with self._lock:
                done = self._inflight.pop(key, None)
            if done is not None:
                done.set()

    def try_get(self, key: Any) -> Any | None:
        """Return cached value when present and fresh; otherwise ``None``."""
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
        return None

    def get_stale(self, key: Any) -> Any | None:
        """Return last stored value for ``key`` even when TTL expired."""
        with self._lock:
            cached = self._store.get(key)
            if cached is not None:
                return cached[1]
        return None

    def cache_age_ms(self, key: Any) -> int | None:
        """Milliseconds since ``key`` was last stored (including fresh computes)."""
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is None:
                return None
            return int((now - cached[0]) * 1000)

    def seed(self, key: Any, value: Any) -> None:
        """Store ``value`` under ``key`` as if freshly computed (for cross-endpoint cache warm-up)."""
        with self._lock:
            self._store[key] = (time.monotonic(), value)
