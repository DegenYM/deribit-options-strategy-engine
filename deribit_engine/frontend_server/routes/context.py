from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..types import DashboardAccount, _TtlCache


@dataclass
class RouteContext:
    accounts: list[DashboardAccount]
    multi_account: bool
    status_cache: _TtlCache
    report_cache: _TtlCache
    groups_cache: _TtlCache
    bundle_cache: _TtlCache
    exchange_prefetch_cache: _TtlCache
    spot_cache: _TtlCache
    stress_cache: _TtlCache
    transfers_cache: _TtlCache
    series_cache: _TtlCache
    heavy_portfolio_lock: Any
    fetch_spot: Callable[[], dict[str, Any]]
    locked_aggregate_status: Callable[[], dict[str, Any]]
    locked_aggregate_report: Callable[[int], dict[str, Any]]
    locked_compute_dashboard_bundle: Callable[..., dict[str, Any]]
    locked_aggregate_stress: Callable[[list[Decimal]], dict[str, Any]]
    locked_aggregate_transfers: Callable[..., dict[str, Any]]
    seed_bundle_component_caches: Callable[..., None]
    finalize_dashboard_bundle: Callable[[dict[str, Any]], dict[str, Any]]
