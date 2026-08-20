from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routes.admin import router as admin_router
from .routes.auth import router as auth_router
from .routes.billing import router as billing_router
from .routes.bot import router as bot_router
from .routes.dashboard import router as dashboard_router


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(
        title="Covered Call SaaS",
        version="0.1.0",
        description="BYOK hosted Covered Call automation. Not investment advice.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(billing_router)
    app.include_router(bot_router)
    app.include_router(dashboard_router)
    app.include_router(admin_router)

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "product": "covered-call-saas",
            "strategy": "covered_call",
            "disclaimer": "Not investment advice. No APR guarantee.",
        }

    @app.get("/api/legal/index")
    def legal_index():
        return {
            "terms": "/legal/TERMS.md",
            "privacy": "/legal/PRIVACY.md",
            "risk_zh": "/legal/RISK_DISCLOSURE.zh-TW.md",
            "risk_en": "/legal/RISK_DISCLOSURE.en.md",
            "marketing": "/legal/MARKETING.md",
        }

    web_dir = settings.web_dir
    legal_dir = Path(__file__).resolve().parents[3] / "legal"
    if legal_dir.is_dir():
        app.mount("/legal", StaticFiles(directory=str(legal_dir)), name="legal")
    if web_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(web_dir)), name="assets")

        @app.get("/")
        def index():
            return FileResponse(web_dir / "index.html")

    return app


app = create_app()
