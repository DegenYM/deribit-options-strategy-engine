from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...utils import to_decimal
from .context import RouteContext


def register_stress_routes(app: Any, ctx: RouteContext) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    import deribit_engine.frontend_server as pkg

    @app.get("/api/stress")
    def api_stress(shocks: str = Query(default="0.10,0.20,0.30,0.40,0.50")) -> Any:
        if not any(pkg._has_private_creds(account.config) for account in ctx.accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        shock_decimals: list[Decimal] = []
        for raw in str(shocks or "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            value = to_decimal(raw)
            if value <= 0:
                continue
            shock_decimals.append(-value)
        if not shock_decimals:
            raise HTTPException(status_code=400, detail="no valid shocks")

        def _compute() -> dict[str, Any]:
            if ctx.multi_account:
                return ctx.locked_aggregate_stress(shock_decimals)
            account = ctx.accounts[0]
            cfg = pkg.load_config(account.env_file, require_private=True)
            prefetch = pkg._exchange_prefetch_for_account(account, cache=ctx.exchange_prefetch_cache)
            if prefetch is not None:
                result = pkg.compute_stress_from_prefetch(
                    cfg,
                    prefetch,
                    shocks=shock_decimals,
                    client=pkg.DeribitClient(cfg),
                )
            else:
                result = pkg.compute_current_stress(cfg, pkg.DeribitClient(cfg), shocks=shock_decimals)
            return pkg._stress_result_payload(result)

        try:
            payload = ctx.stress_cache.get_or_set(("stress", shocks), _compute)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"stress failed: {exc}") from exc
        return JSONResponse(pkg._decimalize(payload))
