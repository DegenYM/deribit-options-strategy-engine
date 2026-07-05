from __future__ import annotations

import threading
from typing import Any

from .context import RouteContext


def register_transfers_routes(app: Any, ctx: RouteContext) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    import deribit_engine.frontend_server as pkg

    from ..transfers_service import build_transfers_payload_from_store

    def _spot_index() -> dict:
        return pkg._spot_index_decimals(ctx.spot_cache.get_or_set("spot", ctx.fetch_spot))

    def _cache_key(days: int, limit: int) -> tuple:
        return ("transfers", days, limit, pkg._ledger_equity_cache_key(ctx.accounts))

    def _compute_live(*, days: int, limit: int, index_by_ccy: dict) -> dict[str, Any]:
        return ctx.locked_aggregate_transfers(days=days, limit=limit, index_by_ccy=index_by_ccy)

    def _schedule_live_refresh(*, days: int, limit: int, index_by_ccy: dict) -> None:
        cache_key = _cache_key(days, limit)

        def _run() -> None:
            try:
                payload = _compute_live(days=days, limit=limit, index_by_ccy=index_by_ccy)
                ctx.transfers_cache.seed(cache_key, payload)
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("transfers background refresh failed: %s", exc)

        threading.Thread(target=_run, name="transfers-bg-refresh", daemon=True).start()

    @app.get("/api/transfers")
    def api_transfers(
        days: int = Query(default=90, ge=1, le=3650),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> Any:
        if not any(pkg._has_private_creds(account.config) for account in ctx.accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")

        cache_key = _cache_key(days, limit)
        fresh = ctx.transfers_cache.try_get(cache_key)
        if fresh is not None:
            headers: dict[str, str] = {}
            age_ms = ctx.transfers_cache.cache_age_ms(cache_key)
            if age_ms is not None:
                headers["X-Cache-Age-Ms"] = str(age_ms)
            return JSONResponse(pkg._decimalize(fresh), headers=headers)

        try:
            spot_idx = _spot_index()
        except Exception as exc:  # noqa: BLE001
            spot_idx = {}

        store_payload = build_transfers_payload_from_store(
            ctx.accounts,
            days=days,
            index_by_ccy=spot_idx,
            limit_per_account=limit,
        )
        if store_payload is not None:
            _schedule_live_refresh(days=days, limit=limit, index_by_ccy=spot_idx)
            headers = {"X-Transfer-Store": "true"}
            return JSONResponse(pkg._decimalize(store_payload), headers=headers)

        def _compute() -> dict[str, Any]:
            return _compute_live(days=days, limit=limit, index_by_ccy=_spot_index())

        try:
            payload = ctx.transfers_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            stale = ctx.transfers_cache.get_stale(cache_key)
            if stale is not None:
                return JSONResponse(pkg._decimalize(stale), headers={"X-Cache-Stale": "true"})
            raise HTTPException(status_code=502, detail=f"transfers failed: {exc}") from exc
        headers = {}
        age_ms = ctx.transfers_cache.cache_age_ms(cache_key)
        if age_ms is not None:
            headers["X-Cache-Age-Ms"] = str(age_ms)
        return JSONResponse(pkg._decimalize(payload), headers=headers)
