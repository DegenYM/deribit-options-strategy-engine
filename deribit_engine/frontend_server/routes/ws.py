from __future__ import annotations

import logging
import os
from typing import Any

from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..dashboard_ws import DashboardWsHub, parse_ws_channels

LOGGER = logging.getLogger(__name__)


def _ws_enabled() -> bool:
    raw = os.environ.get("FRONTEND_WS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def register_ws_routes(app: Any, *, ws_hub: DashboardWsHub | None) -> None:
    if ws_hub is None or not _ws_enabled():
        return

    async def dashboard_ws_endpoint(websocket: WebSocket) -> None:
        raw_channels = websocket.query_params.get("channels", "market,portfolio,groups")
        try:
            selected = parse_ws_channels(raw_channels)
        except ValueError as exc:
            await websocket.close(code=4400, reason=str(exc))
            return

        await websocket.accept()
        await ws_hub.connect(websocket, selected)
        try:
            while True:
                # Clients may send pings or subscribe tweaks later; discard for now.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("dashboard websocket closed: %s", exc)
        finally:
            await ws_hub.disconnect(websocket)

    app.router.routes.insert(0, WebSocketRoute("/ws/dashboard", endpoint=dashboard_ws_endpoint))
