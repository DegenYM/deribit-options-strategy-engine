from __future__ import annotations

import copy
import logging
from typing import Any

from .context import RouteContext

LOGGER = logging.getLogger(__name__)


def register_groups_routes(app: Any, ctx: RouteContext) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    import deribit_engine.frontend_server as pkg

    @app.get("/api/groups")
    def api_groups(
        snapshot: bool = Query(default=False, description="Disk-only groups (no Deribit prefetch)"),
    ) -> Any:
        if snapshot:
            try:
                spot_idx = pkg._spot_index_decimals(ctx.spot_cache.get_or_set("spot", ctx.fetch_spot))
                payload = copy.deepcopy(pkg._aggregate_groups_disk_only(ctx.accounts, spot_index=spot_idx or None))
                pkg._apply_spot_native_backfill(payload, spot_idx)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("dashboard /api/groups?snapshot=1 failed: %s", exc)
                raise HTTPException(status_code=500, detail=f"groups snapshot failed: {exc}") from exc
            return JSONResponse(pkg._decimalize(payload))

        cache_key = ("groups", pkg._closed_groups_cache_key(ctx.accounts))

        def _compute() -> dict[str, Any]:
            return pkg._aggregate_groups(ctx.accounts, exchange_prefetch_cache=ctx.exchange_prefetch_cache)

        try:
            payload = copy.deepcopy(ctx.groups_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            stale = ctx.groups_cache.get_stale(cache_key)
            if stale is not None:
                LOGGER.warning("dashboard /api/groups using stale cache: %s", exc)
                payload = copy.deepcopy(stale)
            else:
                raise HTTPException(status_code=500, detail=f"groups failed: {exc}") from exc
        try:
            spot_idx = pkg._spot_index_decimals(ctx.spot_cache.get_or_set("spot", ctx.fetch_spot))
            pkg._apply_spot_native_backfill(payload, spot_idx)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("groups spot native backfill skipped: %s", exc)
        return JSONResponse(pkg._decimalize(payload))
