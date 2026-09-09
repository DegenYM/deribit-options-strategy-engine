"""Read-only catalog of investor frontends for the local admin console."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..env_layout import find_repo_root
from ..exceptions import ConfigurationError
from ..frontend_server.auth import DASHBOARD_TOKEN_ENV, configured_token
from ..investor_ops import list_investors
from ..utils import utc_now_ms

DEFAULT_LOCAL_HOST = "127.0.0.1"
HEALTH_PATH = "/api/health"
DEFAULT_HEALTH_TIMEOUT_SEC = 1.5
_HEALTH_USER_AGENT = "deribit-admin-console/1.0"


def local_page_url(port: int | None, path: str, *, local_host: str = DEFAULT_LOCAL_HOST) -> str | None:
    if port is None:
        return None
    normalized = path if path.startswith("/") else f"/{path}"
    return f"http://{local_host}:{int(port)}{normalized}"


def _as_port(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_frontend_health(
    port: int | None,
    *,
    local_host: str = DEFAULT_LOCAL_HOST,
    timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SEC,
) -> dict[str, Any]:
    """GET ``/api/health`` on a local investor frontend. Never raises."""
    url = local_page_url(port, HEALTH_PATH, local_host=local_host)
    if url is None:
        return {
            "ok": False,
            "url": None,
            "status_code": None,
            "elapsed_ms": None,
            "error": "missing frontend_port",
        }

    headers = {"User-Agent": _HEALTH_USER_AGENT}
    # Investor frontends gated by DASHBOARD_API_TOKEN answer 401 otherwise; the
    # admin console shares the operator's env so it can present the same token.
    dashboard_token = configured_token(DASHBOARD_TOKEN_ENV)
    if dashboard_token:
        headers["X-Dashboard-Token"] = dashboard_token
    request = urllib.request.Request(url, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", response.getcode()))
            response.read(512)
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "url": url,
            "status_code": int(exc.code),
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "error": f"HTTP {exc.code}",
        }
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None)
        return {
            "ok": False,
            "url": url,
            "status_code": None,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "error": str(reason or exc),
        }

    elapsed_ms = (time.monotonic() - started) * 1000
    ok = 200 <= status_code < 300
    return {
        "ok": ok,
        "url": url,
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": None if ok else f"HTTP {status_code}",
    }


def _catalog_row(
    row: dict[str, Any],
    *,
    local_host: str,
    health: dict[str, Any],
) -> dict[str, Any]:
    port = _as_port(row.get("frontend_port"))
    accounts = list(row.get("accounts") or [])
    return {
        "investor_id": row.get("investor_id"),
        "display_name": row.get("display_name") or row.get("investor_id"),
        "dashboard_email": row.get("dashboard_email") or None,
        "hostname": row.get("hostname") or None,
        "frontend_port": port,
        "frontend_enabled": bool(row.get("frontend_enabled")),
        "live_enabled": bool(row.get("live_enabled")),
        "ops_url": local_page_url(port, "/index.html", local_host=local_host),
        "portal_url": local_page_url(port, "/investor.html", local_host=local_host),
        "health": health,
        "accounts": accounts,
        "account_count": len(accounts),
    }


def _skipped_health(port: int | None, *, local_host: str) -> dict[str, Any]:
    return {
        "ok": False,
        "url": local_page_url(port, HEALTH_PATH, local_host=local_host),
        "status_code": None,
        "elapsed_ms": None,
        "error": "probe_disabled",
    }


def build_admin_catalog(
    *,
    repo_root: Path | str | None = None,
    local_host: str = DEFAULT_LOCAL_HOST,
    timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SEC,
    probe: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else find_repo_root(Path.cwd())
    if root is None:
        raise ConfigurationError("Cannot locate repository root (missing deribit_engine/)")

    rows = list_investors(repo_root=root)
    health_by_index: dict[int, dict[str, Any]] = {}
    if probe and rows:
        workers = min(8, max(1, len(rows)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    probe_frontend_health,
                    _as_port(row.get("frontend_port")),
                    local_host=local_host,
                    timeout_seconds=timeout_seconds,
                ): index
                for index, row in enumerate(rows)
            }
            for future in as_completed(futures):
                health_by_index[futures[future]] = future.result()

    investors = [
        _catalog_row(
            row,
            local_host=local_host,
            health=health_by_index.get(index)
            or _skipped_health(_as_port(row.get("frontend_port")), local_host=local_host),
        )
        for index, row in enumerate(rows)
    ]
    healthy = sum(1 for item in investors if (item.get("health") or {}).get("ok"))
    return {
        "generated_at_ms": utc_now_ms(),
        "local_host": local_host,
        "investor_count": len(investors),
        "healthy_count": healthy,
        "investors": investors,
    }
