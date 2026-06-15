"""Build and load investor portal snapshot payloads (disk + live)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from .env_layout import investor_portal_db_path, shared_market_db_path
from .frontend_server.constants import (
    DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC,
    DEFAULT_MARKET_SNAPSHOT_RETENTION_DAYS,
    DEFAULT_PORTAL_SNAPSHOT_DISK_RETENTION_DAYS,
    DEFAULT_PORTAL_SNAPSHOT_LIVE_RETENTION_DAYS,
)
from .market_snapshot_store import MarketSnapshotStore
from .portal_snapshot_store import PortalSnapshotStore
from .utils import utc_now_ms

LOGGER = logging.getLogger(__name__)


def _fingerprint(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _summary_numbers(summary: dict[str, Any] | None) -> dict[str, str | None]:
    s = summary or {}
    return {
        "realized_pnl_usdc": str(s.get("realized_pnl_usdc")) if s.get("realized_pnl_usdc") is not None else None,
        "window_realized_pnl_usdc": (
            str(s.get("window_realized_pnl_usdc")) if s.get("window_realized_pnl_usdc") is not None else None
        ),
        "total_equity_usdc": None,
    }


def build_portal_payload(
    *,
    ledger_snapshot: dict[str, Any],
    groups: dict[str, Any],
    realized_summary: dict[str, Any],
    dashboard_strategies: list[str],
) -> dict[str, Any]:
    """Merge components into the JSON stored in portal_snapshots.db."""
    portfolio = ledger_snapshot.get("portfolio") or {}
    summary = (realized_summary or {}).get("summary") or {}
    fp_basis = {
        "portfolio": portfolio,
        "groups_closed": len(groups.get("closed") or []),
        "groups_open": len(groups.get("open") or []),
        "summary": _summary_numbers(summary),
    }
    return {
        "snapshot_ts_ms": ledger_snapshot.get("snapshot_ts_ms"),
        "freshness_ms": ledger_snapshot.get("freshness_ms"),
        "portfolio": portfolio,
        "accounts": ledger_snapshot.get("accounts") or [],
        "scheduler": ledger_snapshot.get("scheduler") or {},
        "groups": groups,
        "realized_summary": realized_summary,
        "dashboard_strategies": list(dashboard_strategies),
        "_fingerprint_basis": fp_basis,
    }


def portal_row_to_api_response(
    row_payload: dict[str, Any],
    *,
    snapshot_kind: str,
    ts_ms: int,
    market_snapshot_id: int | None,
) -> dict[str, Any]:
    now_ms = utc_now_ms()
    out = {key: value for key, value in row_payload.items() if not key.startswith("_")}
    out["source"] = "portal_cache"
    out["cache_kind"] = snapshot_kind
    out["snapshot_ts_ms"] = ts_ms
    out["freshness_ms"] = max(0, now_ms - ts_ms)
    if market_snapshot_id is not None:
        out["market_snapshot_id"] = market_snapshot_id
    live_status = row_payload.get("live_status")
    if isinstance(live_status, dict):
        out["live_status"] = live_status
    return out


class PortalSnapshotService:
    def __init__(
        self,
        *,
        repo_root: Path,
        investor_id: str,
        market_store: MarketSnapshotStore | None = None,
        portal_store: PortalSnapshotStore | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._investor_id = investor_id
        self._market = market_store or MarketSnapshotStore(shared_market_db_path(repo_root))
        self._portal = portal_store or PortalSnapshotStore(investor_portal_db_path(repo_root, investor_id))
        self._last_live_capture_ms = 0

    @property
    def market_store(self) -> MarketSnapshotStore:
        return self._market

    @property
    def portal_store(self) -> PortalSnapshotStore:
        return self._portal

    def capture_market(self, spot_payload: dict[str, Any]) -> int:
        return self._market.append_from_spot_payload(spot_payload)

    def capture_disk(
        self,
        *,
        ledger_snapshot: dict[str, Any],
        groups: dict[str, Any],
        realized_summary: dict[str, Any],
        dashboard_strategies: list[str],
        market_snapshot_id: int | None = None,
    ) -> bool:
        if ledger_snapshot.get("source") == "none":
            return False
        payload = build_portal_payload(
            ledger_snapshot=ledger_snapshot,
            groups=groups,
            realized_summary=realized_summary,
            dashboard_strategies=dashboard_strategies,
        )
        fp = _fingerprint(payload["_fingerprint_basis"])
        row_id = self._portal.append(
            investor_id=self._investor_id,
            snapshot_kind="disk",
            payload=payload,
            content_fingerprint=fp,
            market_snapshot_id=market_snapshot_id,
        )
        return row_id is not None

    def capture_live(
        self,
        *,
        ledger_snapshot: dict[str, Any] | None,
        status: dict[str, Any],
        groups: dict[str, Any],
        realized_summary: dict[str, Any],
        dashboard_strategies: list[str],
        market_snapshot_id: int | None = None,
        min_interval_sec: int | None = None,
    ) -> bool:
        interval_ms = (
            int(
                min_interval_sec
                if min_interval_sec is not None
                else os.environ.get("PORTAL_SNAPSHOT_LIVE_INTERVAL_SEC", "600")
            )
            * 1000
        )
        now_ms = utc_now_ms()
        if self._last_live_capture_ms and (now_ms - self._last_live_capture_ms) < interval_ms:
            return False

        portfolio = status.get("portfolio") or {}
        if not portfolio:
            return False

        base = ledger_snapshot or {}
        merged = {
            "source": "ledger",
            "snapshot_ts_ms": base.get("snapshot_ts_ms") or now_ms,
            "freshness_ms": 0,
            "portfolio": portfolio,
            "accounts": base.get("accounts") or [],
            "scheduler": base.get("scheduler") or {},
        }
        payload = build_portal_payload(
            ledger_snapshot=merged,
            groups=groups,
            realized_summary=realized_summary,
            dashboard_strategies=dashboard_strategies,
        )
        payload["live_status"] = {
            "underlying_index_usd": status.get("underlying_index_usd") or {},
            "premium_sweep_fill_stats_by_book": status.get("premium_sweep_fill_stats_by_book") or {},
        }
        fp = _fingerprint(
            {
                **payload["_fingerprint_basis"],
                "live": status.get("underlying_index_usd"),
                "fill_stats": status.get("premium_sweep_fill_stats_by_book"),
            }
        )
        row_id = self._portal.append(
            investor_id=self._investor_id,
            snapshot_kind="live",
            payload=payload,
            content_fingerprint=fp,
            market_snapshot_id=market_snapshot_id,
            ts_ms=now_ms,
        )
        if row_id is not None:
            self._last_live_capture_ms = now_ms
        return row_id is not None

    def load_for_api(
        self,
        *,
        prefer_live: bool = True,
        live_max_age_ms: int | None = None,
    ) -> dict[str, Any] | None:
        max_age = live_max_age_ms
        if max_age is None:
            max_age = (
                int(
                    os.environ.get(
                        "FRONTEND_INVESTOR_STATUS_CACHE_TTL_SEC",
                        DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC,
                    )
                )
                * 1000
            )

        live_row = self._portal.latest(self._investor_id, snapshot_kind="live") if prefer_live else None
        if live_row is not None:
            age = utc_now_ms() - live_row.ts_ms
            if age <= max_age:
                return portal_row_to_api_response(
                    live_row.payload,
                    snapshot_kind=live_row.snapshot_kind,
                    ts_ms=live_row.ts_ms,
                    market_snapshot_id=live_row.market_snapshot_id,
                )

        disk_row = self._portal.latest(self._investor_id, snapshot_kind="disk")
        if disk_row is not None:
            return portal_row_to_api_response(
                disk_row.payload,
                snapshot_kind=disk_row.snapshot_kind,
                ts_ms=disk_row.ts_ms,
                market_snapshot_id=disk_row.market_snapshot_id,
            )
        return None

    def run_retention(self) -> dict[str, int]:
        now_ms = utc_now_ms()
        market_days = int(os.environ.get("MARKET_SNAPSHOT_RETENTION_DAYS", DEFAULT_MARKET_SNAPSHOT_RETENTION_DAYS))
        disk_days = int(
            os.environ.get("PORTAL_SNAPSHOT_DISK_RETENTION_DAYS", DEFAULT_PORTAL_SNAPSHOT_DISK_RETENTION_DAYS)
        )
        live_days = int(
            os.environ.get("PORTAL_SNAPSHOT_LIVE_RETENTION_DAYS", DEFAULT_PORTAL_SNAPSHOT_LIVE_RETENTION_DAYS)
        )
        deleted_market = self._market.purge_older_than(cutoff_ms=now_ms - market_days * 86400 * 1000)
        deleted_disk = self._portal.purge_older_than(
            investor_id=self._investor_id,
            snapshot_kind="disk",
            cutoff_ms=now_ms - disk_days * 86400 * 1000,
        )
        deleted_live = self._portal.purge_older_than(
            investor_id=self._investor_id,
            snapshot_kind="live",
            cutoff_ms=now_ms - live_days * 86400 * 1000,
        )
        return {
            "market": deleted_market,
            "disk": deleted_disk,
            "live": deleted_live,
        }


def attach_realized_summary_to_ledger_snapshot(
    payload: dict[str, Any],
    *,
    accounts: list[Any],
    days: int,
    status_payload: dict[str, Any] | None,
    series_cache: Any,
    spot_cache: Any,
    fetch_spot: Any,
    status_cache: Any,
) -> dict[str, Any]:
    """Existing snapshot enrichment: summary + disk groups (used when cache miss)."""
    import copy

    import deribit_engine.frontend_server as pkg
    from deribit_engine.frontend_server.aggregation import _resolve_apr_effective_capital_usdc
    from deribit_engine.frontend_server.groups_service import _closed_groups_cache_key
    from deribit_engine.frontend_server.helpers import (
        _ledger_equity_cache_key,
        _spot_index_decimals,
    )

    out = copy.deepcopy(payload)
    if out.get("source") == "none":
        return out

    try:
        status_for_summary = status_payload if status_payload is not None else status_cache.try_get("status")
        override = None
        capital = _resolve_apr_effective_capital_usdc(
            accounts,
            override=override,
            status_payload=status_for_summary or {},
        )
        cache_key = (
            "realized_summary",
            days,
            str(capital),
            _ledger_equity_cache_key(accounts),
            _closed_groups_cache_key(accounts),
        )
        summary = series_cache.try_get(cache_key)
        if summary is None:
            summary = pkg._aggregate_realized_summary(
                accounts,
                days=days,
                status_payload=status_for_summary,
                effective_capital_override=override,
            )
            series_cache.seed(cache_key, summary)
        report_payload = copy.deepcopy(summary)
        try:
            from .realized_summary import patch_realized_report_spot_pnl

            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", fetch_spot))
            closed_rows = pkg._all_closed_group_rows(accounts, spot_index=spot_idx)
            patch_realized_report_spot_pnl(
                report_payload,
                closed_rows,
                spot_index=spot_idx,
                window_days=days,
            )
        except Exception as spot_exc:  # noqa: BLE001
            LOGGER.debug("snapshot realized_summary spot patch skipped: %s", spot_exc)
        out["realized_summary"] = report_payload
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("snapshot realized_summary attach skipped: %s", exc)

    try:
        spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", fetch_spot))
        disk_groups = pkg._aggregate_groups_disk_only(accounts, spot_index=spot_idx or None)
        pkg._apply_spot_native_backfill(disk_groups, spot_idx)
        out["groups"] = disk_groups
    except Exception as groups_exc:  # noqa: BLE001
        LOGGER.debug("snapshot disk groups attach skipped: %s", groups_exc)

    return out
