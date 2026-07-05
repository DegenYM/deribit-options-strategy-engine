from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .routes.bundle import register_bundle_routes
from .routes.core import register_core_routes
from .routes.groups import register_groups_routes
from .routes.static import register_static_routes
from .routes.stress import register_stress_routes
from .routes.transfers import register_transfers_routes
from .routes.ws import register_ws_routes
from .runtime_setup import build_runtime_setup

LOGGER = logging.getLogger(__name__)


def create_app(
    *,
    env_file: str | Path = ".env",
    account_env_files: tuple[str | Path, ...] | None = None,
    enable_scheduler: bool = True,
    snapshot_interval_sec: int | None = None,
    investor_portal: bool = False,
    skipped_accounts: tuple[dict[str, str], ...] | None = None,
) -> Any:
    """Build the FastAPI application.

    Imports are local so the rest of the package stays usable on machines
    that haven't installed FastAPI/uvicorn yet.
    """
    try:
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from starlette.middleware.gzip import GZipMiddleware
    except ImportError as exc:  # pragma: no cover — surfaces a clear hint.
        raise RuntimeError("fastapi/uvicorn not installed; run `pip install -r requirements.txt`") from exc

    runtime = build_runtime_setup(
        env_file=env_file,
        account_env_files=account_env_files,
        enable_scheduler=enable_scheduler,
        snapshot_interval_sec=snapshot_interval_sec,
        investor_portal=investor_portal,
        skipped_accounts=skipped_accounts,
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        if runtime.ws_hub is not None:
            await runtime.ws_hub.start()
        if enable_scheduler:
            for scheduler in runtime.background_schedulers:
                scheduler.start()
        try:
            yield
        finally:
            if runtime.ws_hub is not None:
                await runtime.ws_hub.stop()
            for scheduler in runtime.background_schedulers:
                scheduler.stop()

    app = FastAPI(
        title="Deribit Strategy Dashboard",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.middleware("http")
    async def _static_long_cache_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/vendor/") or (path.endswith(".css") and request.query_params.get("v")):
            response.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
        return response

    register_core_routes(app, runtime)
    register_bundle_routes(app, runtime.route_ctx)
    register_groups_routes(app, runtime.route_ctx)
    register_stress_routes(app, runtime.route_ctx)
    register_transfers_routes(app, runtime.route_ctx)
    register_ws_routes(app, ws_hub=runtime.ws_hub)

    if runtime.frontend_dir.is_dir():
        register_static_routes(
            app,
            frontend_dir=runtime.frontend_dir,
            investor_portal=investor_portal,
            dashboard_strategies_list=runtime.dashboard_strategies_list,
        )
    else:  # pragma: no cover — should always exist in repo.
        LOGGER.warning("frontend dir not found at %s; static UI disabled", runtime.frontend_dir)

    return app


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    env_file: str | Path = ".env",
    account_env_files: tuple[str | Path, ...] | None = None,
    enable_scheduler: bool = True,
    snapshot_interval_sec: int | None = None,
    investor_portal: bool = False,
    log_level: str = "info",
    skipped_accounts: tuple[dict[str, str], ...] | None = None,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover — clear hint.
        raise RuntimeError("uvicorn not installed; run `pip install -r requirements.txt`") from exc

    app = create_app(
        env_file=env_file,
        account_env_files=account_env_files,
        enable_scheduler=enable_scheduler,
        snapshot_interval_sec=snapshot_interval_sec,
        investor_portal=investor_portal,
        skipped_accounts=skipped_accounts,
    )
    LOGGER.info("serving dashboard on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=int(port), log_level=log_level)
