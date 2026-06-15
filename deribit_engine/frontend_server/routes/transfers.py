from __future__ import annotations

from typing import Any

from .context import RouteContext


def register_transfers_routes(app: Any, ctx: RouteContext) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    import deribit_engine.frontend_server as pkg

    @app.get("/api/transfers")
    def api_transfers(
        days: int = Query(default=90, ge=1, le=3650),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> Any:
        if not any(pkg._has_private_creds(account.config) for account in ctx.accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")

        cache_key = ("transfers", days, limit, pkg._ledger_equity_cache_key(ctx.accounts))

        def _compute() -> dict[str, Any]:
            spot_idx = pkg._spot_index_decimals(ctx.spot_cache.get_or_set("spot", ctx.fetch_spot))
            return ctx.locked_aggregate_transfers(days=days, limit=limit, index_by_ccy=spot_idx)

        try:
            payload = ctx.transfers_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            stale = ctx.transfers_cache.get_stale(cache_key)
            if stale is not None:
                return JSONResponse(pkg._decimalize(stale), headers={"X-Cache-Stale": "true"})
            raise HTTPException(status_code=502, detail=f"transfers failed: {exc}") from exc
        headers: dict[str, str] = {}
        age_ms = ctx.transfers_cache.cache_age_ms(cache_key)
        if age_ms is not None:
            headers["X-Cache-Age-Ms"] = str(age_ms)
        return JSONResponse(pkg._decimalize(payload), headers=headers)
