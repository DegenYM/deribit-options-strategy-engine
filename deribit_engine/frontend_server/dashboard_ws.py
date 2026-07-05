from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from typing import Any

from ..utils import utc_now_ms
from .constants import (
    DEFAULT_WS_HEALTH_INTERVAL_SEC,
    DEFAULT_WS_MARKET_INTERVAL_SEC,
    DEFAULT_WS_PORTFOLIO_INTERVAL_SEC,
)
from .helpers import _decimalize

LOGGER = logging.getLogger(__name__)

_VALID_CHANNELS = frozenset({"market", "portfolio", "groups", "health"})


def parse_ws_channels(raw: str | None) -> frozenset[str]:
    if raw is None or not raw.strip():
        return frozenset({"market", "portfolio", "groups"})
    parts = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = parts - _VALID_CHANNELS
    if unknown:
        allowed = ", ".join(sorted(_VALID_CHANNELS))
        unknown_list = ", ".join(sorted(unknown))
        raise ValueError(f"unknown websocket channels: {unknown_list}; allowed: {allowed}")
    if not parts:
        raise ValueError("websocket channels must include at least one channel")
    return frozenset(parts)


def _payload_hash(payload: Any) -> str:
    encoded = json.dumps(_decimalize(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class DashboardWsHub:
    """Push dashboard deltas to browsers over a single WebSocket endpoint."""

    def __init__(
        self,
        *,
        fetch_market: Callable[[], dict[str, Any]],
        read_status: Callable[[], dict[str, Any] | None],
        read_groups: Callable[[], dict[str, Any] | None],
        build_health: Callable[[], dict[str, Any]],
        has_private_creds: Callable[[], bool],
        market_interval_sec: float | None = None,
        portfolio_interval_sec: float | None = None,
        health_interval_sec: float | None = None,
    ) -> None:
        self._fetch_market = fetch_market
        self._read_status = read_status
        self._read_groups = read_groups
        self._build_health = build_health
        self._has_private_creds = has_private_creds
        self._market_interval = max(
            3.0,
            float(
                market_interval_sec
                if market_interval_sec is not None
                else os.environ.get("FRONTEND_WS_MARKET_INTERVAL_SEC", DEFAULT_WS_MARKET_INTERVAL_SEC)
            ),
        )
        self._portfolio_interval = max(
            5.0,
            float(
                portfolio_interval_sec
                if portfolio_interval_sec is not None
                else os.environ.get("FRONTEND_WS_PORTFOLIO_INTERVAL_SEC", DEFAULT_WS_PORTFOLIO_INTERVAL_SEC)
            ),
        )
        self._health_interval = max(
            10.0,
            float(
                health_interval_sec
                if health_interval_sec is not None
                else os.environ.get("FRONTEND_WS_HEALTH_INTERVAL_SEC", DEFAULT_WS_HEALTH_INTERVAL_SEC)
            ),
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[Any, frozenset[str]] = {}
        self._clients_lock = asyncio.Lock()
        self._last_hashes: dict[str, str] = {}
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._market_loop(), name="dashboard-ws-market"),
            asyncio.create_task(self._portfolio_loop(), name="dashboard-ws-portfolio"),
            asyncio.create_task(self._health_loop(), name="dashboard-ws-health"),
        ]
        LOGGER.info(
            "dashboard websocket hub started (market=%ss portfolio=%ss health=%ss)",
            self._market_interval,
            self._portfolio_interval,
            self._health_interval,
        )

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        async with self._clients_lock:
            clients = list(self._clients.keys())
            self._clients.clear()
        for ws in clients:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        LOGGER.info("dashboard websocket hub stopped")

    def notify_live_warm(self, *, status: dict[str, Any] | None, groups: dict[str, Any] | None) -> None:
        """Called from background bundle-warm threads when live caches refresh."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._push_live_warm(status=status, groups=groups), loop)

    async def connect(self, websocket: Any, channels: frozenset[str]) -> None:
        async with self._clients_lock:
            self._clients[websocket] = channels
        await websocket.send_json(
            {
                "type": "hello",
                "channels": sorted(channels),
                "server_time_ms": utc_now_ms(),
            }
        )
        await self._send_initial_snapshots(websocket, channels)

    async def disconnect(self, websocket: Any) -> None:
        async with self._clients_lock:
            self._clients.pop(websocket, None)

    async def _send_initial_snapshots(self, websocket: Any, channels: frozenset[str]) -> None:
        if "market" in channels:
            payload = await self._load_market()
            if payload is not None:
                await websocket.send_json(self._envelope("market", payload))
        if "health" in channels:
            payload = _decimalize(self._build_health())
            await websocket.send_json(self._envelope("health", payload))
        if self._has_private_creds():
            if "portfolio" in channels:
                status = self._read_status()
                if status is not None:
                    await websocket.send_json(self._envelope("portfolio", _decimalize(status)))
            if "groups" in channels:
                groups = self._read_groups()
                if groups is not None:
                    await websocket.send_json(self._envelope("groups", _decimalize(groups)))

    async def _market_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick_market()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("dashboard ws market tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._market_interval)
                break
            except TimeoutError:
                continue

    async def _portfolio_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick_portfolio()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("dashboard ws portfolio tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._portfolio_interval)
                break
            except TimeoutError:
                continue

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick_health()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("dashboard ws health tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._health_interval)
                break
            except TimeoutError:
                continue

    async def _load_market(self) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        try:
            payload = await loop.run_in_executor(None, self._fetch_market)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("dashboard ws market fetch failed: %s", exc)
            return None
        return _decimalize(payload)

    async def _tick_market(self) -> None:
        payload = await self._load_market()
        if payload is None:
            return
        await self._maybe_broadcast("market", payload)

    async def _tick_portfolio(self) -> None:
        if not self._has_private_creds():
            return
        status = self._read_status()
        if status is not None:
            await self._maybe_broadcast("portfolio", _decimalize(status))
        groups = self._read_groups()
        if groups is not None:
            await self._maybe_broadcast("groups", _decimalize(groups))

    async def _tick_health(self) -> None:
        payload = _decimalize(self._build_health())
        await self._maybe_broadcast("health", payload)

    async def _push_live_warm(self, *, status: dict[str, Any] | None, groups: dict[str, Any] | None) -> None:
        if status is not None:
            await self._maybe_broadcast("portfolio", _decimalize(status), force=True)
        if groups is not None:
            await self._maybe_broadcast("groups", _decimalize(groups), force=True)

    async def _maybe_broadcast(self, channel: str, payload: Any, *, force: bool = False) -> None:
        digest = _payload_hash(payload)
        if not force and self._last_hashes.get(channel) == digest:
            return
        self._last_hashes[channel] = digest
        await self._broadcast(channel, payload)

    async def _broadcast(self, channel: str, payload: Any) -> None:
        message = self._envelope(channel, payload)
        async with self._clients_lock:
            targets = [(ws, chans) for ws, chans in self._clients.items() if channel in chans]
        if not targets:
            return
        dead: list[Any] = []
        for ws, _chans in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._clients_lock:
                for ws in dead:
                    self._clients.pop(ws, None)

    @staticmethod
    def _envelope(channel: str, payload: Any) -> dict[str, Any]:
        return {
            "type": "update",
            "channel": channel,
            "ts_ms": utc_now_ms(),
            "data": payload,
        }
