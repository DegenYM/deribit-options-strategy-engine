from __future__ import annotations

from pathlib import Path
from typing import Any


def register_static_routes(
    app: Any,
    *,
    frontend_dir: Path,
    investor_portal: bool,
    dashboard_strategies_list: list[str],
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

    if investor_portal:

        @app.get("/", include_in_schema=False)
        def investor_portal_root() -> Any:
            return RedirectResponse("/investor.html", status_code=302)

    if frontend_dir.is_dir():

        @app.get("/app.js", include_in_schema=False)
        def app_js() -> Any:
            """Always serve fresh app.js (investor portal caches aggressively via CDN)."""
            path = frontend_dir / "app.js"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="app.js not found")
            return FileResponse(
                path,
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        @app.get("/app-investor.js", include_in_schema=False)
        def app_investor_js() -> Any:
            path = frontend_dir / "app-investor.js"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="app-investor.js not found")
            return FileResponse(
                path,
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        for html_name in ("index.html", "investor.html", "investor.zh.html"):
            html_path = frontend_dir / html_name

            def _make_html_handler(path: Path, *, inject_strategies: bool) -> Any:
                def _html_handler() -> Any:
                    if not path.is_file():
                        raise HTTPException(status_code=404, detail=f"{path.name} not found")
                    body = path.read_text(encoding="utf-8")
                    if inject_strategies and dashboard_strategies_list:
                        import json

                        snippet = (
                            "<script>window.__DASHBOARD_STRATEGIES__="
                            f"{json.dumps(list(dashboard_strategies_list))};</script>"
                        )
                        if "</head>" in body:
                            body = body.replace("</head>", f"  {snippet}\n  </head>", 1)
                        else:
                            body = snippet + body
                    return Response(
                        content=body,
                        media_type="text/html",
                        headers={"Cache-Control": "no-cache, must-revalidate"},
                    )

                return _html_handler

            app.add_api_route(
                f"/{html_name}",
                _make_html_handler(
                    html_path,
                    inject_strategies=html_name.startswith("investor") or investor_portal,
                ),
                methods=["GET"],
                include_in_schema=False,
            )

        app.mount(
            "/",
            StaticFiles(directory=str(frontend_dir), html=True),
            name="frontend",
        )
