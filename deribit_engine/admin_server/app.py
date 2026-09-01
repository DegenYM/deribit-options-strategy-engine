from __future__ import annotations

import logging
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..env_layout import find_repo_root
from ..exceptions import AuthenticationError, ConfigurationError, ExchangeError
from .catalog import DEFAULT_LOCAL_HOST, build_admin_catalog


class AdminTradePayload(BaseModel):
    account: str | None = None
    group_id: str | None = None
    live: bool = False
    confirm: str | None = None


LOGGER = logging.getLogger(__name__)

DEFAULT_ADMIN_PORT = 8750
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
ALLOWED_REQUEST_HOSTS = LOOPBACK_HOSTS | {"testserver"}
FRONTEND_ACTIONS = frozenset({"start", "stop", "restart"})


def frontend_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend"


def assert_admin_bind_host(host: str, *, allow_public: bool) -> None:
    normalized = str(host or "").strip().lower()
    if allow_public:
        LOGGER.warning("admin console binding %s with --allow-public (not for investor tunnels)", normalized)
        return
    if normalized in LOOPBACK_HOSTS:
        return
    try:
        if ip_address(normalized).is_loopback:
            return
    except ValueError:
        pass
    raise ConfigurationError(
        f"admin console refuses to bind {host!r}; use 127.0.0.1 (or --allow-public for a deliberate exception)"
    )


def _request_host(request: Any) -> str:
    raw = str(request.headers.get("host") or "")
    host = raw.split("%")[0].split(":")[0].strip().lower()
    if host.startswith("[") and host.endswith("]"):
        return host
    return host


def create_admin_app(*, repo_root: Path | str | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
        from fastapi.staticfiles import StaticFiles
        from starlette.middleware.gzip import GZipMiddleware
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fastapi/uvicorn not installed; run `pip install -r requirements.txt`") from exc

    resolved_root = Path(repo_root) if repo_root is not None else find_repo_root(Path.cwd())
    assets = frontend_dir()

    app = FastAPI(title="Deribit Admin Console", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{DEFAULT_ADMIN_PORT}",
            f"http://localhost:{DEFAULT_ADMIN_PORT}",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.middleware("http")
    async def _loopback_hosts_only(request: Any, call_next: Any) -> Any:
        host = _request_host(request)
        if host and host not in ALLOWED_REQUEST_HOSTS:
            return JSONResponse(
                {"detail": "admin console is loopback-only"},
                status_code=403,
            )
        return await call_next(request)

    def _root() -> Path:
        if resolved_root is not None:
            return resolved_root
        found = find_repo_root(Path.cwd())
        if found is None:
            raise HTTPException(status_code=500, detail="Cannot locate repository root")
        return found

    @app.get("/api/admin/health")
    def api_admin_health() -> dict[str, Any]:
        return {
            "ok": True,
            "role": "admin",
            "loopback_only": True,
        }

    @app.get("/api/admin/investors")
    def api_admin_investors(probe: bool = True) -> Any:
        try:
            return build_admin_catalog(repo_root=_root(), probe=probe)
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/admin/investors/{investor_id}/targets")
    def api_admin_targets(investor_id: str) -> Any:
        from .actions import list_investor_targets

        try:
            return list_investor_targets(investor_id, repo_root=_root())
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/investors/{investor_id}/close-position")
    def api_admin_close_position(investor_id: str, payload: AdminTradePayload | None = None) -> Any:
        from .actions import run_close_position

        body = payload or AdminTradePayload()
        try:
            return run_close_position(
                investor_id,
                repo_root=_root(),
                account=body.account,
                group_id=str(body.group_id or ""),
                live=bool(body.live),
                confirm=body.confirm,
            )
        except (ConfigurationError, ExchangeError, AuthenticationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/investors/{investor_id}/panic-close")
    def api_admin_panic_close(investor_id: str, payload: AdminTradePayload | None = None) -> Any:
        from .actions import run_panic_close

        body = payload or AdminTradePayload()
        try:
            return run_panic_close(
                investor_id,
                repo_root=_root(),
                account=body.account,
                live=bool(body.live),
                confirm=body.confirm,
            )
        except (ConfigurationError, ExchangeError, AuthenticationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/investors/{investor_id}/spot-restore")
    def api_admin_spot_restore(investor_id: str, payload: AdminTradePayload | None = None) -> Any:
        from .actions import run_spot_restore

        body = payload or AdminTradePayload()
        try:
            return run_spot_restore(
                investor_id,
                repo_root=_root(),
                account=body.account,
                group_id=str(body.group_id or ""),
                live=bool(body.live),
                confirm=body.confirm,
            )
        except (ConfigurationError, ExchangeError, AuthenticationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/frontend/{investor_id}/{action}")
    def api_admin_frontend_action(investor_id: str, action: str) -> Any:
        normalized = str(action or "").strip().lower()
        if normalized not in FRONTEND_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported action {action!r}; use start, stop, or restart",
            )
        from ..investor_frontend_launchd import manage_frontend_launchd
        from ..investor_registry import validate_investor_id

        try:
            investor = validate_investor_id(investor_id)
            results = manage_frontend_launchd(
                normalized,  # type: ignore[arg-type]
                repo_root=_root(),
                investor_id=investor,
                include_disabled=True,
                check_health=True,
            )
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not results:
            raise HTTPException(status_code=404, detail=f"investor {investor_id!r} not found")
        row = results[0].to_dict()
        return {
            "ok": bool(row.get("ok")),
            "action": normalized,
            "result": row,
        }

    @app.get("/", include_in_schema=False)
    def admin_root() -> Any:
        return RedirectResponse("/admin.html", status_code=302)

    @app.get("/index.html", include_in_schema=False)
    def admin_index_alias() -> Any:
        return RedirectResponse("/admin.html", status_code=302)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> Any:
        svg_path = assets / "favicon.svg"
        if svg_path.is_file():
            return FileResponse(svg_path, media_type="image/svg+xml")
        return Response(status_code=204)

    if assets.is_dir():
        admin_html = assets / "admin.html"

        @app.get("/admin.html", include_in_schema=False)
        def admin_page() -> Any:
            if not admin_html.is_file():
                raise HTTPException(status_code=404, detail="admin.html not found")
            return Response(
                content=admin_html.read_text(encoding="utf-8"),
                media_type="text/html",
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        app.mount("/", StaticFiles(directory=str(assets), html=False), name="frontend")
    else:  # pragma: no cover
        LOGGER.warning("frontend dir not found at %s; admin UI disabled", assets)

    return app


def serve_admin(
    *,
    host: str = DEFAULT_LOCAL_HOST,
    port: int = DEFAULT_ADMIN_PORT,
    allow_public: bool = False,
    log_level: str = "info",
    repo_root: Path | str | None = None,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn not installed; run `pip install -r requirements.txt`") from exc

    assert_admin_bind_host(host, allow_public=allow_public)
    app = create_admin_app(repo_root=repo_root)
    uvicorn.run(app, host=host, port=port, log_level=log_level)
