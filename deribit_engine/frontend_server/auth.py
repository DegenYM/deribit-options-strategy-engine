"""Optional shared-token gate for the dashboard / admin APIs.

Both servers historically relied on the network perimeter (Cloudflare Access for
the investor portals, loopback-only for the admin console). This module adds a
defence-in-depth layer: when a token is configured, every ``/api/*`` request and
every ``/ws/*`` handshake must present it. Static assets stay public so the page
shell can still load and prompt for / read the token.

Env knobs (read via ``os.environ`` so the frontend can run without touching
``BotConfig``):

``DASHBOARD_API_TOKEN``
    Shared secret for the dashboard API. Empty / unset disables the gate
    (preserving the historical local-use behaviour).
``DASHBOARD_API_TOKEN_EMBED``
    ``true`` → the served ``index.html`` / ``investor*.html`` get a
    ``<meta name="dashboard-api-token">`` so the bundled JS can authenticate
    without operator setup. Only sensible behind Cloudflare Access (anyone who
    can load the HTML also gets the token). Default ``false``.
``ADMIN_CONSOLE_TOKEN`` / ``ADMIN_CONSOLE_TOKEN_EMBED``
    Same pair for the local admin console (``X-Admin-Token`` header).
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

DASHBOARD_TOKEN_ENV = "DASHBOARD_API_TOKEN"
DASHBOARD_TOKEN_EMBED_ENV = "DASHBOARD_API_TOKEN_EMBED"
DASHBOARD_TOKEN_HEADER = "x-dashboard-token"
DASHBOARD_TOKEN_META = "dashboard-api-token"

ADMIN_TOKEN_ENV = "ADMIN_CONSOLE_TOKEN"
ADMIN_TOKEN_EMBED_ENV = "ADMIN_CONSOLE_TOKEN_EMBED"
ADMIN_TOKEN_HEADER = "x-admin-token"
ADMIN_TOKEN_META = "admin-console-token"


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE_VALUES


def configured_token(env_name: str) -> str:
    """Return the configured shared token or ``""`` when the gate is disabled."""
    return (os.environ.get(env_name) or "").strip()


def token_matches(candidate: str | None, expected: str) -> bool:
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _header_lookup(raw_headers: Iterable[tuple[bytes, bytes]], name: str) -> str | None:
    wanted = name.lower().encode("latin-1")
    for key, value in raw_headers:
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


def request_presents_token(
    scope: dict[str, Any],
    *,
    expected: str,
    header_name: str,
    allow_query: bool,
) -> bool:
    """True when the ASGI ``scope`` carries the expected token.

    Accepts ``Authorization: Bearer <token>`` or ``<header_name>: <token>``; for
    websocket handshakes (where browsers cannot set headers) also ``?token=``.
    """
    raw_headers = scope.get("headers") or ()
    candidates: list[str | None] = [
        _bearer_token(_header_lookup(raw_headers, "authorization")),
        _header_lookup(raw_headers, header_name),
    ]
    if allow_query:
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"), keep_blank_values=False)
        candidates.extend(query.get("token") or [])
    return any(token_matches(candidate, expected) for candidate in candidates)


class SharedTokenGate:
    """Pure ASGI middleware: 401 / 4401 for gated paths without a valid token.

    HTTP requests get ``401 {"detail": "..."}`` with ``WWW-Authenticate: Bearer``;
    websocket handshakes are closed before ``accept`` (the server answers 403).
    Paths outside ``gated_prefixes`` (static HTML/JS/CSS) pass through untouched.
    """

    def __init__(
        self,
        app: Any,
        *,
        token: str,
        header_name: str,
        gated_prefixes: tuple[str, ...] = ("/api/", "/ws/"),
        detail: str = "unauthorized: missing or invalid dashboard token",
    ) -> None:
        self.app = app
        self._token = token
        self._header = header_name
        self._prefixes = gated_prefixes
        self._detail = detail

    def _is_gated(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._prefixes)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket") or not self._token:
            await self.app(scope, receive, send)
            return
        if not self._is_gated(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return
        if request_presents_token(
            scope,
            expected=self._token,
            header_name=self._header,
            allow_query=scope_type == "websocket",
        ):
            await self.app(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 4401, "reason": "unauthorized"})
            return
        from starlette.responses import JSONResponse

        response = JSONResponse(
            {"detail": self._detail},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def inject_token_meta(html: str, *, meta_name: str, token: str) -> str:
    """Insert ``<meta name=... content=...>`` before ``</head>`` (or prepend)."""
    import html as html_mod

    snippet = f'<meta name="{meta_name}" content="{html_mod.escape(token, quote=True)}" />'
    if "</head>" in html:
        return html.replace("</head>", f"  {snippet}\n  </head>", 1)
    return snippet + html


def parse_cors_origins(raw: str | None) -> list[str]:
    """Comma-separated ``DASHBOARD_CORS_ORIGINS`` → list; empty → no CORS middleware."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]
