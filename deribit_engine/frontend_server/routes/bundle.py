from __future__ import annotations

import copy
import logging
from decimal import Decimal
from typing import Any

from .context import RouteContext

LOGGER = logging.getLogger(__name__)

_BUNDLE_SECTIONS = frozenset({"status", "groups", "realized_summary"})
_DEFAULT_BUNDLE_SECTIONS = frozenset({"status", "groups", "realized_summary"})


def _parse_bundle_sections(raw: str | None) -> frozenset[str]:
    if raw is None or not raw.strip():
        return _DEFAULT_BUNDLE_SECTIONS
    parts = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = parts - _BUNDLE_SECTIONS
    if unknown:
        allowed = ", ".join(sorted(_BUNDLE_SECTIONS))
        unknown_list = ", ".join(sorted(unknown))
        raise ValueError(f"unknown dashboard_bundle sections: {unknown_list}; allowed: {allowed}")
    if not parts:
        raise ValueError("dashboard_bundle sections must include at least one section")
    return frozenset(parts)


def register_bundle_routes(app: Any, ctx: RouteContext) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    import deribit_engine.frontend_server as pkg

    @app.get("/api/dashboard_bundle")
    def api_dashboard_bundle(
        days: int = Query(default=30, ge=0, le=3650),
        effective_capital_usdc: float | None = Query(default=None, ge=0),
        sections: str | None = Query(
            default=None,
            description="Comma-separated subset of status,groups,realized_summary",
        ),
    ) -> Any:
        """Status + groups + realized summary in one Deribit prefetch pass."""
        if not any(pkg._has_private_creds(account.config) for account in ctx.accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            selected_sections = _parse_bundle_sections(sections)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        override = (
            Decimal(str(effective_capital_usdc))
            if effective_capital_usdc is not None and effective_capital_usdc > 0
            else None
        )
        cache_key = (
            "dashboard_bundle",
            days,
            str(override) if override is not None else "",
            pkg._ledger_equity_cache_key(ctx.accounts),
            pkg._closed_groups_cache_key(ctx.accounts),
            ",".join(sorted(selected_sections)),
        )

        def _compute() -> dict[str, Any]:
            payload = ctx.locked_compute_dashboard_bundle(
                days=days,
                override=override,
                sections=selected_sections,
            )
            ctx.seed_bundle_component_caches(
                status=payload.get("status"),
                groups=payload.get("groups"),
                summary=payload.get("realized_summary"),
                days=days,
                override=override,
            )
            return payload

        try:
            payload = copy.deepcopy(ctx.bundle_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            stale = ctx.bundle_cache.get_stale(cache_key)
            if stale is not None:
                LOGGER.warning("dashboard /api/dashboard_bundle using stale cache: %s", exc)
                payload = copy.deepcopy(stale)
                headers: dict[str, str] = {"X-Cache-Stale": "true"}
                return JSONResponse(pkg._decimalize(ctx.finalize_dashboard_bundle(payload)), headers=headers)
            LOGGER.warning("dashboard /api/dashboard_bundle failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"dashboard bundle failed: {exc}") from exc
        headers: dict[str, str] = {}
        age_ms = ctx.bundle_cache.cache_age_ms(cache_key)
        if age_ms is not None:
            headers["X-Cache-Age-Ms"] = str(age_ms)
        return JSONResponse(pkg._decimalize(ctx.finalize_dashboard_bundle(payload)), headers=headers)

    @app.get("/api/status")
    def api_status() -> Any:
        if not any(pkg._has_private_creds(account.config) for account in ctx.accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            payload = ctx.status_cache.get_or_set("status", ctx.locked_aggregate_status)
        except Exception as exc:  # noqa: BLE001
            stale = ctx.status_cache.get_stale("status")
            if stale is not None:
                LOGGER.warning("dashboard /api/status using stale cache: %s", exc)
                payload = stale
                headers = {"X-Cache-Stale": "true"}
                age_ms = ctx.status_cache.cache_age_ms("status")
                if age_ms is not None:
                    headers["X-Cache-Age-Ms"] = str(age_ms)
                return JSONResponse(pkg._decimalize(payload), headers=headers)
            LOGGER.warning("dashboard /api/status aggregate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"status failed: {exc}") from exc
        headers: dict[str, str] = {}
        age_ms = ctx.status_cache.cache_age_ms("status")
        if age_ms is not None:
            headers["X-Cache-Age-Ms"] = str(age_ms)
        return JSONResponse(pkg._decimalize(payload), headers=headers)

    @app.get("/api/report")
    def api_report(days: int = Query(default=30, ge=0, le=3650)) -> Any:
        if not any(pkg._has_private_creds(account.config) for account in ctx.accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            payload = ctx.report_cache.get_or_set(("report", days), lambda: ctx.locked_aggregate_report(days))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/report aggregate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"report failed: {exc}") from exc
        return JSONResponse(pkg._decimalize(payload))
