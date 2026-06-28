from __future__ import annotations

import copy
import logging
import os
import threading
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..engine import DeribitOptionTrialBot
from ..env_layout import (
    find_repo_root,
    load_investor_manifest,
    resolve_investor_scope,
)
from ..exceptions import ConfigurationError
from ..portal_snapshot_service import PortalSnapshotService, attach_realized_summary_to_ledger_snapshot
from ..trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state
from ..utils import to_decimal, utc_now_ms
from .aggregation import (
    FILL_STATS_CACHE_KEY,
    _resolve_apr_effective_capital_usdc,
    attach_cached_premium_sweep_fill_stats,
)
from .constants import (
    DEFAULT_BUNDLE_WARM_INTERVAL_SEC,
    DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC,
    DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC,
    DEFAULT_PORTAL_SNAPSHOT_DISK_INTERVAL_SEC,
    DEFAULT_PREMIUM_SWEEP_FILL_STATS_CACHE_TTL_SEC,
    DEFAULT_SNAPSHOT_INTERVAL_SEC,
    DEFAULT_TRADE_JOURNAL_SYNC_INTERVAL_SEC,
    GROUPS_CACHE_TTL_SEC,
    REPORT_CACHE_TTL_SEC,
    SERIES_CACHE_TTL_SEC,
    SPOT_CACHE_TTL_SEC,
    STATUS_CACHE_TTL_SEC,
    TRANSFERS_CACHE_TTL_SEC,
)
from .exchange import _bot_for_account
from .groups_service import _closed_groups_cache_key
from .helpers import (
    _apply_spot_native_backfill,
    _backfill_row_collateral_native,
    _configure_metrics_db,
    _cumulative_spot_pnl_series_from_accounts,
    _cumulative_stable_pnl_series,
    _dashboard_strategies,
    _decimalize,
    _has_private_creds,
    _ledger_equity_cache_key,
    _make_dashboard_accounts,
    _ratio,
    _read_ledger,
    _rolling_apr_series_from_store,
    _spot_index_decimals,
    _spot_series_cache_key,
)
from .market_vol import fetch_index_price_change_24h_pct, fetch_iv_rank_snapshot
from .portal_snapshot_scheduler import (
    MarketSnapshotScheduler,
    PortalDiskSnapshotScheduler,
    make_portal_snapshot_service,
)
from .types import (
    BundleWarmScheduler,
    DashboardAccount,
    EquitySnapshotScheduler,
    TradeJournalSyncScheduler,
    _TtlCache,
)

LOGGER = logging.getLogger(__name__)


def _api_error_detail(label: str, exc: Exception, *, investor_portal: bool) -> str:
    if investor_portal:
        return f"{label} unavailable"
    return f"{label} failed: {exc}"


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

        from fastapi import FastAPI, HTTPException, Query
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
        from fastapi.staticfiles import StaticFiles
        from starlette.middleware.gzip import GZipMiddleware
    except ImportError as exc:  # pragma: no cover — surfaces a clear hint.
        raise RuntimeError("fastapi/uvicorn not installed; run `pip install -r requirements.txt`") from exc

    accounts = _make_dashboard_accounts(
        env_file=env_file,
        account_env_files=account_env_files,
    )
    env_paths = tuple(account.env_file for account in accounts)
    metrics_db_path = _configure_metrics_db(env_paths)
    repo_root = find_repo_root(env_paths[0])
    dashboard_investor_id = resolve_investor_scope(env_paths, repo_root=repo_root)
    dashboard_investor_display_name: str | None = None
    if dashboard_investor_id:
        if repo_root is not None:
            try:
                dashboard_investor_display_name = load_investor_manifest(
                    dashboard_investor_id, repo_root=repo_root
                ).display_name
            except ConfigurationError:
                dashboard_investor_display_name = dashboard_investor_id
        else:
            dashboard_investor_display_name = dashboard_investor_id
    config_public = accounts[0].config
    multi_account = len(accounts) > 1
    dashboard_strategies_list = _dashboard_strategies(
        investor_id=dashboard_investor_id,
        repo_root=repo_root,
        accounts=accounts,
    )
    interval = int(
        snapshot_interval_sec
        if snapshot_interval_sec is not None
        else os.environ.get("FRONTEND_SNAPSHOT_INTERVAL_SEC", DEFAULT_SNAPSHOT_INTERVAL_SEC)
    )
    journal_interval = int(
        os.environ.get(
            "FRONTEND_TRADE_JOURNAL_SYNC_INTERVAL_SEC",
            DEFAULT_TRADE_JOURNAL_SYNC_INTERVAL_SEC,
        )
    )
    state_path = accounts[0].state_path
    ledger_root = accounts[0].ledger_root if not multi_account else accounts[0].ledger_root.parent

    investor_status_ttl = int(
        os.environ.get(
            "FRONTEND_INVESTOR_STATUS_CACHE_TTL_SEC",
            DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC,
        )
    )
    status_ttl = investor_status_ttl if investor_portal else STATUS_CACHE_TTL_SEC
    # Serve stale live data instantly while refreshing in the background so a slow
    # Deribit prefetch never blocks the Snapshot→Live flip on the dashboard.
    status_cache = _TtlCache(status_ttl, stale_while_revalidate=True)
    fill_stats_cache = _TtlCache(DEFAULT_PREMIUM_SWEEP_FILL_STATS_CACHE_TTL_SEC)
    report_cache = _TtlCache(REPORT_CACHE_TTL_SEC)
    groups_cache = _TtlCache(GROUPS_CACHE_TTL_SEC)
    bundle_cache = _TtlCache(status_ttl, stale_while_revalidate=True)
    exchange_prefetch_cache = _TtlCache(status_ttl)
    spot_cache = _TtlCache(SPOT_CACHE_TTL_SEC)
    stress_cache = _TtlCache(STATUS_CACHE_TTL_SEC)
    transfers_cache = _TtlCache(TRANSFERS_CACHE_TTL_SEC, stale_while_revalidate=True)
    series_cache = _TtlCache(SERIES_CACHE_TTL_SEC)
    # Serialize heavy portfolio endpoints so parallel browser tabs / dashboard waves
    # do not stack duplicate Deribit JSON-RPC bursts (often surfaced as 502/timeouts).
    _heavy_portfolio_lock = threading.Lock()

    def _account_bot_factory(account: DashboardAccount) -> Callable[[], DeribitOptionTrialBot]:
        return lambda: _bot_for_account(account, require_private=True)

    equity_schedulers = [
        EquitySnapshotScheduler(
            account_name=account.name,
            bot_factory=_account_bot_factory(account),
            interval_sec=interval,
            ledger_root=account.ledger_root,
            config=account.config,
        )
        for account in accounts
    ]
    journal_scheduler = TradeJournalSyncScheduler(accounts=accounts, interval_sec=journal_interval)
    background_schedulers: list[Any] = [*equity_schedulers, journal_scheduler]

    portal_service: PortalSnapshotService | None = make_portal_snapshot_service(repo_root, dashboard_investor_id)

    def _build_ledger_snapshot_payload() -> dict[str, Any]:
        import deribit_engine.frontend_server as pkg

        payload = pkg._latest_ledger_snapshot(
            accounts,
            scheduler_states=[s.state for s in equity_schedulers],
            snapshot_interval_sec=interval,
        )
        if payload is None:
            payload = {"source": "none"}
        if isinstance(payload, dict) and payload.get("source") != "none":
            payload["dashboard_strategies"] = list(dashboard_strategies_list)
        return payload

    def _capture_portal_disk_snapshot(*, market_snapshot_id: int | None = None) -> None:
        if portal_service is None or not dashboard_investor_id:
            return

        ledger = _build_ledger_snapshot_payload()
        if ledger.get("source") == "none":
            return
        enriched = attach_realized_summary_to_ledger_snapshot(
            ledger,
            accounts=accounts,
            days=30,
            status_payload=status_cache.try_get("status"),
            series_cache=series_cache,
            spot_cache=spot_cache,
            fetch_spot=_fetch_spot,
            status_cache=status_cache,
        )
        market_id = market_snapshot_id
        if market_id is None:
            latest_market = portal_service.market_store.latest()
            market_id = latest_market.id if latest_market is not None else None
        portal_service.capture_disk(
            ledger_snapshot=ledger,
            groups=enriched.get("groups") or {},
            realized_summary=enriched.get("realized_summary") or {},
            dashboard_strategies=list(dashboard_strategies_list),
            market_snapshot_id=market_id,
        )

    def _capture_portal_live_snapshot(
        *,
        status: dict[str, Any],
        groups: dict[str, Any],
        realized_summary: dict[str, Any],
    ) -> None:
        if portal_service is None or not dashboard_investor_id:
            return
        ledger = _build_ledger_snapshot_payload()
        latest_market = portal_service.market_store.latest()
        portal_service.capture_live(
            ledger_snapshot=ledger if ledger.get("source") != "none" else None,
            status=status,
            groups=groups,
            realized_summary=realized_summary,
            dashboard_strategies=list(dashboard_strategies_list),
            market_snapshot_id=latest_market.id if latest_market is not None else None,
        )

    def _market_store():
        if repo_root is None:
            return None
        if portal_service is not None:
            return portal_service.market_store
        from ..env_layout import shared_market_db_path
        from ..market_snapshot_store import MarketSnapshotStore

        return MarketSnapshotStore(shared_market_db_path(repo_root))

    def _capture_market_snapshot() -> None:
        store = _market_store()
        if store is None:
            return
        spot_payload = spot_cache.get_or_set("spot", _fetch_spot)
        store.append_from_spot_payload(spot_payload)

    def _run_snapshot_retention() -> None:
        if portal_service is not None:
            deleted = portal_service.run_retention()
            LOGGER.debug("portal snapshot retention: %s", deleted)
            return
        store = _market_store()
        if store is None:
            return
        from .constants import DEFAULT_MARKET_SNAPSHOT_RETENTION_DAYS

        cutoff = (
            utc_now_ms()
            - int(os.environ.get("MARKET_SNAPSHOT_RETENTION_DAYS", DEFAULT_MARKET_SNAPSHOT_RETENTION_DAYS)) * 86400_000
        )
        deleted = store.purge_older_than(cutoff_ms=cutoff)
        LOGGER.debug("market snapshot retention deleted=%s", deleted)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        if enable_scheduler:
            for scheduler in background_schedulers:
                scheduler.start()
        try:
            yield
        finally:
            for scheduler in background_schedulers:
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

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def _fetch_spot() -> dict[str, Any]:
        import deribit_engine.frontend_server as pkg

        client = pkg.DeribitClient(config_public)
        btc_raw = client.get_index_price("btc_usd")
        eth_raw = client.get_index_price("eth_usd")
        btc_px = to_decimal(btc_raw.get("index_price") or 0)
        eth_px = to_decimal(eth_raw.get("index_price") or 0)
        vol = fetch_iv_rank_snapshot(
            client,
            lookback_days=max(10, int(config_public.iv_rank_lookback_days)),
        )
        change_24h = fetch_index_price_change_24h_pct(client)
        return {
            "BTC": str(btc_px) if btc_px > 0 else None,
            "ETH": str(eth_px) if eth_px > 0 else None,
            **vol,
            "price_change_pct_24h": change_24h,
        }

    @app.get("/api/spot")
    def api_spot() -> dict[str, Any]:
        """Public BTC/ETH USD index and IV rank for dashboard header (no private auth)."""
        try:
            store = _market_store()
            if store is not None:
                row = store.latest()
                if row is not None:
                    max_age_ms = (
                        int(os.environ.get("MARKET_SNAPSHOT_INTERVAL_SEC", DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC)) * 2000
                    )
                    if utc_now_ms() - row.ts_ms <= max_age_ms:
                        return row.to_spot_api_payload()
            return spot_cache.get_or_set("spot", _fetch_spot)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=_api_error_detail("spot", exc, investor_portal=investor_portal),
            ) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        any_have_creds = any(_has_private_creds(account.config) for account in accounts)
        covered_call_sweep_enabled = any(
            account.config.covered_call_profit_sweep_enabled and account.config.option_strategy == "covered_call"
            for account in accounts
        )
        any_scheduler_running = any(scheduler.state.running for scheduler in background_schedulers)
        last_attempts = [s.state.last_attempt_ms for s in equity_schedulers if s.state.last_attempt_ms is not None]
        last_successes = [s.state.last_success_ms for s in equity_schedulers if s.state.last_success_ms is not None]
        last_errors = [
            f"{account.name}: {scheduler.state.last_error}"
            for account, scheduler in zip(accounts, equity_schedulers, strict=False)
            if scheduler.state.last_error
        ]
        return {
            "env": "multi" if multi_account else config_public.env,
            "has_private_creds": any_have_creds,
            "skipped_accounts": list(skipped_accounts or ()),
            "scheduler_running": any_scheduler_running,
            "snapshot_interval_sec": interval,
            "last_snapshot_attempt_ms": max(last_attempts, default=None),
            "last_snapshot_success_ms": max(last_successes, default=None),
            "last_snapshot_error": "; ".join(last_errors) if last_errors else None,
            "trade_journal_sync_running": journal_scheduler.state.running,
            "trade_journal_sync_interval_sec": journal_interval,
            "last_trade_journal_sync_attempt_ms": journal_scheduler.state.last_attempt_ms,
            "last_trade_journal_sync_success_ms": journal_scheduler.state.last_success_ms,
            "last_trade_journal_sync_error": journal_scheduler.state.last_error,
            "last_trade_journal_sync_inserted": journal_scheduler.state.last_inserted,
            "state_file": str(state_path) if not multi_account else "multi",
            "ledger_dir": str(ledger_root),
            "investor_id": dashboard_investor_id,
            "investor_display_name": dashboard_investor_display_name,
            "metrics_db": str(metrics_db_path),
            "managed_currencies": list(config_public.managed_currencies),
            "traded_collaterals": list(config_public.traded_collaterals),
            "option_strategy": "multi_account" if multi_account else config_public.option_strategy,
            "reference_capital_usdc": str(
                sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
            ),
            "target_portfolio_apr": str(
                _ratio(
                    sum(
                        (
                            account.config.target_portfolio_apr * account.config.reference_capital_usdc
                            for account in accounts
                        ),
                        Decimal("0"),
                    ),
                    sum((account.config.reference_capital_usdc for account in accounts), Decimal("0")),
                )
            ),
            "halt_open_max_loss_pct": str(config_public.halt_open_max_loss_pct),
            "covered_call_profit_sweep_enabled": covered_call_sweep_enabled,
            "multi_account": multi_account,
            "dashboard_strategies": dashboard_strategies_list,
            "accounts": [
                {
                    "name": account.name,
                    "env": account.config.env,
                    "option_strategy": account.config.option_strategy,
                    "risk_tier": account.config.risk_tier,
                    "covered_call_profit_sweep_enabled": (
                        account.config.covered_call_profit_sweep_enabled
                        if account.config.option_strategy == "covered_call"
                        else False
                    ),
                    "state_file": str(account.state_path),
                    "ledger_dir": str(account.ledger_root),
                    "has_private_creds": _has_private_creds(account.config),
                }
                for account in accounts
            ],
            "server_time_ms": utc_now_ms(),
        }

    @app.get("/api/portfolio/snapshot")
    def api_portfolio_snapshot(
        days: int = Query(default=30, ge=0, le=3650),
    ) -> Any:
        """Last on-disk equity snapshot (no Deribit); for fast investor first paint."""
        if portal_service is not None and investor_portal:
            cached = portal_service.load_for_api(
                prefer_live=True,
                live_max_age_ms=investor_status_ttl * 1000,
            )
            if cached is not None:
                return JSONResponse(_decimalize(_enrich_snapshot_payload(cached)))

        payload = _build_ledger_snapshot_payload()
        if payload.get("source") == "none":
            return JSONResponse(_decimalize(payload), status_code=200)
        payload = attach_realized_summary_to_ledger_snapshot(
            payload,
            accounts=accounts,
            days=days,
            status_payload=status_cache.try_get("status"),
            series_cache=series_cache,
            spot_cache=spot_cache,
            fetch_spot=_fetch_spot,
            status_cache=status_cache,
        )
        return JSONResponse(_decimalize(_enrich_snapshot_payload(payload)))

    def _locked_aggregate_status() -> dict[str, Any]:
        import deribit_engine.frontend_server as pkg

        with _heavy_portfolio_lock:
            status = pkg._aggregate_status(accounts, exchange_prefetch_cache=exchange_prefetch_cache)
        return attach_cached_premium_sweep_fill_stats(status, fill_stats_cache)

    def _fill_stats_for_snapshot() -> dict[str, Any]:
        cached = fill_stats_cache.get_stale(FILL_STATS_CACHE_KEY)
        if cached:
            return cached
        cached_status = status_cache.try_get("status")
        fill_stats = (cached_status or {}).get("premium_sweep_fill_stats_by_book")
        if fill_stats:
            return fill_stats
        if not any(_has_private_creds(account.config) for account in accounts):
            return {}
        try:
            live_status = _locked_aggregate_status()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("snapshot fill stats status fetch skipped: %s", exc)
            return fill_stats_cache.get_stale(FILL_STATS_CACHE_KEY) or {}
        status_cache.seed("status", live_status)
        return live_status.get("premium_sweep_fill_stats_by_book") or {}

    def _enrich_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
        fill_stats = _fill_stats_for_snapshot()
        cached_status = status_cache.try_get("status") or {}
        hedge_summary = cached_status.get("hedge_pnl_summary")
        if not fill_stats and not hedge_summary:
            return payload
        out = dict(payload)
        live_status = dict(out.get("live_status") or {})
        if fill_stats:
            live_status["premium_sweep_fill_stats_by_book"] = fill_stats
        if hedge_summary:
            live_status["hedge_pnl_summary"] = hedge_summary
        out["live_status"] = live_status
        return out

    def _locked_aggregate_report(d: int) -> dict[str, Any]:
        import deribit_engine.frontend_server as pkg

        with _heavy_portfolio_lock:
            return pkg._aggregate_report(accounts, days=d)

    def _locked_compute_dashboard_bundle(
        *,
        days: int,
        override: Decimal | None,
        sections: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        import deribit_engine.frontend_server as pkg

        selected = sections or frozenset({"status", "groups", "realized_summary"})
        need_status = "status" in selected
        need_groups = "groups" in selected
        need_summary = "realized_summary" in selected
        payload: dict[str, Any] = {}

        with _heavy_portfolio_lock:
            status: dict[str, Any] | None = None
            if need_status:
                status = attach_cached_premium_sweep_fill_stats(
                    pkg._aggregate_status(accounts, exchange_prefetch_cache=exchange_prefetch_cache),
                    fill_stats_cache,
                )
                payload["status"] = status
            if need_groups:
                payload["groups"] = pkg._aggregate_groups(accounts, exchange_prefetch_cache=exchange_prefetch_cache)
            if need_summary:
                status_for_summary = status if status is not None else status_cache.try_get("status")
                spot_idx: dict[str, Decimal] = {}
                try:
                    spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("dashboard bundle spot fetch skipped: %s", exc)
                payload["realized_summary"] = pkg._aggregate_realized_summary(
                    accounts,
                    days=days,
                    spot_index=spot_idx or None,
                    status_payload=status_for_summary,
                    effective_capital_override=override,
                )
        return payload

    def _seed_bundle_component_caches(
        *,
        status: dict[str, Any] | None,
        groups: dict[str, Any] | None,
        summary: dict[str, Any] | None,
        days: int,
        override: Decimal | None,
    ) -> None:
        if status is not None:
            status_cache.seed("status", status)
        if groups is not None:
            groups_cache.seed(("groups", _closed_groups_cache_key(accounts)), groups)
        if summary is not None:
            capital = _resolve_apr_effective_capital_usdc(
                accounts,
                override=override,
                status_payload=status or {},
            )
            series_cache.seed(
                (
                    "realized_summary",
                    days,
                    str(capital),
                    _ledger_equity_cache_key(accounts),
                    _closed_groups_cache_key(accounts),
                ),
                summary,
            )

    def _warm_dashboard_bundle() -> None:
        """Background warm: refresh the Deribit prefetch and reseed live caches.

        Keeps ``/api/dashboard_bundle`` (and the status/groups/prefetch caches it
        depends on) hot so the dashboard flips Snapshot→Live without paying a cold
        Deribit prefetch. Seeds the ``override=None`` keys the browser uses on first
        paint; the warmed prefetch also makes any other cache key cheap to compute.
        """
        import deribit_engine.frontend_server as pkg

        if not any(_has_private_creds(account.config) for account in accounts):
            return
        try:
            pkg._force_refresh_prefetch_all(accounts, cache=exchange_prefetch_cache)
        except Exception as exc:  # noqa: BLE001 — fall back to cached prefetch below.
            LOGGER.warning("bundle warm prefetch refresh failed: %s", exc)
        days = 30
        override: Decimal | None = None
        ledger_key = _ledger_equity_cache_key(accounts)
        closed_key = _closed_groups_cache_key(accounts)
        for sections in (
            frozenset({"status", "groups"}),
            frozenset({"status", "groups", "realized_summary"}),
        ):
            cache_key = (
                "dashboard_bundle",
                days,
                "",
                ledger_key,
                closed_key,
                ",".join(sorted(sections)),
            )
            payload = _locked_compute_dashboard_bundle(days=days, override=override, sections=sections)
            _seed_bundle_component_caches(
                status=payload.get("status"),
                groups=payload.get("groups"),
                summary=payload.get("realized_summary"),
                days=days,
                override=override,
            )
            bundle_cache.seed(cache_key, payload)
            if (
                portal_service is not None
                and investor_portal
                and "realized_summary" in sections
                and payload.get("status")
                and payload.get("groups")
            ):
                try:
                    _capture_portal_live_snapshot(
                        status=payload["status"],
                        groups=payload["groups"],
                        realized_summary=payload.get("realized_summary") or {},
                    )
                except Exception as live_exc:  # noqa: BLE001
                    LOGGER.debug("portal live snapshot skipped: %s", live_exc)

    def _finalize_dashboard_bundle(payload: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(payload)
        try:
            from ..realized_summary import patch_realized_report_spot_pnl

            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
            _apply_spot_native_backfill(out.get("groups") or {}, spot_idx)
            groups = out.get("groups") or {}
            closed_rows = [row for row in (groups.get("closed") or []) if isinstance(row, dict)]
            report_payload = out.get("realized_summary")
            if report_payload and closed_rows and spot_idx:
                summary = report_payload.get("summary") or {}
                window_days = int(to_decimal(summary.get("window_days_requested") or 30))
                from ..hedge_pnl import hedge_performance_adjustments

                hedge_lifetime, hedge_window = hedge_performance_adjustments(
                    [account.state_path for account in accounts],
                    window_days=window_days,
                )
                patch_realized_report_spot_pnl(
                    report_payload,
                    closed_rows,
                    spot_index=spot_idx,
                    window_days=window_days,
                    hedge_lifetime_usdc=hedge_lifetime,
                    hedge_window_usdc=hedge_window,
                    fill_stats=(out.get("status") or {}).get("premium_sweep_fill_stats_by_book"),
                )
            for row in (report_payload or {}).get("recent_closed_trades") or []:
                if isinstance(row, dict):
                    _backfill_row_collateral_native(row, spot_idx)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("dashboard bundle spot native backfill skipped: %s", exc)
        return out

    def _locked_aggregate_stress(shock_decimals: list[Decimal]) -> dict[str, Any]:
        import deribit_engine.frontend_server as pkg

        with _heavy_portfolio_lock:
            return pkg._aggregate_stress(
                accounts,
                shocks=shock_decimals,
                exchange_prefetch_cache=exchange_prefetch_cache,
            )

    def _locked_aggregate_transfers(
        *,
        days: int,
        limit: int,
        index_by_ccy: dict[str, Decimal],
    ) -> dict[str, Any]:
        from .transfers_service import aggregate_transfers_for_accounts

        with _heavy_portfolio_lock:
            return aggregate_transfers_for_accounts(
                accounts,
                days=days,
                index_by_ccy=index_by_ccy,
                limit_per_account=limit,
            )

    from .routes.bundle import register_bundle_routes
    from .routes.context import RouteContext
    from .routes.groups import register_groups_routes
    from .routes.stress import register_stress_routes
    from .routes.transfers import register_transfers_routes

    route_ctx = RouteContext(
        accounts=accounts,
        multi_account=multi_account,
        status_cache=status_cache,
        report_cache=report_cache,
        groups_cache=groups_cache,
        bundle_cache=bundle_cache,
        exchange_prefetch_cache=exchange_prefetch_cache,
        spot_cache=spot_cache,
        stress_cache=stress_cache,
        transfers_cache=transfers_cache,
        series_cache=series_cache,
        heavy_portfolio_lock=_heavy_portfolio_lock,
        fetch_spot=_fetch_spot,
        locked_aggregate_status=_locked_aggregate_status,
        locked_aggregate_report=_locked_aggregate_report,
        locked_compute_dashboard_bundle=_locked_compute_dashboard_bundle,
        locked_aggregate_stress=_locked_aggregate_stress,
        locked_aggregate_transfers=_locked_aggregate_transfers,
        seed_bundle_component_caches=_seed_bundle_component_caches,
        finalize_dashboard_bundle=_finalize_dashboard_bundle,
    )
    register_bundle_routes(app, route_ctx)
    register_groups_routes(app, route_ctx)
    register_stress_routes(app, route_ctx)
    register_transfers_routes(app, route_ctx)

    # Keep the investor live caches warm so Snapshot→Live is near-instant. Only the
    # investor portal uses a long status TTL, so warming the ops dashboard (15s TTL)
    # would add load without helping; gate it accordingly.
    if enable_scheduler and repo_root is not None:
        background_schedulers.append(
            MarketSnapshotScheduler(
                capture_fn=_capture_market_snapshot,
                interval_sec=int(os.environ.get("MARKET_SNAPSHOT_INTERVAL_SEC", DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC)),
                retention_fn=_run_snapshot_retention,
            )
        )
    if enable_scheduler and investor_portal and portal_service is not None:
        background_schedulers.append(
            PortalDiskSnapshotScheduler(
                capture_fn=lambda: _capture_portal_disk_snapshot(),
                interval_sec=int(
                    os.environ.get(
                        "PORTAL_SNAPSHOT_DISK_INTERVAL_SEC",
                        DEFAULT_PORTAL_SNAPSHOT_DISK_INTERVAL_SEC,
                    )
                ),
                retention_fn=_run_snapshot_retention,
            )
        )

    if investor_portal:
        bundle_warm_interval = int(
            os.environ.get("FRONTEND_BUNDLE_WARM_INTERVAL_SEC", DEFAULT_BUNDLE_WARM_INTERVAL_SEC)
        )
        background_schedulers.append(
            BundleWarmScheduler(
                warm_fn=_warm_dashboard_bundle,
                interval_sec=bundle_warm_interval,
                has_private_creds=lambda: any(_has_private_creds(account.config) for account in accounts),
            )
        )

    @app.get("/api/equity_series")
    def api_equity_series(days: int = Query(default=30, ge=1, le=3650)) -> Any:
        since_ms = utc_now_ms() - days * 86400 * 1000
        rows = []
        for account in accounts:
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
        return JSONResponse(journal_scheduler.run_once())

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
        status_payload = status_cache.try_get("status")
        capital = _resolve_apr_effective_capital_usdc(
            accounts,
            override=override,
            status_payload=status_payload,
        )
        cache_key = (
            "realized_summary",
            days,
            str(capital),
            _ledger_equity_cache_key(accounts),
            _closed_groups_cache_key(accounts),
        )

        def _compute() -> dict[str, Any]:
            import deribit_engine.frontend_server as pkg

            return pkg._aggregate_realized_summary(
                accounts,
                days=days,
                status_payload=status_payload,
                effective_capital_override=override,
            )

        try:
            payload = copy.deepcopy(series_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=_api_error_detail("realized summary", exc, investor_portal=investor_portal),
            ) from exc
        try:
            import deribit_engine.frontend_server as pkg

            from ..realized_summary import patch_realized_report_spot_pnl

            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
            closed_rows = pkg._all_closed_group_rows(accounts, spot_index=spot_idx)
            from ..hedge_pnl import hedge_performance_adjustments

            hedge_lifetime, hedge_window = hedge_performance_adjustments(
                [account.state_path for account in accounts],
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
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("realized summary spot native backfill skipped: %s", exc)
        return JSONResponse(_decimalize(payload))

    @app.get("/api/trade_executions")
    def api_trade_executions(
        limit: int = Query(default=200, ge=1, le=2000),
        since_days: int = Query(default=90, ge=1, le=3650),
        group_id: str | None = Query(default=None),
    ) -> Any:
        since_ms = utc_now_ms() - since_days * 86400 * 1000
        rows: list[dict[str, Any]] = []
        per_account = max(1, limit // max(len(accounts), 1))
        for account in accounts:
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
            _closed_groups_cache_key(accounts),
            _spot_series_cache_key(spot_cache.try_get("spot")),
        )

        def _compute() -> dict[str, Any]:
            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
            return _cumulative_spot_pnl_series_from_accounts(accounts, spot_index=spot_idx)

        try:
            series = series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=_api_error_detail("cumulative spot pnl", exc, investor_portal=investor_portal),
            ) from exc
        return JSONResponse(series)

    @app.get("/api/cumulative_pnl_series")
    def api_cumulative_pnl_series() -> Any:
        cache_key = (
            "cumulative_pnl_stable",
            _closed_groups_cache_key(accounts),
            _spot_series_cache_key(spot_cache.try_get("spot")),
        )

        def _compute() -> dict[str, Any]:
            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
            return _cumulative_stable_pnl_series(accounts, spot_index=spot_idx)

        try:
            series = series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=_api_error_detail("cumulative pnl", exc, investor_portal=investor_portal),
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
            accounts,
            override=override,
            status_payload=status_cache.try_get("status"),
        )
        cache_key = (
            "apr_series",
            window_days,
            str(capital),
            _ledger_equity_cache_key(accounts),
            _closed_groups_cache_key(accounts),
        )

        def _compute() -> dict[str, Any]:
            rows = _rolling_apr_series_from_store(
                accounts,
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
            payload = series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=_api_error_detail("apr series", exc, investor_portal=investor_portal),
            ) from exc
        return JSONResponse(payload)

    # ------------------------------------------------------------------
    # Static frontend
    # ------------------------------------------------------------------

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"

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

        for _html_name in ("index.html", "investor.html", "investor.zh.html"):
            _html_path = frontend_dir / _html_name

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
                f"/{_html_name}",
                _make_html_handler(
                    _html_path,
                    inject_strategies=_html_name.startswith("investor") or investor_portal,
                ),
                methods=["GET"],
                include_in_schema=False,
            )

        app.mount(
            "/",
            StaticFiles(directory=str(frontend_dir), html=True),
            name="frontend",
        )
    else:  # pragma: no cover — should always exist in repo.
        LOGGER.warning("frontend dir not found at %s; static UI disabled", frontend_dir)

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
