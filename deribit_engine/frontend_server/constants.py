from __future__ import annotations

from decimal import Decimal
from pathlib import Path

LEDGER_DIR = Path("data/frontend_ledger")
LEGACY_METRICS_DB_PATH = LEDGER_DIR / "metrics.db"
_active_metrics_db_path: Path | None = None
DEFAULT_SNAPSHOT_INTERVAL_SEC = 300
DEFAULT_TRADE_JOURNAL_SYNC_INTERVAL_SEC = 300
STATUS_CACHE_TTL_SEC = 15
# Investor portal: keep the live status/bundle cache TTL at least as long as the
# frontend auto-refresh interval (180s) so scheduled refreshes hit a warm cache
# instead of paying a full Deribit prefetch every time.
DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC = 180
# Exchange premium-sweep VWAP stats: survive Deribit rate limits between fast status polls.
DEFAULT_PREMIUM_SWEEP_FILL_STATS_CACHE_TTL_SEC = 86400
# Background warm cadence for the live dashboard bundle / exchange prefetch. Must
# be shorter than the cache TTL so the cache never goes cold for a user request.
DEFAULT_BUNDLE_WARM_INTERVAL_SEC = 90
DEFAULT_MARKET_SNAPSHOT_INTERVAL_SEC = 300
DEFAULT_PORTAL_SNAPSHOT_DISK_INTERVAL_SEC = 300
DEFAULT_PORTAL_SNAPSHOT_LIVE_INTERVAL_SEC = 600
DEFAULT_MARKET_SNAPSHOT_RETENTION_DAYS = 7
DEFAULT_PORTAL_SNAPSHOT_DISK_RETENTION_DAYS = 90
DEFAULT_PORTAL_SNAPSHOT_LIVE_RETENTION_DAYS = 30
REPORT_CACHE_TTL_SEC = 15
TRANSFERS_CACHE_TTL_SEC = 120
GROUPS_CACHE_TTL_SEC = 30
SPOT_CACHE_TTL_SEC = 10
SERIES_CACHE_TTL_SEC = 30
ROLLING_APR_MAX_CHART_DAYS = 730
STRATEGY_DISPLAY_ORDER = ("covered_call", "naked_short", "bull_put_spread")
_INSTRUMENT_CONTRACT_SIZE_CACHE: dict[str, Decimal] = {}
