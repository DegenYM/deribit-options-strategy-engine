from __future__ import annotations

from decimal import Decimal
from pathlib import Path

LEDGER_DIR = Path("data/frontend_ledger")
LEGACY_METRICS_DB_PATH = LEDGER_DIR / "metrics.db"
_active_metrics_db_path: Path | None = None
DEFAULT_SNAPSHOT_INTERVAL_SEC = 300
DEFAULT_TRADE_JOURNAL_SYNC_INTERVAL_SEC = 300
STATUS_CACHE_TTL_SEC = 15
DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC = 120
REPORT_CACHE_TTL_SEC = 15
GROUPS_CACHE_TTL_SEC = 30
SPOT_CACHE_TTL_SEC = 10
SERIES_CACHE_TTL_SEC = 30
ROLLING_APR_MAX_CHART_DAYS = 730
STRATEGY_DISPLAY_ORDER = ("covered_call", "naked_short", "bull_put_spread")
_INSTRUMENT_CONTRACT_SIZE_CACHE: dict[str, Decimal] = {}
