from __future__ import annotations

import copy
import logging
import os
from decimal import Decimal
from typing import Any

from ...trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state
from ...utils import utc_now_ms
from ..aggregation import _resolve_apr_effective_capital_usdc
from ..constants import DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC
from ..helpers import (
    _backfill_row_collateral_native,
    _cumulative_spot_pnl_series_from_accounts,
    _cumulative_stable_pnl_series,
    _ledger_equity_cache_key,
    _read_ledger,
    _rolling_apr_series_from_store,
    _spot_index_decimals,
    _spot_series_cache_key,
)
from ..runtime_setup import RuntimeSetup

LOGGER = logging.getLogger(__name__)


def register_core_routes(app: Any, runtime: RuntimeSetup) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    import deribit_engine.frontend_server as pkg

    @app.get("/api/spot")
    def api_spot() -> dict[str, Any]:
        """Public BTC/ETH USD index and IV rank for dashboard header (no private auth)."""
        try:
            store = runtime.market_store()
            if store is not None:
                row = store.latest()
                if row is not None:
                    max_age_ms = (
                        int(os.environ.get("MARKET_SNAPSHOT_INTERVAL_SEC", DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC))
                        * 2000
                    )
                    if utc_now_ms() - row.ts_ms <= max_age_ms:
                        return row.to_spot_api_payload()
            return runtime.spot_cache.get_or_set("spot", runtime.fetch_spot)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/spot failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=502,
                detail=runtime.api_error_detail("spot", exc),
            ) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return runtime.build_health_payload()

    @app.get("/api/portfolio/snapshot")
    def api_portfolio_snapshot(
        days: int = Query(default=30, ge=0, le=3650),
    ) -> Any:
        """Last on-disk equity snapshot (no Deribit); for fast investor first paint."""
        if runtime.investor_portal:
            portal_service = runtime.portal_service
            if portal_service is not None:
                cached = portal_service.load_for_api(
                    prefer_live=True,
                    live_max_age_ms=runtime.investor_status_ttl * 1000,
                )
                if cached is not None:
                    return JSONResponse(pkg._decimalize(runtime.enrich_snapshot_payload(cached)))

        payload = runtime.build_ledger_snapshot_payload()
        if payload.get("source") == "none":
            return JSONResponse(pkg._decimalize(payload), status_code=200)
        payload = _attach_realized_summary(
            payload,
            runtime=runtime,
            days=days,
        )
        return JSONResponse(pkg._decimalize(runtime.enrich_snapshot_payload(payload)))

    @app.get("/api/equity_series")
    def api_equity_series(days: int = Query(default=30, ge=1, le=3650)) -> Any:
        since_ms = utc_now_ms() - days * 86400 * 1000
        rows = []
        for account in runtime.accounts:
            rows.extend(_read_ledger(account.ledger_root, since_ms=since_ms))
        rows.sort(key=lambda row: int(row.get("ts_ms") or 0))
        return JSONResponse(
            {
                "days_requested": days,
                "row_count": len(rows),
                "rows": rows,
            }
        )

    @app.get("/api/trade_journal/sync")
    def api_trade_journal_sync() -> Any:
        """Manual one-shot journal sync (normally runs on a background scheduler)."""
        return JSONResponse(runtime.journal_scheduler.run_once())

    @app.get("/api/realized_summary")
    def api_realized_summary(
        days: int = Query(default=30, ge=0, le=3650),
        effective_capital_usdc: float | None = Query(default=None, ge=0),
    ) -> Any:
        override = (
            Decimal(str(effective_capital_usdc))
            if effective_capital_usdc is not None and effective_capital_usdc > 0
            else None
        )
        status_payload = runtime.status_cache.try_get("status")
        capital = _resolve_apr_effective_capital_usdc(
            runtime.accounts,
            override=override,
            status_payload=status_payload,
        )
        cache_key = (
            "realized_summary",
            days,
            str(capital),
            _ledger_equity_cache_key(runtime.accounts),
            pkg._closed_groups_cache_key(runtime.accounts),
        )

        def _compute() -> dict[str, Any]:
            return pkg._aggregate_realized_summary(
                runtime.accounts,
                days=days,
                status_payload=status_payload,
                effective_capital_override=override,
            )

        try:
            payload = copy.deepcopy(runtime.series_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/realized_summary failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=runtime.api_error_detail("realized summary", exc),
            ) from exc
        try:
            from ...hedge_pnl import hedge_performance_adjustments
            from ...realized_summary import patch_realized_report_spot_pnl

            spot_idx = _spot_index_decimals(runtime.spot_cache.get_or_set("spot", runtime.fetch_spot))
            closed_rows = pkg._all_closed_group_rows(runtime.accounts, spot_index=spot_idx)
            hedge_lifetime, hedge_window = hedge_performance_adjustments(
                [account.state_path for account in runtime.accounts],
                window_days=days,
            )
            patch_realized_report_spot_pnl(
                payload,
                closed_rows,
                spot_index=spot_idx,
                window_days=days,
                hedge_lifetime_usdc=hedge_lifetime,
                hedge_window_usdc=hedge_window,
                fill_stats=(status_payload or {}).get("premium_sweep_fill_stats_by_book"),
            )
            for row in payload.get("recent_closed_trades") or []:
                if isinstance(row, dict):
                    _backfill_row_collateral_native(row, spot_idx)
        except Exception as exc:  # noqa: BLE001 — spot backfill must never break the summary.
            LOGGER.debug("realized summary spot native backfill skipped: %s", exc)
        return JSONResponse(pkg._decimalize(payload))

    @app.get("/api/trade_executions")
    def api_trade_executions(
        limit: int = Query(default=200, ge=1, le=2000),
        since_days: int = Query(default=90, ge=1, le=3650),
        group_id: str | None = Query(default=None),
    ) -> Any:
        since_ms = utc_now_ms() - since_days * 86400 * 1000
        rows: list[dict[str, Any]] = []
        per_account = max(1, limit // max(len(runtime.accounts), 1))
        for account in runtime.accounts:
            store = TradeJournalStore(journal_db_path_for_state(account.state_path))
            scope = scope_key_for_state(account.state_path)
            for row in store.list_executions(
                scope,
                limit=per_account,
                since_ms=since_ms,
                group_id=group_id,
            ):
                row["account_name"] = account.name
                rows.append(row)
        rows.sort(key=lambda item: int(item.get("ts_ms") or 0), reverse=True)
        return JSONResponse(
            {
                "since_days": since_days,
                "row_count": len(rows[:limit]),
                "rows": rows[:limit],
            }
        )

    @app.get("/api/cumulative_spot_pnl_series")
    def api_cumulative_spot_pnl_series() -> Any:
        cache_key = (
            "cumulative_spot_pnl",
            pkg._closed_groups_cache_key(runtime.accounts),
            _spot_series_cache_key(runtime.spot_cache.try_get("spot")),
        )

        def _compute() -> dict[str, Any]:
            spot_idx = _spot_index_decimals(runtime.spot_cache.get_or_set("spot", runtime.fetch_spot))
            return _cumulative_spot_pnl_series_from_accounts(runtime.accounts, spot_index=spot_idx)

        try:
            series = runtime.series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/cumulative_spot_pnl_series failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=runtime.api_error_detail("cumulative spot pnl", exc),
            ) from exc
        return JSONResponse(series)

    @app.get("/api/cumulative_pnl_series")
    def api_cumulative_pnl_series() -> Any:
        cache_key = (
            "cumulative_pnl_stable",
            pkg._closed_groups_cache_key(runtime.accounts),
            _spot_series_cache_key(runtime.spot_cache.try_get("spot")),
        )

        def _compute() -> dict[str, Any]:
            spot_idx = _spot_index_decimals(runtime.spot_cache.get_or_set("spot", runtime.fetch_spot))
            return _cumulative_stable_pnl_series(runtime.accounts, spot_index=spot_idx)

        try:
            series = runtime.series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/cumulative_pnl_series failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=runtime.api_error_detail("cumulative pnl", exc),
            ) from exc
        return JSONResponse(series)

    @app.get("/api/apr_series")
    def api_apr_series(
        window_days: int = Query(default=30, ge=1, le=365),
        effective_capital_usdc: float | None = Query(default=None, ge=0),
    ) -> Any:
        override = (
            Decimal(str(effective_capital_usdc))
            if effective_capital_usdc is not None and effective_capital_usdc > 0
            else None
        )
        capital = _resolve_apr_effective_capital_usdc(
            runtime.accounts,
            override=override,
            status_payload=runtime.status_cache.try_get("status"),
        )
        cache_key = (
            "apr_series",
            window_days,
            str(capital),
            _ledger_equity_cache_key(runtime.accounts),
            pkg._closed_groups_cache_key(runtime.accounts),
        )

        def _compute() -> dict[str, Any]:
            rows = _rolling_apr_series_from_store(
                runtime.accounts,
                window_days=window_days,
                effective_capital_usdc=capital,
            )
            return {
                "window_days": window_days,
                "capital_basis": "daily_total_equity_usdc",
                "effective_capital_usdc": str(capital),
                "rows": rows,
            }

        try:
            payload = runtime.series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/apr_series failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=runtime.api_error_detail("apr series", exc),
            ) from exc
        return JSONResponse(payload)
def _attach_realized_summary(
    payload: dict[str, Any],
    *,
    runtime: RuntimeSetup,
    days: int,
) -> dict[str, Any]:
    from ...portal_snapshot_service import attach_realized_summary_to_ledger_snapshot

    return attach_realized_summary_to_ledger_snapshot(
        payload,
        accounts=runtime.accounts,
        days=days,
        status_payload=runtime.status_cache.try_get("status"),
        series_cache=runtime.series_cache,
        spot_cache=runtime.spot_cache,
        fetch_spot=runtime.fetch_spot,
        status_cache=runtime.status_cache,
    )
