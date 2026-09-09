from __future__ import annotations

from pathlib import Path
from typing import Any

from ..auth import DASHBOARD_TOKEN_META, inject_token_meta

# Only these root-level files are ever served. ``frontend/`` also holds
# ``node_modules/``, ``src/``, ``package-lock.json``, ``e2e/``,
# ``test-results/`` … which must never be reachable from the public origin.
HTML_ASSETS: tuple[str, ...] = ("index.html", "investor.html", "investor.zh.html", "admin.html")
ROOT_ASSET_MEDIA_TYPES: dict[str, str] = {
    "app.js": "application/javascript",
    "app-investor.js": "application/javascript",
    "styles.css": "text/css",
    "tailwind.css": "text/css",
    "tokens.css": "text/css",
    "favicon.svg": "image/svg+xml",
}
VENDOR_DIR_NAME = "vendor"


def bundle_cache_headers(version: str | None) -> dict[str, str]:
    """Long-cache hash-stamped bundle URLs; revalidate unversioned ones.

    build.mjs stamps every ``<script src>`` / ``<link href>`` with a hash of the
    file, so a ``?v=`` request can never go stale — serving it ``no-cache`` only
    buys a wasted revalidation round-trip on every page load.
    """
    if version:
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-cache, must-revalidate"}


def register_static_routes(
    app: Any,
    *,
    frontend_dir: Path,
    investor_portal: bool,
    dashboard_strategies_list: list[str],
    embed_api_token: str | None = None,
) -> None:
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, RedirectResponse, Response
    from fastapi.staticfiles import StaticFiles

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> Any:
        """Serve SVG at /favicon.ico so tab requests stop logging 404."""
        svg_path = frontend_dir / "favicon.svg"
        if svg_path.is_file():
            return FileResponse(svg_path, media_type="image/svg+xml")
        return Response(status_code=204)

    if not frontend_dir.is_dir():
        return

    def _render_html(path: Path, *, inject_strategies: bool) -> Any:
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{path.name} not found")
        body = path.read_text(encoding="utf-8")
        if inject_strategies and dashboard_strategies_list:
            import json

            snippet = f"<script>window.__DASHBOARD_STRATEGIES__={json.dumps(list(dashboard_strategies_list))};</script>"
            if "</head>" in body:
                body = body.replace("</head>", f"  {snippet}\n  </head>", 1)
            else:
                body = snippet + body
        if embed_api_token:
            body = inject_token_meta(body, meta_name=DASHBOARD_TOKEN_META, token=embed_api_token)
        return Response(
            content=body,
            media_type="text/html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    def _make_html_handler(path: Path, *, inject_strategies: bool) -> Any:
        def _html_handler() -> Any:
            return _render_html(path, inject_strategies=inject_strategies)

        return _html_handler

    if investor_portal:

        @app.get("/", include_in_schema=False)
        def investor_portal_root() -> Any:
            return RedirectResponse("/investor.html", status_code=302)

    else:

        @app.get("/", include_in_schema=False)
        def ops_root() -> Any:
            return _render_html(frontend_dir / "index.html", inject_strategies=False)

    for html_name in HTML_ASSETS:
        app.add_api_route(
            f"/{html_name}",
            _make_html_handler(
                frontend_dir / html_name,
                inject_strategies=html_name.startswith("investor") or investor_portal,
            ),
            methods=["GET"],
            include_in_schema=False,
        )

    vendor_dir = frontend_dir / VENDOR_DIR_NAME
    if vendor_dir.is_dir():
        app.mount(
            f"/{VENDOR_DIR_NAME}",
            StaticFiles(directory=str(vendor_dir), html=False),
            name="frontend-vendor",
        )

    @app.get("/{asset_name}", include_in_schema=False)
    def root_asset(asset_name: str, v: str | None = None) -> Any:
        """Allowlisted root files only; everything else in ``frontend/`` is 404."""
        media_type = ROOT_ASSET_MEDIA_TYPES.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="Not Found")
        path = frontend_dir / asset_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{asset_name} not found")
        return FileResponse(path, media_type=media_type, headers=bundle_cache_headers(v))
