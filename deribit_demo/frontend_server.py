"""Local dashboard backend.

Exposes a small FastAPI app that re-uses :class:`DeribitOptionTrialBot` and
``compute_current_stress`` to surface the same data as the CLI but as JSON
endpoints, plus a background scheduler that periodically appends an
equity snapshot to ``data/frontend_ledger/<investor_id>/`` (or legacy flat dir).

The frontend (``frontend/index.html``) consumes those endpoints; static
assets are mounted at ``/`` so the dashboard is reachable from a single URL.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .client import DeribitClient
from .config import BotConfig, has_private_creds_config, load_config
from .env_layout import (
    account_slug_from_env_path,
    find_repo_root,
    investor_frontend_ledger_dir,
    investor_metrics_db_path,
    load_investor_manifest,
    resolve_investor_scope,
)
from .current_stress import compute_current_stress
from .engine import DeribitOptionTrialBot, ExchangePrefetch
from .exceptions import ConfigurationError
from .models import OrderBookSnapshot, TradeGroup, normalize_strategy_name
from .stress import black_swan_strategy_analysis
from .metrics_store import MetricsStore, fingerprint_from_cache_key, performance_scope_key
from .realized_summary import realized_summary_from_closed
from .trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state
from .trade_journal_backfill import sync_incremental_journal
from .models import is_phantom_reconcile_close, open_short_instrument_names
from .state import StrategyStateStore, load_performance_exclusion_group_ids
from .trade_apr import entry_dte_days_at_open, realized_apr_from_close
from .utils import format_decimal, json_default, parse_option_name, to_decimal, utc_now, utc_now_ms

LOGGER = logging.getLogger(__name__)

LEDGER_DIR = Path("data/frontend_ledger")
LEGACY_METRICS_DB_PATH = LEDGER_DIR / "metrics.db"
_active_metrics_db_path: Path | None = None
DEFAULT_SNAPSHOT_INTERVAL_SEC = 300
DEFAULT_TRADE_JOURNAL_SYNC_INTERVAL_SEC = 300
STATUS_CACHE_TTL_SEC = 15
DEFAULT_INVESTOR_STATUS_CACHE_TTL_SEC = 60
REPORT_CACHE_TTL_SEC = 15
GROUPS_CACHE_TTL_SEC = 15
SPOT_CACHE_TTL_SEC = 10
SERIES_CACHE_TTL_SEC = 30
ROLLING_APR_MAX_CHART_DAYS = 730
STRATEGY_DISPLAY_ORDER = ("covered_call", "naked_short", "bull_put_spread")
_INSTRUMENT_CONTRACT_SIZE_CACHE: dict[str, Decimal] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _group_collateral_currency(group: dict[str, Any]) -> str:
    name = str(group.get("short_instrument_name") or "")
    coll = str(group.get("collateral_currency") or "").upper()
    if coll in ("BTC", "ETH", "USDC"):
        return coll
    if "_USDC-" in name:
        return "USDC"
    if name.startswith("BTC-"):
        return "BTC"
    if name.startswith("ETH-"):
        return "ETH"
    return str(group.get("currency") or "USDC").upper()


def _group_strike(group: dict[str, Any]) -> Decimal:
    strike = _dec(group.get("short_strike"))
    if strike > 0:
        return strike
    parsed = parse_option_name(str(group.get("short_instrument_name") or ""))
    if parsed:
        return to_decimal(parsed.get("strike"))
    return Decimal("0")


def _group_option_type(group: dict[str, Any]) -> str:
    opt = str(group.get("option_type") or "").lower()
    if opt in ("call", "put"):
        return opt
    parsed = parse_option_name(str(group.get("short_instrument_name") or ""))
    if parsed:
        return str(parsed.get("option_type") or "put")
    name = str(group.get("short_instrument_name") or "")
    if name.endswith("-C"):
        return "call"
    return "put"


def _group_index_usd_for_apr(group: dict[str, Any], index_usd: dict[str, Decimal]) -> Decimal:
    coll = _group_collateral_currency(group)
    if coll == "USDC":
        if _group_option_type(group) == "call":
            underlying = str(group.get("currency") or "BTC").upper()
            spot = index_usd.get(underlying)
            if spot is not None and spot > Decimal("100"):
                return spot
            for key in ("close_index_usd", "entry_index_usd"):
                val = _dec(group.get(key))
                if val > Decimal("100"):
                    return val
            strike = _dec(group.get("short_strike"))
            if strike > Decimal("100"):
                return strike
            return Decimal("0")
        return Decimal("0")
    idx = index_usd.get(coll) or _dec(group.get("close_index_usd")) or _dec(group.get("entry_index_usd")) or Decimal("0")
    return idx


def _contract_size_for_instrument(
    instrument_name: str,
    *,
    bot: DeribitOptionTrialBot,
    prefetch: ExchangePrefetch | None = None,
) -> Decimal:
    if not instrument_name:
        return Decimal("1")
    cached = _INSTRUMENT_CONTRACT_SIZE_CACHE.get(instrument_name)
    if cached is not None and cached > 0:
        return cached
    if prefetch is not None:
        for markets in prefetch.markets_by_currency.values():
            for inst in markets:
                if inst.instrument_name == instrument_name and inst.contract_size > 0:
                    _INSTRUMENT_CONTRACT_SIZE_CACHE[instrument_name] = inst.contract_size
                    return inst.contract_size
        future = prefetch.future_markets_by_name.get(instrument_name)
        if future is not None and future.contract_size > 0:
            _INSTRUMENT_CONTRACT_SIZE_CACHE[instrument_name] = future.contract_size
            return future.contract_size
    cs = bot._option_contract_size(instrument_name)
    if cs > 0:
        _INSTRUMENT_CONTRACT_SIZE_CACHE[instrument_name] = cs
    return cs if cs > 0 else Decimal("1")


def _decimalize(value: Any) -> Any:
    """Recursively convert Decimal / datetime payloads to JSON-friendly forms."""
    return json.loads(json.dumps(value, default=json_default, ensure_ascii=False))


def _has_private_creds(config: BotConfig) -> bool:
    return has_private_creds_config(config)


def _live_api_identity_config(config: BotConfig, label: str) -> str:
    """Same meaning as `_live_api_identity`, but accepts a config object directly."""
    if not _has_private_creds(config):
        return f"noid:{label}"
    cid = config.client_id.strip().lower()
    csec = config.client_secret.strip()
    return f"{cid}\0{csec}"


def _live_api_identity(account: DashboardAccount) -> str:
    """Identify the Deribit API login used for exchange balances / portfolio snapshots.

    Multiple dashboard rows may share one sub-account (same API key, different strategy
    state files). Summing those snapshots double-counts equity; de-dupe on this key.
    """
    return _live_api_identity_config(account.config, account.name)


def _merge_decimal_dict_union_max(into: dict[str, Decimal], extra: dict[str, Any]) -> None:
    """Union-merge ``extra`` into ``into`` (upper-case keys); same-book values should match — keep max."""
    for raw_key, raw_val in (extra or {}).items():
        k = str(raw_key).upper()
        v = _dec(raw_val)
        if k not in into:
            into[k] = v
        else:
            into[k] = max(into[k], v)


def _merge_portfolio_for_same_api_identity(base: dict[str, Any], extra: dict[str, Any]) -> None:
    """Mutate ``base`` portfolio dict by layering per-book views from ``extra``.

    Same Deribit login may load multiple env files with different ``TRADED_COLLATERALS``.
    Each bot snapshot only lists books it manages; union-merge restores missing collateral rows
    for the dashboard without double-counting headline totals (those stay on the first snapshot).
    """
    if not extra:
        return

    for key in (
        "equity_by_book",
        "day_start_equity_by_book",
        "day_net_flow_usdc_by_book",
        "day_pnl_usdc_ex_flow_by_book",
        "day_pnl_usdc_ex_flow_ex_spot_by_book",
        "day_drawdown_pct_by_book",
    ):
        merged_map: dict[str, Decimal] = {}
        for raw_k, raw_v in (base.get(key) or {}).items():
            merged_map[str(raw_k).upper()] = _dec(raw_v)
        _merge_decimal_dict_union_max(merged_map, extra.get(key) or {})
        base[key] = merged_map

    base["halt_new_entries"] = bool(base.get("halt_new_entries")) or bool(extra.get("halt_new_entries"))
    base["hard_derisk"] = bool(base.get("hard_derisk")) or bool(extra.get("hard_derisk"))
    base["cooling_down"] = bool(base.get("cooling_down")) or bool(extra.get("cooling_down"))

    base_delta = dict(base.get("delta_totals_by_currency") or {})
    _merge_decimal_dict_union_max(base_delta, extra.get("delta_totals_by_currency") or {})
    base["delta_totals_by_currency"] = base_delta

    base_reg = dict(base.get("regime_by_currency") or {})
    for raw_k, raw_v in (extra.get("regime_by_currency") or {}).items():
        kk = str(raw_k).upper()
        base_reg[kk] = _worst_regime([str(base_reg.get(kk, "normal")), str(raw_v)])
    base["regime_by_currency"] = base_reg

    base_margin = dict(base.get("margin_ratios_by_currency") or {})
    for raw_k, ratios in (extra.get("margin_ratios_by_currency") or {}).items():
        kk = str(raw_k).upper()
        if kk not in base_margin:
            base_margin[kk] = ratios
    base["margin_ratios_by_currency"] = base_margin

    base_detail = {str(k).upper(): list(v or []) for k, v in (base.get("regime_detail_by_currency") or {}).items()}
    for raw_k, details in (extra.get("regime_detail_by_currency") or {}).items():
        kk = str(raw_k).upper()
        base_detail.setdefault(kk, []).extend(list(details or []))
    base["regime_detail_by_currency"] = base_detail

    base_halt_book = {
        str(k).upper(): list(v or []) for k, v in (base.get("halt_entry_reasons_by_book") or {}).items()
    }
    for raw_k, reasons in (extra.get("halt_entry_reasons_by_book") or {}).items():
        kk = str(raw_k).upper()
        base_halt_book.setdefault(kk, []).extend(list(reasons or []))
    base["halt_entry_reasons_by_book"] = base_halt_book

    base_cd_book = {str(k).upper(): v for k, v in (base.get("cooldown_until_ms_by_book") or {}).items()}
    for raw_k, ms in (extra.get("cooldown_until_ms_by_book") or {}).items():
        kk = str(raw_k).upper()
        choices = [base_cd_book.get(kk), ms]
        numeric = [int(x) for x in choices if x is not None]
        base_cd_book[kk] = max(numeric) if numeric else base_cd_book.get(kk)
    base["cooldown_until_ms_by_book"] = base_cd_book

    for key in ("cooling_down_by_book", "hard_derisk_by_book", "halt_entries_by_book"):
        sub = dict(base.get(key) or {})
        for raw_k, flag in (extra.get(key) or {}).items():
            kk = str(raw_k).upper()
            sub[kk] = bool(sub.get(kk)) or bool(flag)
        base[key] = sub


def _merge_status_group_for_equity(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Deep-merge a list of status payloads that share one Deribit API identity."""
    merged = copy.deepcopy(group[0])
    portfolio = dict(merged.get("portfolio") or {})
    merged["portfolio"] = portfolio
    for other in group[1:]:
        _merge_portfolio_for_same_api_identity(portfolio, other.get("portfolio") or {})
    return merged


def _dedupe_statuses_for_equity_aggregate(
    accounts: list[DashboardAccount],
    statuses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One status per `_live_api_identity`, with per-book portfolio rows union-merged.

    Order follows first-seen account row (same as the legacy de-dupe).
    """
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account, status in zip(accounts, statuses, strict=False):
        key = _live_api_identity(account)
        if key not in buckets:
            order.append(key)
        buckets[key].append(status)

    out: list[dict[str, Any]] = []
    for key in order:
        group = buckets[key]
        if len(group) == 1:
            out.append(group[0])
        else:
            out.append(_merge_status_group_for_equity(group))
    return out


def _ledger_path_for(ts: datetime, root: Path) -> Path:
    return root / f"equity_{ts.strftime('%Y%m%d')}.jsonl"


def _iter_ledger_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.glob("equity_*.jsonl") if p.is_file())


def _read_ledger(root: Path, *, since_ms: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _iter_ledger_files(root):
        try:
            with path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if since_ms is not None and int(row.get("ts_ms") or 0) < since_ms:
                        continue
                    rows.append(row)
        except OSError as exc:  # pragma: no cover — best-effort log only.
            LOGGER.warning("ledger read failed for %s: %s", path, exc)
    rows.sort(key=lambda r: int(r.get("ts_ms") or 0))
    return rows


def _append_ledger(root: Path, row: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _ledger_path_for(datetime.now(tz=UTC), root)
    line = json.dumps(row, default=json_default, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def _latest_ledger_row(root: Path) -> dict[str, Any] | None:
    rows = _read_ledger(root)
    return rows[-1] if rows else None


def _latest_ledger_snapshot(
    accounts: list[DashboardAccount],
    *,
    scheduler_states: list[SnapshotState] | None = None,
    snapshot_interval_sec: int = DEFAULT_SNAPSHOT_INTERVAL_SEC,
) -> dict[str, Any]:
    """Aggregate last on-disk equity rows (no Deribit). De-dupe shared API identities."""
    now_ms = utc_now_ms()
    account_entries: list[dict[str, Any]] = []
    seen_identity: set[str] = set()
    included_rows: list[dict[str, Any]] = []

    for account in accounts:
        row = _latest_ledger_row(account.ledger_root)
        if row is None:
            continue
        ts = int(row.get("ts_ms") or 0)
        account_entries.append(
            {
                "name": account.name,
                "env": row.get("env") or account.config.env,
                "option_strategy": row.get("option_strategy") or account.config.option_strategy,
                "ts_ms": ts,
                "ledger_dir": str(account.ledger_root),
            }
        )
        identity = _live_api_identity(account)
        if identity in seen_identity:
            continue
        seen_identity.add(identity)
        included_rows.append(row)

    scheduler_info: dict[str, Any] = {}
    if scheduler_states is not None:
        last_attempts = [s.last_attempt_ms for s in scheduler_states if s.last_attempt_ms is not None]
        last_successes = [s.last_success_ms for s in scheduler_states if s.last_success_ms is not None]
        scheduler_info = {
            "last_attempt_ms": max(last_attempts, default=None),
            "last_success_ms": max(last_successes, default=None),
            "interval_sec": snapshot_interval_sec,
        }

    if not account_entries:
        return {
            "source": "none",
            "snapshot_ts_ms": None,
            "freshness_ms": None,
            "portfolio": {},
            "accounts": [],
            "scheduler": scheduler_info,
        }

    min_ts = min(int(r.get("ts_ms") or 0) for r in included_rows)
    total_equity = sum((_dec(r.get("total_equity_usdc")) for r in included_rows), Decimal("0"))
    day_start = sum((_dec(r.get("day_start_equity_usdc")) for r in included_rows), Decimal("0"))
    day_net_flow = sum((_dec(r.get("day_net_flow_usdc")) for r in included_rows), Decimal("0"))
    day_pnl = sum((_dec(r.get("day_pnl_usdc_ex_flow")) for r in included_rows), Decimal("0"))
    day_pnl_ex_spot = sum((_dec(r.get("day_pnl_usdc_ex_flow_ex_spot")) for r in included_rows), Decimal("0"))
    open_max_loss = sum((_dec(r.get("open_max_loss_usdc")) for r in included_rows), Decimal("0"))
    equity_by_book: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    day_start_by_book: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    day_pnl_by_book: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in included_rows:
        for book, val in (row.get("equity_by_book") or {}).items():
            equity_by_book[str(book).upper()] += _dec(val)
        for book, val in (row.get("day_start_equity_by_book") or {}).items():
            day_start_by_book[str(book).upper()] += _dec(val)
        for book, val in (row.get("day_pnl_usdc_ex_flow_by_book") or {}).items():
            day_pnl_by_book[str(book).upper()] += _dec(val)
    drawdown_pct = max((_dec(r.get("day_drawdown_pct")) for r in included_rows), default=Decimal("0"))

    return {
        "source": "ledger",
        "snapshot_ts_ms": min_ts,
        "freshness_ms": max(0, now_ms - min_ts) if min_ts > 0 else None,
        "portfolio": {
            "total_equity_usdc": str(total_equity),
            "day_start_equity_usdc": str(day_start),
            "day_net_flow_usdc": str(day_net_flow),
            "day_pnl_usdc_ex_flow": str(day_pnl),
            "day_pnl_usdc_ex_flow_ex_spot": str(day_pnl_ex_spot),
            "day_drawdown_pct": str(drawdown_pct),
            "open_max_loss": str(open_max_loss),
            "equity_by_book": {k: str(v) for k, v in sorted(equity_by_book.items())},
            "day_start_equity_by_book": {k: str(v) for k, v in sorted(day_start_by_book.items())},
            "day_pnl_usdc_ex_flow_by_book": {k: str(v) for k, v in sorted(day_pnl_by_book.items())},
        },
        "accounts": account_entries,
        "scheduler": scheduler_info,
    }


# ---------------------------------------------------------------------------
# Snapshot scheduler
# ---------------------------------------------------------------------------


@dataclass
class SnapshotState:
    last_attempt_ms: int | None = None
    last_success_ms: int | None = None
    last_error: str | None = None
    running: bool = False


@dataclass(frozen=True)
class DashboardAccount:
    name: str
    env_file: Path
    config: BotConfig
    state_path: Path
    ledger_root: Path


def _parse_account_env_files(raw: str | None) -> tuple[Path, ...]:
    if not raw:
        return ()
    return tuple(Path(item.strip()) for item in raw.split(",") if item.strip())


def _slugify_account_name(raw: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw.strip())
    return slug.strip("_.-") or "account"


def _default_account_name(env_file: Path, config: BotConfig) -> str:
    slug = account_slug_from_env_path(env_file)
    if slug:
        return _slugify_account_name(slug)
    name = env_file.name
    if name.startswith(".env."):
        name = name.removeprefix(".env.")
    elif name == ".env":
        name = config.order_label_prefix or config.option_strategy
    else:
        name = env_file.stem
    return _slugify_account_name(name)


def _resolve_frontend_ledger_base(env_files: tuple[Path, ...]) -> Path:
    """Ledger root: per-investor dir by default; legacy flat ``data/frontend_ledger`` otherwise."""
    explicit = os.environ.get("FRONTEND_LEDGER_DIR")
    if explicit:
        return Path(explicit)
    repo_root = find_repo_root(env_files[0] if env_files else Path.cwd())
    investor_id = resolve_investor_scope(env_files, repo_root=repo_root)
    if investor_id and repo_root is not None:
        return investor_frontend_ledger_dir(repo_root, investor_id)
    if investor_id:
        return LEDGER_DIR / investor_id
    return LEDGER_DIR


def _configure_metrics_db(env_files: tuple[Path, ...]) -> Path:
    global _active_metrics_db_path
    explicit = os.environ.get("FRONTEND_METRICS_DB")
    if explicit:
        path = Path(explicit)
    else:
        repo_root = find_repo_root(env_files[0] if env_files else Path.cwd())
        investor_id = resolve_investor_scope(env_files, repo_root=repo_root)
        if investor_id and repo_root is not None:
            path = investor_metrics_db_path(repo_root, investor_id)
        elif investor_id:
            path = LEDGER_DIR / investor_id / "metrics.db"
        else:
            path = LEGACY_METRICS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _active_metrics_db_path = path
    return path


def _make_dashboard_accounts(
    *,
    env_file: str | Path,
    account_env_files: tuple[str | Path, ...] | None,
) -> list[DashboardAccount]:
    env_files = (
        tuple(Path(item) for item in account_env_files)
        if account_env_files
        else _parse_account_env_files(os.environ.get("FRONTEND_ACCOUNT_ENV_FILES"))
    )
    if not env_files:
        env_files = (Path(env_file),)

    ledger_base = _resolve_frontend_ledger_base(env_files)
    multi = len(env_files) > 1
    seen: dict[str, int] = {}
    accounts: list[DashboardAccount] = []
    for item in env_files:
        cfg = load_config(item, require_private=False)
        base_name = _default_account_name(item, cfg)
        index = seen.get(base_name, 0) + 1
        seen[base_name] = index
        name = base_name if index == 1 else f"{base_name}_{index}"
        ledger_root = ledger_base / name if multi else ledger_base
        accounts.append(
            DashboardAccount(
                name=name,
                env_file=item,
                config=cfg,
                state_path=Path(cfg.state_file),
                ledger_root=ledger_root,
            )
        )
    return accounts


class EquitySnapshotScheduler:
    """Tiny self-contained scheduler.

    APScheduler would work too, but a single daemon thread keeps the
    dependency surface small (no extra package, no signal handling).
    """

    def __init__(
        self,
        *,
        account_name: str,
        bot_factory: Callable[[], DeribitOptionTrialBot],
        interval_sec: int,
        ledger_root: Path,
        config: BotConfig,
    ) -> None:
        self._account_name = account_name
        self._bot_factory = bot_factory
        self._interval_sec = max(30, int(interval_sec))
        self._ledger_root = ledger_root
        self._config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = SnapshotState()

    def start(self) -> None:
        if not _has_private_creds(self._config):
            LOGGER.info("snapshot scheduler disabled: no private creds in env")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="equity-snapshot", daemon=True)
        self.state.running = True
        self._thread.start()
        LOGGER.info("snapshot scheduler started (interval=%ss)", self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.state.running = False

    def _loop(self) -> None:
        # Take an immediate snapshot so the ledger gets a row even on first
        # boot, then sleep on the configured cadence.
        self._tick()
        while not self._stop.wait(self._interval_sec):
            self._tick()

    def _tick(self) -> None:
        self.state.last_attempt_ms = utc_now_ms()
        try:
            bot = self._bot_factory()
            context = bot._load_runtime()
            snapshot = context.snapshot
            row = {
                "ts_ms": utc_now_ms(),
                "account_name": self._account_name,
                "env": self._config.env,
                "option_strategy": self._config.option_strategy,
                "state_file": str(self._config.state_file),
                "regime": snapshot.regime.value,
                "total_equity_usdc": str(snapshot.total_equity_usdc),
                "day_start_equity_usdc": str(snapshot.day_start_equity_usdc),
                "day_net_flow_usdc": str(snapshot.day_net_flow_usdc),
                "day_pnl_usdc_ex_flow": str(snapshot.day_pnl_usdc_ex_flow),
                "day_pnl_usdc_ex_flow_ex_spot": str(snapshot.day_pnl_usdc_ex_flow_ex_spot),
                "day_drawdown_pct": str(snapshot.day_drawdown_pct),
                "open_max_loss_usdc": str(snapshot.open_max_loss),
                "initial_margin_ratio": str(snapshot.initial_margin_ratio),
                "maintenance_margin_ratio": str(snapshot.maintenance_margin_ratio),
                "equity_by_book": {k: str(v) for k, v in snapshot.equity_by_book.items()},
                "day_start_equity_by_book": {
                    k: str(v) for k, v in snapshot.day_start_equity_by_book.items()
                },
                "day_net_flow_usdc_by_book": {
                    k: str(v) for k, v in snapshot.day_net_flow_usdc_by_book.items()
                },
                "day_pnl_usdc_ex_flow_by_book": {
                    k: str(v) for k, v in snapshot.day_pnl_usdc_ex_flow_by_book.items()
                },
                "day_pnl_usdc_ex_flow_ex_spot_by_book": {
                    k: str(v) for k, v in snapshot.day_pnl_usdc_ex_flow_ex_spot_by_book.items()
                },
                "delta_totals_by_currency": {
                    k: str(v) for k, v in snapshot.delta_totals_by_currency.items()
                },
                "regime_by_currency": {
                    k: v.value for k, v in snapshot.regime_by_currency.items()
                },
                "halt_new_entries": snapshot.halt_new_entries,
                "hard_derisk": snapshot.hard_derisk,
            }
            _append_ledger(self._ledger_root, row)
            self.state.last_success_ms = utc_now_ms()
            self.state.last_error = None
        except Exception as exc:  # noqa: BLE001 — scheduler must not crash the server.
            LOGGER.warning("equity snapshot failed: %s", exc)
            self.state.last_error = str(exc)


@dataclass
class TradeJournalSyncState:
    last_attempt_ms: int | None = None
    last_success_ms: int | None = None
    last_error: str | None = None
    last_inserted: int = 0
    running: bool = False


class TradeJournalSyncScheduler:
    """Background incremental sync of Deribit fills into trade_journal.db."""

    def __init__(
        self,
        *,
        accounts: list[DashboardAccount],
        interval_sec: int,
    ) -> None:
        self._accounts = accounts
        self._interval_sec = max(60, int(interval_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = TradeJournalSyncState()

    def start(self) -> None:
        if not any(_has_private_creds(account.config) for account in self._accounts):
            LOGGER.info("trade journal sync scheduler disabled: no private creds")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="trade-journal-sync", daemon=True)
        self.state.running = True
        self._thread.start()
        LOGGER.info("trade journal sync scheduler started (interval=%ss)", self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.state.running = False

    def run_once(self) -> dict[str, Any]:
        """Run a single sync pass (used by scheduler tick and manual API)."""
        self.state.last_attempt_ms = utc_now_ms()
        results: list[dict[str, Any]] = []
        total_inserted = 0
        errors: list[str] = []
        for account in self._accounts:
            if not _has_private_creds(account.config):
                results.append({"account": account.name, "skipped": True, "reason": "no_credentials"})
                continue
            try:
                row = sync_incremental_journal(account.env_file)
                total_inserted += int(row.get("api_inserted") or 0)
                row["account"] = account.name
                results.append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{account.name}: {exc}")
                results.append({"account": account.name, "error": str(exc)})
        self.state.last_inserted = total_inserted
        if errors:
            self.state.last_error = "; ".join(errors)
            LOGGER.warning("trade journal sync had errors: %s", self.state.last_error)
        else:
            self.state.last_success_ms = utc_now_ms()
            self.state.last_error = None
            if total_inserted:
                LOGGER.info("trade journal sync inserted %s fill(s)", total_inserted)
        return {"accounts": results, "api_inserted": total_inserted}

    def _loop(self) -> None:
        self._tick()
        while not self._stop.wait(self._interval_sec):
            self._tick()

    def _tick(self) -> None:
        try:
            self.run_once()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("trade journal sync failed: %s", exc)
            self.state.last_error = str(exc)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


class _TtlCache:
    """Trivial TTL cache — just enough to avoid hammering Deribit."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[Any, tuple[float, Any]] = {}
        self._inflight: dict[Any, threading.Event] = {}

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                leader = True
            else:
                leader = False
        if not leader:
            event.wait(timeout=120)
            with self._lock:
                cached = self._store.get(key)
                if cached is not None:
                    return cached[1]
            return factory()
        try:
            value = factory()
            with self._lock:
                self._store[key] = (time.monotonic(), value)
            return value
        finally:
            with self._lock:
                done = self._inflight.pop(key, None)
            if done is not None:
                done.set()

    def try_get(self, key: Any) -> Any | None:
        """Return cached value when present and fresh; otherwise ``None``."""
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
        return None

    def cache_age_ms(self, key: Any) -> int | None:
        """Milliseconds since ``key`` was last stored (including fresh computes)."""
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is None:
                return None
            return int((now - cached[0]) * 1000)

    def seed(self, key: Any, value: Any) -> None:
        """Store ``value`` under ``key`` as if freshly computed (for cross-endpoint cache warm-up)."""
        with self._lock:
            self._store[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Domain helpers (closed-group → time series)
# ---------------------------------------------------------------------------

_closed_groups_payload_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_closed_groups_payload_cache_lock = threading.Lock()
_closed_groups_payload_load_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _state_path_mtime(state_path: Path) -> float:
    try:
        return state_path.stat().st_mtime if state_path.is_file() else 0.0
    except OSError:
        return 0.0


def _spot_index_decimals(spot_payload: dict[str, Any] | None) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for sym in ("BTC", "ETH"):
        px = _dec((spot_payload or {}).get(sym))
        if px > 0:
            out[sym] = px
    return out


def _backfill_row_collateral_native(
    row: dict[str, Any],
    spot_index: dict[str, Decimal],
    *,
    state_path: Path | None = None,
) -> None:
    if str(row.get("status") or "").lower() != "closed":
        return
    book = str(row.get("collateral_currency") or row.get("currency") or "").upper()
    spot = spot_index.get(book)
    if book not in {"BTC", "ETH"}:
        return
    group = TradeGroup.from_dict(row)
    journal_rows = None
    if state_path is not None and group.group_id:
        journal_rows = _journal_executions_for_group(state_path, group.group_id)
    group.backfill_realized_pnl_collateral_native(
        spot_index_usd=spot if spot is not None and spot > 0 else None,
        journal_executions=journal_rows,
    )
    if group.realized_pnl_collateral_native is not None:
        row["realized_pnl_collateral_native"] = format_decimal(group.realized_pnl_collateral_native, 12)
    if group.realized_pnl is not None and spot is not None and spot > 0:
        row["realized_pnl"] = format_decimal(group.realized_pnl, 8)


def _apply_spot_native_backfill(payload: dict[str, Any], spot_index: dict[str, Decimal]) -> None:
    if not spot_index:
        return
    for row in payload.get("closed") or []:
        if isinstance(row, dict):
            _backfill_row_collateral_native(row, spot_index)


def _journal_executions_for_group(state_path: Path, group_id: str) -> list[dict[str, Any]]:
    journal_path = journal_db_path_for_state(state_path)
    if not journal_path.is_file():
        return []
    store = TradeJournalStore(journal_path)
    scope = scope_key_for_state(state_path)
    return store.list_executions(scope, group_id=group_id, limit=50)


def _load_closed_groups_payload(
    state_path: Path,
    *,
    spot_index: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    if not state_path.exists():
        return {"open": [], "closed": [], "performance_excluded_closed_group_count": 0, "next_group_id": None}
    store = StrategyStateStore(state_path)
    state = store.load()
    excluded_group_ids = load_performance_exclusion_group_ids(state_path)
    open_short_names = open_short_instrument_names(state.groups)
    open_groups = [g.to_dict() for g in state.groups if g.status != "closed"]
    all_closed_groups = [g for g in state.groups if g.status == "closed"]
    closed_groups = []
    for g in all_closed_groups:
        if g.group_id in excluded_group_ids:
            continue
        if is_phantom_reconcile_close(g, open_short_names=open_short_names):
            continue
        book = (g.collateral_currency or g.currency or "").upper()
        spot = (spot_index or {}).get(book)
        journal_rows = _journal_executions_for_group(state_path, g.group_id) if g.is_coin_collateral() else None
        g.backfill_realized_pnl_collateral_native(
            spot_index_usd=spot if spot is not None and spot > 0 else None,
            journal_executions=journal_rows,
        )
        closed_groups.append(g.to_dict())
    payload = {
        "open": _decimalize(open_groups),
        "closed": _decimalize(closed_groups),
        "performance_excluded_closed_group_count": len(all_closed_groups) - len(closed_groups),
        "next_group_id": state.next_group_id,
    }
    _apply_spot_native_backfill(payload, spot_index or {})
    return payload


def _closed_groups_payload(
    state_path: Path,
    *,
    spot_index: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Load open/closed groups from disk; memoized by path + mtime to cut lock/parse churn."""
    key = str(state_path.resolve())
    mtime = _state_path_mtime(state_path)
    with _closed_groups_payload_cache_lock:
        cached = _closed_groups_payload_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    with _closed_groups_payload_load_locks[key]:
        with _closed_groups_payload_cache_lock:
            cached = _closed_groups_payload_cache.get(key)
            if cached is not None and cached[0] == mtime:
                return cached[1]
        payload = _load_closed_groups_payload(state_path, spot_index=spot_index)
        mtime = _state_path_mtime(state_path)
        with _closed_groups_payload_cache_lock:
            _closed_groups_payload_cache[key] = (mtime, payload)
    if spot_index:
        payload = copy.deepcopy(payload)
        _apply_spot_native_backfill(payload, spot_index)
    return payload


def _entry_dte_days_at_open(group: dict[str, Any]) -> Decimal:
    return entry_dte_days_at_open(
        entry_timestamp_ms=int(group.get("entry_timestamp_ms") or 0),
        expiration_timestamp_ms=int(group.get("expiration_timestamp_ms") or 0),
    )


def _ensure_entry_net_apr(
    group: dict[str, Any],
    *,
    config: BotConfig,
    index_usd: dict[str, Decimal],
    contract_size: Decimal = Decimal("1"),
) -> None:
    from .models import TradeGroup

    dte = _entry_dte_days_at_open(group)
    if dte <= 0:
        return
    coll = _group_collateral_currency(group)
    idx = _group_index_usd_for_apr(group, index_usd)
    if coll != "USDC" and idx <= 0:
        if _dec(group.get("entry_net_apr")) <= 0:
            group["entry_net_apr"] = format_decimal(Decimal("0"), 8)
        return
    strike = _group_strike(group)
    if strike > 0 and _dec(group.get("short_strike")) <= 0:
        group["short_strike"] = format_decimal(strike, 8)

    tg = TradeGroup.from_dict(group)
    if tg.entry_index_usd <= 0 and idx > 0:
        tg.entry_index_usd = idx
    apr = tg.entry_net_apr_at_open(contract_size=contract_size)
    if apr <= 0:
        if _dec(group.get("entry_net_apr")) <= 0:
            group["entry_net_apr"] = format_decimal(Decimal("0"), 8)
        return
    group["entry_net_apr"] = format_decimal(apr, 8)


def _ensure_realized_apr_on_equity(
    group: dict[str, Any],
    *,
    index_usd: dict[str, Decimal],
    contract_size: Decimal = Decimal("1"),
) -> None:
    closed_ms = int(group.get("closed_timestamp_ms") or 0)
    entry_ms = int(group.get("entry_timestamp_ms") or 0)
    if closed_ms <= entry_ms:
        return
    coll = _group_collateral_currency(group)
    pnl_native = _dec(group.get("realized_pnl_collateral_native"))
    if pnl_native == 0 and coll == "USDC":
        pnl_native = _dec(group.get("realized_pnl"))
    elif pnl_native == 0:
        idx = index_usd.get(coll) or _dec(group.get("close_index_usd")) or Decimal("0")
        pnl_usdc = _dec(group.get("realized_pnl"))
        if idx > 0 and pnl_usdc != 0:
            pnl_native = pnl_usdc / idx
    if pnl_native == 0:
        return
    idx = _group_index_usd_for_apr(group, index_usd)
    strike = _group_strike(group)
    if strike > 0 and _dec(group.get("short_strike")) <= 0:
        group["short_strike"] = format_decimal(strike, 8)
    apr = realized_apr_from_close(
        strategy=str(group.get("strategy") or ""),
        collateral_currency=coll,
        option_type=_group_option_type(group),
        quantity=_dec(group.get("quantity")) or Decimal("1"),
        contract_size=contract_size,
        strike=strike,
        index_price_usd=idx,
        estimated_im_collateral=_dec(group.get("estimated_im_collateral")),
        covered_underlying_quantity=_dec(group.get("covered_underlying_quantity")),
        pnl_collateral_native=pnl_native,
        entry_timestamp_ms=entry_ms,
        closed_timestamp_ms=closed_ms,
    )
    group["realized_apr_on_equity"] = format_decimal(apr, 8)
    if not group.get("realized_annualized_return"):
        group["realized_annualized_return"] = group["realized_apr_on_equity"]


def _enrich_groups_payload_open_unrealized(
    bot: DeribitOptionTrialBot,
    payload: dict[str, Any],
    *,
    exchange_prefetch: ExchangePrefetch | None = None,
) -> None:
    """Mirror engine open-row fields so the UI works from ``/api/groups`` alone.

    Persisted state rows omit ``unrealized_*`` and index data; without this,
    BTC/ETH collateral rows can show USD unrealized while native stays ``—``.
    """
    open_rows = payload.get("open") or []
    cache: dict[str, OrderBookSnapshot] = {}
    underlying: dict[str, str] = {}
    index_usd: dict[str, Decimal] = {}
    for sym in ("BTC", "ETH"):
        idx = bot._currency_index_price(sym, cache)
        underlying[sym] = format_decimal(idx, 4) if idx > 0 else "0"
        if idx > 0:
            index_usd[sym] = idx
    index_usd["USDC"] = Decimal("1")
    payload["underlying_index_usd"] = underlying
    closed_rows = payload.get("closed") or []

    def _contract_size_for(instrument_name: str) -> Decimal:
        return _contract_size_for_instrument(
            instrument_name,
            bot=bot,
            prefetch=exchange_prefetch,
        )

    for g in open_rows:
        instrument_name = str(g.get("short_instrument_name") or "")
        cs = _contract_size_for(instrument_name)
        g["contract_size"] = format_decimal(cs, 8)
        _ensure_entry_net_apr(
            g,
            config=bot.config,
            index_usd=index_usd,
            contract_size=cs,
        )
        ec = to_decimal(g.get("entry_credit"))
        cd = to_decimal(g.get("current_debit"))
        unrealized_usdc = ec - cd
        g["unrealized_usdc_estimate"] = format_decimal(unrealized_usdc, 8)
        coll = str(g.get("collateral_currency") or "").upper()
        if coll in ("BTC", "ETH"):
            # Use settlement / book currency for index (BTC, ETH), not ``currency`` which can be
            # empty in older persisted rows — that produced idx=0 and a blank native column.
            idx = bot._currency_index_price(coll, cache)
            if idx > 0:
                g["unrealized_coin_native"] = format_decimal(unrealized_usdc / idx, 12)
            else:
                g["unrealized_coin_native"] = None
        else:
            g["unrealized_coin_native"] = None

    for g in closed_rows:
        instrument_name = str(g.get("short_instrument_name") or "")
        cs = _contract_size_for(instrument_name)
        g["contract_size"] = format_decimal(cs, 8)
        _ensure_entry_net_apr(g, config=bot.config, index_usd=index_usd, contract_size=cs)
        _ensure_realized_apr_on_equity(g, index_usd=index_usd, contract_size=cs)


def _bucket_day_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _metrics_store() -> MetricsStore:
    path = _active_metrics_db_path or LEGACY_METRICS_DB_PATH
    return MetricsStore(path)


def _ensure_daily_pnl_synced(
    accounts: list[DashboardAccount],
    *,
    store: MetricsStore | None = None,
) -> MetricsStore:
    """Load daily realized PnL buckets from SQLite; rebuild when state files change."""
    metrics = store or _metrics_store()
    scope_key = performance_scope_key(accounts)
    fingerprint = fingerprint_from_cache_key(_closed_groups_cache_key(accounts))
    if metrics.is_synced(scope_key, fingerprint):
        return metrics
    closed = _aggregate_closed_groups(accounts)
    metrics.sync_from_closed(
        scope_key,
        fingerprint,
        closed,
        synced_at_ms=utc_now_ms(),
    )
    return metrics


def _cumulative_pnl_series_from_daily(
    daily_by_book: dict[str, dict[str, Decimal]],
    daily_total: dict[str, Decimal],
    *,
    realized_count: int,
) -> dict[str, Any]:
    days_sorted = sorted(daily_total.keys())
    books_sorted = sorted(daily_by_book.keys())

    cumulative_total: list[dict[str, Any]] = []
    running_total = Decimal("0")
    for day in days_sorted:
        running_total += daily_total[day]
        cumulative_total.append({"date": day, "pnl_usdc": str(running_total)})

    cumulative_by_book: dict[str, list[dict[str, Any]]] = {}
    for book in books_sorted:
        running = Decimal("0")
        rows: list[dict[str, Any]] = []
        for day in days_sorted:
            running += daily_by_book[book].get(day, Decimal("0"))
            rows.append({"date": day, "pnl_usdc": str(running)})
        cumulative_by_book[book] = rows

    daily_total_rows = [{"date": day, "pnl_usdc": str(daily_total[day])} for day in days_sorted]
    daily_by_book_rows = {
        book: [
            {"date": day, "pnl_usdc": str(daily_by_book[book].get(day, Decimal("0")))}
            for day in days_sorted
        ]
        for book in books_sorted
    }

    return {
        "books": books_sorted,
        "cumulative_total": cumulative_total,
        "cumulative_by_book": cumulative_by_book,
        "daily_total": daily_total_rows,
        "daily_by_book": daily_by_book_rows,
        "realized_count": realized_count,
    }


def _cumulative_pnl_series(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Return per-book + total cumulative realized PnL by UTC day.

    Each closed group has a ``closed_timestamp_ms`` and ``realized_pnl`` in
    USDC equivalent (engine invariant). We bucket by UTC day so the chart
    works even when only ~20 trades exist.
    """
    daily_by_book: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    daily_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    realized = [
        g for g in closed
        if g.get("closed_timestamp_ms") is not None and g.get("realized_pnl") is not None
    ]
    for g in realized:
        day = _bucket_day_utc(int(g["closed_timestamp_ms"]))
        pnl = to_decimal(g["realized_pnl"])
        book = str(g.get("collateral_currency") or g.get("currency") or "USDC").upper()
        daily_by_book[book][day] += pnl
        daily_total[day] += pnl

    days_sorted = sorted(set(daily_total.keys()))
    books_sorted = sorted(daily_by_book.keys())

    cumulative_total: list[dict[str, Any]] = []
    running_total = Decimal("0")
    for day in days_sorted:
        running_total += daily_total[day]
        cumulative_total.append({"date": day, "pnl_usdc": str(running_total)})

    cumulative_by_book: dict[str, list[dict[str, Any]]] = {}
    for book in books_sorted:
        running = Decimal("0")
        rows: list[dict[str, Any]] = []
        for day in days_sorted:
            running += daily_by_book[book].get(day, Decimal("0"))
            rows.append({"date": day, "pnl_usdc": str(running)})
        cumulative_by_book[book] = rows

    daily_total_rows = [
        {"date": day, "pnl_usdc": str(daily_total[day])} for day in days_sorted
    ]
    daily_by_book_rows = {
        book: [
            {"date": day, "pnl_usdc": str(daily_by_book[book].get(day, Decimal("0")))}
            for day in days_sorted
        ]
        for book in books_sorted
    }

    return _cumulative_pnl_series_from_daily(
        {book: dict(days) for book, days in daily_by_book.items()},
        dict(daily_total),
        realized_count=len(realized),
    )


def _daily_equity_by_utc_day(accounts: list[DashboardAccount]) -> dict[date, Decimal]:
    """Last snapshot per UTC day per account, summed across dashboard accounts."""
    last_by_day_account: dict[tuple[date, str], tuple[int, Decimal]] = {}
    for account in accounts:
        for row in _read_ledger(account.ledger_root):
            ts = int(row.get("ts_ms") or 0)
            if ts <= 0:
                continue
            equity = _dec(row.get("total_equity_usdc"))
            if equity <= 0:
                continue
            day = datetime.fromtimestamp(ts / 1000, tz=UTC).date()
            key = (day, account.name)
            prev = last_by_day_account.get(key)
            if prev is None or ts >= prev[0]:
                last_by_day_account[key] = (ts, equity)
    by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for (day, _account), (_ts, equity) in last_by_day_account.items():
        by_day[day] += equity
    return dict(by_day)


def _equity_on_day(
    equity_by_day: dict[date, Decimal],
    day: date,
    fallback: Decimal,
) -> Decimal:
    """Equity for APR denominator on ``day`` (carry backward within ledger, else fallback)."""
    direct = equity_by_day.get(day)
    if direct is not None and direct > 0:
        return direct
    prior_days = [d for d in equity_by_day if d <= day and equity_by_day[d] > 0]
    if prior_days:
        return equity_by_day[max(prior_days)]
    return fallback if fallback > 0 else Decimal("0")


def _ledger_equity_cache_key(accounts: list[DashboardAccount]) -> tuple[Any, ...]:
    parts: list[Any] = []
    for account in accounts:
        for path in _iter_ledger_files(account.ledger_root):
            try:
                stat = path.stat()
                parts.append((account.name, str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                parts.append((account.name, str(path), 0, 0))
    return tuple(parts)


def _rolling_apr_from_daily_totals(
    pnl_by_day: dict[date, Decimal],
    *,
    window_days: int,
    effective_capital_usdc: Decimal,
    equity_by_day: dict[date, Decimal] | None = None,
    max_chart_days: int = ROLLING_APR_MAX_CHART_DAYS,
) -> list[dict[str, Any]]:
    """Rolling annualized APR sampled per UTC day (O(n) sliding window).

    Each sample divides by that day's total equity (from ledger snapshots) when
    available, so a later deposit does not deflate earlier points on the chart.
    """
    if window_days < 1 or not pnl_by_day:
        return []

    first_day = min(pnl_by_day.keys())
    last_day = max(pnl_by_day.keys())
    today = datetime.now(tz=UTC).date()
    if today > last_day:
        last_day = today

    chart_start = first_day
    if max_chart_days > 0 and (last_day - first_day).days + 1 > max_chart_days:
        chart_start = last_day - timedelta(days=max_chart_days - 1)

    sample_days = Decimal(str(window_days))
    equity_lookup = equity_by_day or {}
    rows: list[dict[str, Any]] = []
    cursor = chart_start
    window_pnl = Decimal("0")
    warm = cursor - timedelta(days=window_days - 1)
    while warm <= cursor:
        window_pnl += pnl_by_day.get(warm, Decimal("0"))
        warm += timedelta(days=1)

    while True:
        capital = _equity_on_day(equity_lookup, cursor, effective_capital_usdc)
        if capital <= 0:
            annualized = Decimal("0")
        else:
            annualized = (window_pnl * Decimal("365") / sample_days) / capital
        rows.append(
            {
                "date": cursor.strftime("%Y-%m-%d"),
                "apr": str(annualized),
                "window_pnl_usdc": str(window_pnl),
                "equity_usdc": str(capital),
            }
        )
        if cursor >= last_day:
            break
        drop = cursor - timedelta(days=window_days - 1)
        cursor += timedelta(days=1)
        window_pnl += pnl_by_day.get(cursor, Decimal("0")) - pnl_by_day.get(drop, Decimal("0"))
    return rows


def _rolling_apr_series(
    closed: list[dict[str, Any]],
    *,
    window_days: int,
    effective_capital_usdc: Decimal,
    max_chart_days: int = ROLLING_APR_MAX_CHART_DAYS,
) -> list[dict[str, Any]]:
    pnl_by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for group in closed:
        ts_raw = group.get("closed_timestamp_ms")
        pnl_raw = group.get("realized_pnl")
        if ts_raw is None or pnl_raw is None:
            continue
        day = datetime.fromtimestamp(int(ts_raw) / 1000, tz=UTC).date()
        pnl_by_day[day] += to_decimal(pnl_raw)
    return _rolling_apr_from_daily_totals(
        dict(pnl_by_day),
        window_days=window_days,
        effective_capital_usdc=effective_capital_usdc,
        equity_by_day=None,
        max_chart_days=max_chart_days,
    )


def _cumulative_pnl_series_from_store(accounts: list[DashboardAccount]) -> dict[str, Any]:
    metrics = _ensure_daily_pnl_synced(accounts)
    scope_key = performance_scope_key(accounts)
    daily_by_book, daily_total = metrics.load_daily_by_book(scope_key)
    return _cumulative_pnl_series_from_daily(
        daily_by_book,
        daily_total,
        realized_count=metrics.closed_count(scope_key),
    )


def _rolling_apr_series_from_store(
    accounts: list[DashboardAccount],
    *,
    window_days: int,
    effective_capital_usdc: Decimal,
) -> list[dict[str, Any]]:
    metrics = _ensure_daily_pnl_synced(accounts)
    scope_key = performance_scope_key(accounts)
    pnl_by_day = metrics.load_daily_totals(scope_key)
    equity_by_day = _daily_equity_by_utc_day(accounts)
    return _rolling_apr_from_daily_totals(
        pnl_by_day,
        window_days=window_days,
        effective_capital_usdc=effective_capital_usdc,
        equity_by_day=equity_by_day,
    )


def _resolve_apr_effective_capital_usdc(
    accounts: list[DashboardAccount],
    *,
    override: Decimal | None,
    status_payload: dict[str, Any] | None,
) -> Decimal:
    """Match engine ``_effective_capital``: live equity when available, else reference."""
    if override is not None and override > 0:
        return override
    if status_payload:
        equity = _dec((status_payload.get("portfolio") or {}).get("total_equity_usdc"))
        if equity > 0:
            return equity
    reference = sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
    return reference if reference > 0 else Decimal("0")


# ---------------------------------------------------------------------------
# Multi-account aggregation
# ---------------------------------------------------------------------------


def _bot_for_account(account: DashboardAccount, *, require_private: bool) -> DeribitOptionTrialBot:
    cfg = load_config(account.env_file, require_private=require_private)
    client = DeribitClient(cfg)
    return DeribitOptionTrialBot(cfg, client)


def _exchange_prefetch_for_account(
    account: DashboardAccount,
    *,
    cache: _TtlCache,
) -> ExchangePrefetch | None:
    if not _has_private_creds(account.config):
        return None
    key = _live_api_identity(account)

    def _fetch() -> ExchangePrefetch:
        return _bot_for_account(account, require_private=True).fetch_exchange_prefetch()

    return cache.get_or_set(key, _fetch)


def _prefetch_all_accounts(
    accounts: list[DashboardAccount],
    *,
    cache: _TtlCache,
) -> dict[str, ExchangePrefetch | None]:
    """One prefetch per unique Deribit API identity (shared across strategy rows)."""
    prefetches: dict[str, ExchangePrefetch | None] = {}
    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        key = _live_api_identity(account)
        if key not in prefetches:
            prefetches[key] = _exchange_prefetch_for_account(account, cache=cache)
    return prefetches


def _status_payload_for_account(
    account: DashboardAccount,
    *,
    exchange_prefetch_cache: _TtlCache,
    prefetches: dict[str, ExchangePrefetch | None] | None = None,
) -> dict[str, Any]:
    bot = _bot_for_account(account, require_private=True)
    if prefetches is not None:
        prefetch = prefetches.get(_live_api_identity(account))
    else:
        prefetch = _exchange_prefetch_for_account(account, cache=exchange_prefetch_cache)
    if prefetch is not None:
        return bot.status_with_exchange_prefetch(prefetch)
    return bot.status()


def _tag_row(row: dict[str, Any], account: DashboardAccount) -> dict[str, Any]:
    out = dict(row)
    out["account_name"] = account.name
    out["account_env_file"] = str(account.env_file)
    return out


def _trade_group_row_key(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(row.get("account_name") or ""),
            str(row.get("group_id") or ""),
            str(row.get("short_instrument_name") or ""),
        ]
    )


def _dedupe_trade_group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = _trade_group_row_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _tag_rows(rows: list[dict[str, Any]], account: DashboardAccount) -> list[dict[str, Any]]:
    return [_tag_row(row, account) for row in rows]


def _dec(value: Any) -> Decimal:
    return to_decimal(value)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def _regime_rank(value: str | None) -> int:
    return {"normal": 0, "elevated": 1, "crisis": 2}.get(str(value or "").lower(), -1)


def _worst_regime(values: list[str]) -> str:
    if not values:
        return "normal"
    return max(values, key=_regime_rank)


def _sum_decimal_field(items: list[dict[str, Any]], key: str) -> Decimal:
    return sum((_dec(item.get(key)) for item in items), Decimal("0"))


def _sum_decimal_dict(items: list[dict[str, Any]], key: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for item in items:
        for sub_key, value in (item.get(key) or {}).items():
            out[str(sub_key).upper()] += _dec(value)
    return dict(out)


def _weighted_ratio_by_book(
    portfolios: list[dict[str, Any]],
    *,
    ratio_key: str,
) -> dict[str, Decimal]:
    numerators: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    denominators: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for portfolio in portfolios:
        equity_by_book = portfolio.get("equity_by_book") or {}
        ratio_by_book = portfolio.get("margin_ratios_by_currency") or {}
        for book, ratios in ratio_by_book.items():
            book_key = str(book).upper()
            equity = _dec(equity_by_book.get(book_key))
            ratio = _dec((ratios or {}).get(ratio_key))
            numerators[book_key] += ratio * equity
            denominators[book_key] += equity
    return {
        book: _ratio(numerators[book], denominators[book])
        for book in sorted(set(numerators) | set(denominators))
    }


def _aggregate_portfolios(
    accounts: list[DashboardAccount],
    statuses: list[dict[str, Any]],
    *,
    equity_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_portfolios = [status.get("portfolio") or {} for status in statuses]
    equity_src = equity_statuses if equity_statuses is not None else statuses
    equity_portfolios = [status.get("portfolio") or {} for status in equity_src]
    total_equity = _sum_decimal_field(equity_portfolios, "total_equity_usdc")
    day_start = _sum_decimal_field(equity_portfolios, "day_start_equity_usdc")
    day_net_flow = _sum_decimal_field(equity_portfolios, "day_net_flow_usdc")
    day_pnl = _sum_decimal_field(equity_portfolios, "day_pnl_usdc_ex_flow")
    day_pnl_ex_spot = _sum_decimal_field(equity_portfolios, "day_pnl_usdc_ex_flow_ex_spot")
    open_max_loss = _sum_decimal_field(equity_portfolios, "open_max_loss")
    run_rate = _sum_decimal_field(equity_portfolios, "projected_max_profit_run_rate_usdc")
    reference_capital = sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
    target_apr_num = sum(
        (account.config.target_portfolio_apr * account.config.reference_capital_usdc for account in accounts),
        Decimal("0"),
    )
    target_apr = _ratio(target_apr_num, reference_capital)
    projected_apr = _ratio(run_rate, reference_capital)

    equity_by_book = _sum_decimal_dict(equity_portfolios, "equity_by_book")
    day_start_by_book = _sum_decimal_dict(equity_portfolios, "day_start_equity_by_book")
    day_net_flow_by_book = _sum_decimal_dict(equity_portfolios, "day_net_flow_usdc_by_book")
    day_pnl_by_book = _sum_decimal_dict(equity_portfolios, "day_pnl_usdc_ex_flow_by_book")
    day_pnl_ex_spot_by_book = _sum_decimal_dict(equity_portfolios, "day_pnl_usdc_ex_flow_ex_spot_by_book")
    drawdown_by_book: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for portfolio in equity_portfolios:
        for book, drawdown in (portfolio.get("day_drawdown_pct_by_book") or {}).items():
            key = str(book).upper()
            drawdown_by_book[key] = max(drawdown_by_book[key], _dec(drawdown))
    im_by_book = _weighted_ratio_by_book(equity_portfolios, ratio_key="im_ratio")
    mm_by_book = _weighted_ratio_by_book(equity_portfolios, ratio_key="mm_ratio")
    margin_ratios = {
        book: {
            "im_ratio": im_by_book.get(book, Decimal("0")),
            "mm_ratio": mm_by_book.get(book, Decimal("0")),
        }
        for book in sorted(set(im_by_book) | set(mm_by_book) | set(equity_by_book))
    }

    regime_by_currency: dict[str, str] = {}
    for portfolio in equity_portfolios:
        for book, regime in (portfolio.get("regime_by_currency") or {}).items():
            key = str(book).upper()
            regime_by_currency[key] = _worst_regime([regime_by_currency.get(key, "normal"), str(regime)])

    def _bool_by_book(key: str) -> dict[str, bool]:
        out: dict[str, bool] = defaultdict(bool)
        for portfolio in equity_portfolios:
            for book, value in (portfolio.get(key) or {}).items():
                out[str(book).upper()] = out[str(book).upper()] or bool(value)
        return dict(out)

    halt_reasons: list[str] = []
    halt_reasons_by_book: dict[str, list[str]] = defaultdict(list)
    regime_detail_by_currency: dict[str, list[str]] = defaultdict(list)
    for account, portfolio in zip(accounts, all_portfolios, strict=False):
        for reason in portfolio.get("halt_entry_reasons") or []:
            halt_reasons.append(f"{account.name}: {reason}")
        for book, reasons in (portfolio.get("halt_entry_reasons_by_book") or {}).items():
            halt_reasons_by_book[str(book).upper()].extend(f"{account.name}: {reason}" for reason in reasons)
        for book, details in (portfolio.get("regime_detail_by_currency") or {}).items():
            regime_detail_by_currency[str(book).upper()].extend(f"{account.name}: {detail}" for detail in details)

    return {
        "total_equity_usdc": total_equity,
        "day_start_equity_usdc": day_start,
        "day_net_flow_usdc": day_net_flow,
        "day_pnl_usdc_ex_flow": day_pnl,
        "day_pnl_usdc_ex_flow_ex_spot": day_pnl_ex_spot,
        "day_drawdown_pct": max((_dec(p.get("day_drawdown_pct")) for p in equity_portfolios), default=Decimal("0")),
        "open_max_loss": open_max_loss,
        "open_max_loss_pct": _ratio(open_max_loss, total_equity),
        "initial_margin_ratio": _ratio(
            sum((_dec(p.get("initial_margin_ratio")) * _dec(p.get("total_equity_usdc")) for p in equity_portfolios), Decimal("0")),
            total_equity,
        ),
        "maintenance_margin_ratio": _ratio(
            sum((_dec(p.get("maintenance_margin_ratio")) * _dec(p.get("total_equity_usdc")) for p in equity_portfolios), Decimal("0")),
            total_equity,
        ),
        "projected_max_profit_run_rate_usdc": run_rate,
        "projected_max_profit_apr": projected_apr,
        "target_progress_ratio": _ratio(projected_apr, target_apr),
        "regime": _worst_regime([str(p.get("regime") or "normal") for p in equity_portfolios]),
        "halt_new_entries": any(bool(p.get("halt_new_entries")) for p in equity_portfolios),
        "hard_derisk": any(bool(p.get("hard_derisk")) for p in equity_portfolios),
        "cooldown_until_ms": max((int(p.get("cooldown_until_ms") or 0) for p in equity_portfolios), default=0) or None,
        "cooling_down": any(bool(p.get("cooling_down")) for p in equity_portfolios),
        "delta_totals_by_currency": _sum_decimal_dict(equity_portfolios, "delta_totals_by_currency"),
        "regime_by_currency": regime_by_currency,
        "halt_entry_reasons": halt_reasons,
        "regime_detail_by_currency": dict(regime_detail_by_currency),
        "margin_ratios_by_currency": margin_ratios,
        "equity_by_book": equity_by_book,
        "day_start_equity_by_book": day_start_by_book,
        "day_net_flow_usdc_by_book": day_net_flow_by_book,
        "day_pnl_usdc_ex_flow_by_book": day_pnl_by_book,
        "day_pnl_usdc_ex_flow_ex_spot_by_book": day_pnl_ex_spot_by_book,
        "day_drawdown_pct_by_book": dict(drawdown_by_book),
        "cooldown_until_ms_by_book": {
            book: max(
                (int((p.get("cooldown_until_ms_by_book") or {}).get(book) or 0) for p in equity_portfolios),
                default=0,
            ) or None
            for book in sorted(equity_by_book)
        },
        "cooling_down_by_book": _bool_by_book("cooling_down_by_book"),
        "hard_derisk_by_book": _bool_by_book("hard_derisk_by_book"),
        "halt_entries_by_book": _bool_by_book("halt_entries_by_book"),
        "halt_entry_reasons_by_book": dict(halt_reasons_by_book),
    }


def _aggregate_status(
    accounts: list[DashboardAccount],
    *,
    exchange_prefetch_cache: _TtlCache,
) -> dict[str, Any]:
    prefetches = _prefetch_all_accounts(accounts, cache=exchange_prefetch_cache)
    work = [account for account in accounts if _has_private_creds(account.config)]

    def _fetch(account: DashboardAccount) -> tuple[DashboardAccount, dict[str, Any]]:
        return account, _status_payload_for_account(
            account,
            exchange_prefetch_cache=exchange_prefetch_cache,
            prefetches=prefetches,
        )

    if len(work) <= 1:
        pairs = [_fetch(account) for account in work]
    else:
        with ThreadPoolExecutor(max_workers=min(len(work), 4)) as pool:
            pairs = list(pool.map(_fetch, work))

    payload_by_name = {account.name: payload for account, payload in pairs}
    statuses: list[dict[str, Any]] = []
    trade_groups: list[dict[str, Any]] = []
    open_orders: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    account_summaries: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    underlying_index_usd: dict[str, str] = {}
    seen_balance_identity: set[str] = set()

    for account in accounts:
        payload = payload_by_name.get(account.name)
        if payload is None:
            continue
        statuses.append(payload)
        for key, value in (payload.get("underlying_index_usd") or {}).items():
            if _dec(value) > 0:
                underlying_index_usd[str(key).upper()] = str(value)
        trade_groups.extend(_tag_rows(payload.get("trade_groups") or [], account))
        open_orders.extend(_tag_rows(payload.get("open_orders") or [], account))
        positions.extend(_tag_rows(payload.get("positions") or [], account))
        balance_key = _live_api_identity(account)
        if balance_key not in seen_balance_identity:
            seen_balance_identity.add(balance_key)
            for book, row in (payload.get("accounts") or {}).items():
                book_key = str(book).upper()
                for field, value in row.items():
                    account_summaries[book_key][field] += _dec(value)

    active_accounts = [account for account in accounts if account.name in payload_by_name]
    equity_statuses = _dedupe_statuses_for_equity_aggregate(active_accounts, statuses)
    return {
        "env": "multi" if len(accounts) > 1 else accounts[0].config.env,
        "portfolio": _aggregate_portfolios(active_accounts, statuses, equity_statuses=equity_statuses),
        "underlying_index_usd": underlying_index_usd,
        "accounts": {book: dict(values) for book, values in sorted(account_summaries.items())},
        "trade_group_count": len(trade_groups),
        "trade_groups": trade_groups,
        "open_orders": open_orders,
        "positions": positions,
        "dashboard_accounts": [
            {
                "name": account.name,
                "env": account.config.env,
                "option_strategy": account.config.option_strategy,
                "state_file": str(account.state_path),
            }
            for account in accounts
        ],
        "account_statuses": [
            {
                "name": account.name,
                "env": payload.get("env"),
                "option_strategy": account.config.option_strategy,
                "portfolio": payload.get("portfolio"),
                "accounts": payload.get("accounts") or {},
                "trade_group_count": payload.get("trade_group_count"),
            }
            for account in accounts
            if (payload := payload_by_name.get(account.name)) is not None
        ],
    }


def _closed_groups_cache_key(accounts: list[DashboardAccount]) -> tuple[Any, ...]:
    parts: list[Any] = []
    for account in accounts:
        path = account.state_path
        try:
            mtime = path.stat().st_mtime if path.is_file() else 0.0
        except OSError:
            mtime = 0.0
        journal_path = journal_db_path_for_state(path)
        try:
            journal_mtime = journal_path.stat().st_mtime if journal_path.is_file() else 0.0
        except OSError:
            journal_mtime = 0.0
        parts.append((account.name, str(path), mtime, str(journal_path), journal_mtime))
    return tuple(parts)


def invalidate_closed_groups_payload_cache() -> None:
    with _closed_groups_payload_cache_lock:
        _closed_groups_payload_cache.clear()


def _aggregate_realized_summary(
    accounts: list[DashboardAccount],
    *,
    days: int = 30,
    spot_index: dict[str, Decimal] | None = None,
    status_payload: dict[str, Any] | None = None,
    effective_capital_override: Decimal | None = None,
) -> dict[str, Any]:
    """Lightweight report summary from on-disk closed groups (no per-account bot.report())."""
    closed: list[dict[str, Any]] = []
    excluded = 0
    open_count = 0
    for account in accounts:
        payload = _closed_groups_payload(account.state_path, spot_index=spot_index)
        closed.extend(_tag_rows(payload.get("closed") or [], account))
        excluded += int(payload.get("performance_excluded_closed_group_count") or 0)
        open_count += len(payload.get("open") or [])
    closed = _dedupe_trade_group_rows(closed)
    reference_capital = sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
    equity_by_day = _daily_equity_by_utc_day(accounts)
    fallback_capital = _resolve_apr_effective_capital_usdc(
        accounts,
        override=effective_capital_override,
        status_payload=status_payload,
    )
    today = datetime.now(tz=UTC).date()
    capital = _equity_on_day(equity_by_day, today, fallback_capital)
    target_num = sum(
        (account.config.target_portfolio_apr * account.config.reference_capital_usdc for account in accounts),
        Decimal("0"),
    )
    target_apr = _ratio(target_num, reference_capital)
    summary = realized_summary_from_closed(
        closed,
        effective_capital_usdc=capital,
        target_portfolio_apr=target_apr,
        window_days=days,
    )
    summary["open_group_count"] = str(open_count)
    summary["performance_excluded_closed_group_count"] = str(excluded)
    recent_closed = [row for row in closed if row.get("realized_pnl") is not None]
    recent_closed.sort(key=lambda row: int(row.get("closed_timestamp_ms") or 0), reverse=True)
    return {
        "action": "report",
        "generated_at": utc_now(),
        "note": "Summary from local state; trade journal stores API fills incrementally.",
        "summary": summary,
        "recent_closed_trades": recent_closed[:20],
        "open_trades": [],
    }


def _aggregate_closed_groups(accounts: list[DashboardAccount]) -> list[dict[str, Any]]:
    """Closed trade groups from on-disk state only (no Deribit enrichment)."""
    merged_closed: list[dict[str, Any]] = []
    for account in accounts:
        payload = _closed_groups_payload(account.state_path)
        merged_closed.extend(_tag_rows(payload.get("closed") or [], account))
    return _dedupe_trade_group_rows(merged_closed)


def _groups_payload_for_account(
    account: DashboardAccount,
    *,
    prefetches: dict[str, ExchangePrefetch | None],
    spot_index: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(_closed_groups_payload(account.state_path, spot_index=spot_index))
    if _has_private_creds(account.config):
        try:
            bot = _bot_for_account(account, require_private=True)
            prefetch = prefetches.get(_live_api_identity(account))
            _enrich_groups_payload_open_unrealized(
                bot,
                payload,
                exchange_prefetch=prefetch,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("groups enrich skipped for %s: %s", account.name, exc)
    return payload


def _aggregate_groups(
    accounts: list[DashboardAccount],
    *,
    exchange_prefetch_cache: _TtlCache,
    spot_index: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    merged_open: list[dict[str, Any]] = []
    merged_closed: list[dict[str, Any]] = []
    underlying_index_usd: dict[str, str] = {}
    next_group_id: dict[str, Any] = {}
    excluded_closed_count = 0

    prefetches = _prefetch_all_accounts(accounts, cache=exchange_prefetch_cache)

    def _fetch(account: DashboardAccount) -> tuple[DashboardAccount, dict[str, Any]]:
        return account, _groups_payload_for_account(
            account,
            prefetches=prefetches,
            spot_index=spot_index,
        )

    if len(accounts) <= 1:
        pairs = [_fetch(account) for account in accounts]
    else:
        with ThreadPoolExecutor(max_workers=min(len(accounts), 4)) as pool:
            pairs = list(pool.map(_fetch, accounts))

    for account, payload in pairs:
        for key, value in (payload.get("underlying_index_usd") or {}).items():
            if _dec(value) > 0:
                underlying_index_usd[str(key).upper()] = str(value)
        merged_open.extend(_tag_rows(payload.get("open") or [], account))
        merged_closed.extend(_tag_rows(payload.get("closed") or [], account))
        next_group_id[account.name] = payload.get("next_group_id")
        excluded_closed_count += int(payload.get("performance_excluded_closed_group_count") or 0)

    merged_open = _dedupe_trade_group_rows(merged_open)
    merged_closed = _dedupe_trade_group_rows(merged_closed)

    return {
        "open": merged_open,
        "closed": merged_closed,
        "performance_excluded_closed_group_count": excluded_closed_count,
        "next_group_id": next_group_id if len(accounts) > 1 else (next(iter(next_group_id.values()), None)),
        "underlying_index_usd": underlying_index_usd,
        "accounts": [{"name": account.name, "state_file": str(account.state_path)} for account in accounts],
    }


def _aggregate_report(accounts: list[DashboardAccount], *, days: int) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    recent_closed: list[dict[str, Any]] = []
    open_trades: list[dict[str, Any]] = []
    for account in accounts:
        payload = _bot_for_account(account, require_private=True).report(days=days)
        reports.append(payload)
        recent_closed.extend(_tag_rows(payload.get("recent_closed_trades") or [], account))
        open_trades.extend(_tag_rows(payload.get("open_trades") or [], account))

    summaries = [report.get("summary") or {} for report in reports]
    effective_capital = _sum_decimal_field(summaries, "effective_capital_usdc")
    realized_count = sum(int(summary.get("realized_closed_group_count") or 0) for summary in summaries)
    closed_count = sum(int(summary.get("closed_group_count") or 0) for summary in summaries)
    excluded_count = sum(int(summary.get("performance_excluded_closed_group_count") or 0) for summary in summaries)
    unresolved_count = sum(int(summary.get("unresolved_closed_group_count") or 0) for summary in summaries)
    total_realized = _sum_decimal_field(summaries, "realized_pnl_usdc")
    window_realized = _sum_decimal_field(summaries, "window_realized_pnl_usdc")
    total_holding_days = sum(
        (_dec(summary.get("avg_holding_days")) * Decimal(str(int(summary.get("realized_closed_group_count") or 0))) for summary in summaries),
        Decimal("0"),
    )
    wins = sum(1 for row in recent_closed if _dec(row.get("realized_pnl")) > 0)
    lifetime_days = max((_dec(summary.get("lifetime_sample_days")) for summary in summaries), default=Decimal("0"))
    window_days_used = max((_dec(summary.get("window_days_used")) for summary in summaries), default=Decimal(str(days)))
    target_num = sum(
        (_dec(summary.get("target_portfolio_apr")) * _dec(summary.get("effective_capital_usdc")) for summary in summaries),
        Decimal("0"),
    )

    recent_closed = _dedupe_trade_group_rows(recent_closed)
    recent_closed.sort(key=lambda row: int(row.get("closed_timestamp_ms") or 0), reverse=True)
    return {
        "action": "report",
        "generated_at": utc_now(),
        "note": "Aggregated multi-account realized report. Perpetual hedge PnL is not included.",
        "summary": {
            "effective_capital_usdc": effective_capital,
            "target_portfolio_apr": _ratio(target_num, effective_capital),
            "open_group_count": sum(int(summary.get("open_group_count") or 0) for summary in summaries),
            "closed_group_count": closed_count,
            "performance_excluded_closed_group_count": excluded_count,
            "realized_closed_group_count": realized_count,
            "unresolved_closed_group_count": unresolved_count,
            "open_max_loss_usdc": _sum_decimal_field(summaries, "open_max_loss_usdc"),
            "realized_pnl_usdc": total_realized,
            "avg_realized_pnl_usdc": _ratio(total_realized, Decimal(str(realized_count))),
            "realized_win_rate": _ratio(Decimal(str(wins)), Decimal(str(realized_count))),
            "avg_holding_days": _ratio(total_holding_days, Decimal(str(realized_count))),
            "lifetime_sample_days": lifetime_days,
            "lifetime_realized_apr": _ratio(total_realized * Decimal("365"), lifetime_days * effective_capital),
            "window_days_requested": days,
            "window_days_used": window_days_used,
            "window_realized_closed_group_count": sum(int(summary.get("window_realized_closed_group_count") or 0) for summary in summaries),
            "window_realized_pnl_usdc": window_realized,
            "window_realized_apr": _ratio(window_realized * Decimal("365"), window_days_used * effective_capital),
        },
        "recent_closed_trades": recent_closed[:20],
        "open_trades": open_trades,
        "accounts": [
            {
                "name": account.name,
                "option_strategy": account.config.option_strategy,
                "summary": report.get("summary") or {},
            }
            for account, report in zip(accounts, reports, strict=False)
        ],
    }


def _new_stress_bucket(option_strategy: str, *, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    option_strategy = normalize_strategy_name(option_strategy, default=option_strategy)
    return {
        "option_strategy": option_strategy,
        "strategy_analysis": analysis or black_swan_strategy_analysis(option_strategy),
        "index_by_ccy": {},
        "equity_usdc_by_book": defaultdict(lambda: Decimal("0")),
        "positions": [],
        "scenarios_by_key": {},
        "notes": [],
        "accounts": [],
    }


def _add_stress_result(bucket: dict[str, Any], account: DashboardAccount, result: Any) -> None:
    bucket["accounts"].append(
        {
            "name": account.name,
            "env": account.config.env,
            "option_strategy": account.config.option_strategy,
        }
    )
    for ccy, value in result.index_by_ccy.items():
        key = str(ccy).upper()
        if _dec(value) > 0:
            bucket["index_by_ccy"][key] = value
    for book, value in result.equity_usdc_by_book.items():
        bucket["equity_usdc_by_book"][str(book).upper()] += value
    bucket["positions"].extend(_tag_rows(_decimalize(result.positions), account))
    bucket["notes"].extend(f"{account.name}: {note}" for note in result.notes)

    scenario_by_key = bucket["scenarios_by_key"]
    for scenario in _decimalize(result.scenarios):
        key = (str(scenario.get("shock")), str(scenario.get("slippage")))
        scenario_bucket = scenario_by_key.setdefault(
            key,
            {
                "shock": scenario.get("shock"),
                "slippage": scenario.get("slippage"),
                "loss_usdc_total": Decimal("0"),
                "loss_by_book_usdc": defaultdict(lambda: Decimal("0")),
                "components_total_usdc": defaultdict(lambda: Decimal("0")),
                "worst_legs": [],
            },
        )
        scenario_bucket["loss_usdc_total"] += _dec(scenario.get("loss_usdc_total"))
        for book, value in (scenario.get("loss_by_book_usdc") or {}).items():
            scenario_bucket["loss_by_book_usdc"][str(book).upper()] += _dec(value)
        for component, value in (scenario.get("components_total_usdc") or {}).items():
            scenario_bucket["components_total_usdc"][str(component)] += _dec(value)
        scenario_bucket["worst_legs"].extend(_tag_rows(scenario.get("worst_legs") or [], account))


def _finalize_stress_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    equity = dict(bucket["equity_usdc_by_book"])
    total_equity = sum(equity.values(), Decimal("0"))
    scenarios: list[dict[str, Any]] = []
    for scenario_bucket in bucket["scenarios_by_key"].values():
        total_loss = scenario_bucket["loss_usdc_total"]
        worst_legs = sorted(scenario_bucket["worst_legs"], key=lambda row: _dec(row.get("loss_usdc")))[:5]
        scenarios.append(
            {
                "shock": scenario_bucket["shock"],
                "slippage": scenario_bucket["slippage"],
                "loss_usdc_total": total_loss,
                "loss_usdc_pct_of_total_equity": _ratio(total_loss, total_equity),
                "loss_by_book_usdc": dict(scenario_bucket["loss_by_book_usdc"]),
                "components_total_usdc": dict(scenario_bucket["components_total_usdc"]),
                "worst_legs": worst_legs,
            }
        )
    scenarios.sort(key=lambda row: (_dec(row.get("shock")), _dec(row.get("slippage"))))
    return {
        "generated_at": utc_now(),
        "option_strategy": bucket["option_strategy"],
        "strategy_analysis": _decimalize(bucket["strategy_analysis"]),
        "index_by_ccy": dict(bucket["index_by_ccy"]),
        "equity_usdc_by_book": equity,
        "positions": bucket["positions"],
        "scenarios": scenarios,
        "notes": bucket["notes"],
        "accounts": bucket["accounts"],
    }


def _aggregate_stress(accounts: list[DashboardAccount], *, shocks: list[Decimal]) -> dict[str, Any]:
    aggregate = _new_stress_bucket(
        "multi_account",
        analysis={
            "label": "multi_account",
            "summary": "Aggregated stress across the configured strategy sub-accounts.",
            "focus": "Use the per-strategy cards below to compare naked put, put spread, and covered call tail exposure.",
        },
    )
    strategy_buckets: dict[str, dict[str, Any]] = {}
    aggregate_identity: set[str] = set()

    for account in accounts:
        cfg = load_config(account.env_file, require_private=True)
        result = compute_current_stress(cfg, DeribitClient(cfg), shocks=shocks)
        strategy = normalize_strategy_name(result.option_strategy or cfg.option_strategy)
        strategy_bucket = strategy_buckets.setdefault(strategy, _new_stress_bucket(strategy, analysis=result.strategy_analysis))
        ident = _live_api_identity_config(cfg, account.name)
        if ident not in aggregate_identity:
            aggregate_identity.add(ident)
            _add_stress_result(aggregate, account, result)
        _add_stress_result(strategy_bucket, account, result)

    payload = _finalize_stress_bucket(aggregate)
    order_rank = {name: index for index, name in enumerate(STRATEGY_DISPLAY_ORDER)}
    ordered_buckets = sorted(
        strategy_buckets.values(),
        key=lambda bucket: order_rank.get(
            normalize_strategy_name(bucket.get("option_strategy") or ""),
            len(STRATEGY_DISPLAY_ORDER),
        ),
    )
    payload["strategy_stresses"] = [_finalize_stress_bucket(bucket) for bucket in ordered_buckets]
    return payload


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    env_file: str | Path = ".env",
    account_env_files: tuple[str | Path, ...] | None = None,
    enable_scheduler: bool = True,
    snapshot_interval_sec: int | None = None,
    investor_portal: bool = False,
    skipped_accounts: tuple[dict[str, str], ...] | None = None,
) -> "Any":
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
    except ImportError as exc:  # pragma: no cover — surfaces a clear hint.
        raise RuntimeError(
            "fastapi/uvicorn not installed; run `pip install -r requirements.txt`"
        ) from exc

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
    status_cache = _TtlCache(status_ttl)
    report_cache = _TtlCache(REPORT_CACHE_TTL_SEC)
    groups_cache = _TtlCache(GROUPS_CACHE_TTL_SEC)
    bundle_cache = _TtlCache(status_ttl)
    exchange_prefetch_cache = _TtlCache(status_ttl)
    spot_cache = _TtlCache(SPOT_CACHE_TTL_SEC)
    stress_cache = _TtlCache(STATUS_CACHE_TTL_SEC)
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

    @asynccontextmanager
    async def _lifespan(_app: "FastAPI"):
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

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def _fetch_spot() -> dict[str, Any]:
        client = DeribitClient(config_public)
        btc_raw = client.get_index_price("btc_usd")
        eth_raw = client.get_index_price("eth_usd")
        btc_px = to_decimal(btc_raw.get("index_price") or 0)
        eth_px = to_decimal(eth_raw.get("index_price") or 0)
        return {
            "BTC": str(btc_px) if btc_px > 0 else None,
            "ETH": str(eth_px) if eth_px > 0 else None,
        }

    @app.get("/api/spot")
    def api_spot() -> dict[str, Any]:
        """Public BTC/ETH USD index for dashboard header (no private auth)."""
        try:
            return spot_cache.get_or_set("spot", _fetch_spot)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"spot failed: {exc}") from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        any_have_creds = any(_has_private_creds(account.config) for account in accounts)
        any_scheduler_running = any(scheduler.state.running for scheduler in background_schedulers)
        last_attempts = [
            s.state.last_attempt_ms for s in equity_schedulers if s.state.last_attempt_ms is not None
        ]
        last_successes = [
            s.state.last_success_ms for s in equity_schedulers if s.state.last_success_ms is not None
        ]
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
            "reference_capital_usdc": str(sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))),
            "target_portfolio_apr": str(
                _ratio(
                    sum(
                        (account.config.target_portfolio_apr * account.config.reference_capital_usdc for account in accounts),
                        Decimal("0"),
                    ),
                    sum((account.config.reference_capital_usdc for account in accounts), Decimal("0")),
                )
            ),
            "halt_open_max_loss_pct": str(config_public.halt_open_max_loss_pct),
            "multi_account": multi_account,
            "accounts": [
                {
                    "name": account.name,
                    "env": account.config.env,
                    "option_strategy": account.config.option_strategy,
                    "state_file": str(account.state_path),
                    "ledger_dir": str(account.ledger_root),
                    "has_private_creds": _has_private_creds(account.config),
                }
                for account in accounts
            ],
            "server_time_ms": utc_now_ms(),
        }

    @app.get("/api/portfolio/snapshot")
    def api_portfolio_snapshot() -> Any:
        """Last on-disk equity snapshot (no Deribit); for fast investor first paint."""
        payload = _latest_ledger_snapshot(
            accounts,
            scheduler_states=[s.state for s in equity_schedulers],
            snapshot_interval_sec=interval,
        )
        if payload.get("source") == "none":
            return JSONResponse(_decimalize(payload), status_code=200)
        return JSONResponse(_decimalize(payload))

    def _locked_aggregate_status() -> dict[str, Any]:
        with _heavy_portfolio_lock:
            return _aggregate_status(accounts, exchange_prefetch_cache=exchange_prefetch_cache)

    def _locked_aggregate_report(d: int) -> dict[str, Any]:
        with _heavy_portfolio_lock:
            return _aggregate_report(accounts, days=d)

    def _locked_compute_dashboard_bundle(
        *,
        days: int,
        override: Decimal | None,
    ) -> dict[str, Any]:
        with _heavy_portfolio_lock:
            status = _aggregate_status(accounts, exchange_prefetch_cache=exchange_prefetch_cache)
            groups = _aggregate_groups(accounts, exchange_prefetch_cache=exchange_prefetch_cache)
            summary = _aggregate_realized_summary(
                accounts,
                days=days,
                status_payload=status,
                effective_capital_override=override,
            )
        return {
            "status": status,
            "groups": groups,
            "realized_summary": summary,
        }

    def _seed_bundle_component_caches(
        *,
        status: dict[str, Any],
        groups: dict[str, Any],
        summary: dict[str, Any],
        days: int,
        override: Decimal | None,
    ) -> None:
        status_cache.seed("status", status)
        groups_cache.seed(("groups", _closed_groups_cache_key(accounts)), groups)
        capital = _resolve_apr_effective_capital_usdc(
            accounts,
            override=override,
            status_payload=status,
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

    def _finalize_dashboard_bundle(payload: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(payload)
        try:
            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
            _apply_spot_native_backfill(out.get("groups") or {}, spot_idx)
            for row in (out.get("realized_summary") or {}).get("recent_closed_trades") or []:
                if isinstance(row, dict):
                    _backfill_row_collateral_native(row, spot_idx)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("dashboard bundle spot native backfill skipped: %s", exc)
        return out

    @app.get("/api/dashboard_bundle")
    def api_dashboard_bundle(
        days: int = Query(default=30, ge=0, le=3650),
        effective_capital_usdc: float | None = Query(default=None, ge=0),
    ) -> Any:
        """Status + groups + realized summary in one Deribit prefetch pass."""
        if not any(_has_private_creds(account.config) for account in accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        override = (
            Decimal(str(effective_capital_usdc))
            if effective_capital_usdc is not None and effective_capital_usdc > 0
            else None
        )
        cache_key = (
            "dashboard_bundle",
            days,
            str(override) if override is not None else "",
            _ledger_equity_cache_key(accounts),
            _closed_groups_cache_key(accounts),
        )

        def _compute() -> dict[str, Any]:
            payload = _locked_compute_dashboard_bundle(days=days, override=override)
            _seed_bundle_component_caches(
                status=payload["status"],
                groups=payload["groups"],
                summary=payload["realized_summary"],
                days=days,
                override=override,
            )
            return payload

        try:
            payload = copy.deepcopy(bundle_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/dashboard_bundle failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"dashboard bundle failed: {exc}") from exc
        headers: dict[str, str] = {}
        age_ms = bundle_cache.cache_age_ms(cache_key)
        if age_ms is not None:
            headers["X-Cache-Age-Ms"] = str(age_ms)
        return JSONResponse(_decimalize(_finalize_dashboard_bundle(payload)), headers=headers)

    @app.get("/api/status")
    def api_status() -> Any:
        if not any(_has_private_creds(account.config) for account in accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            payload = status_cache.get_or_set("status", _locked_aggregate_status)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/status aggregate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"status failed: {exc}") from exc
        headers: dict[str, str] = {}
        age_ms = status_cache.cache_age_ms("status")
        if age_ms is not None:
            headers["X-Cache-Age-Ms"] = str(age_ms)
        return JSONResponse(_decimalize(payload), headers=headers)

    @app.get("/api/report")
    def api_report(days: int = Query(default=30, ge=0, le=3650)) -> Any:
        if not any(_has_private_creds(account.config) for account in accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            payload = report_cache.get_or_set(("report", days), lambda: _locked_aggregate_report(days))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/report aggregate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"report failed: {exc}") from exc
        return JSONResponse(_decimalize(payload))

    @app.get("/api/stress")
    def api_stress(shocks: str = Query(default="0.10,0.20,0.30,0.40,0.50")) -> Any:
        if not any(_has_private_creds(account.config) for account in accounts):
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
            if multi_account:
                return _aggregate_stress(accounts, shocks=shock_decimals)
            cfg = load_config(accounts[0].env_file, require_private=True)
            client = DeribitClient(cfg)
            result = compute_current_stress(cfg, client, shocks=shock_decimals)
            return {
                "generated_at": result.generated_at,
                "option_strategy": result.option_strategy,
                "strategy_analysis": _decimalize(result.strategy_analysis),
                "index_by_ccy": {k: str(v) for k, v in result.index_by_ccy.items()},
                "equity_usdc_by_book": {k: str(v) for k, v in result.equity_usdc_by_book.items()},
                "positions": _decimalize(result.positions),
                "scenarios": _decimalize(result.scenarios),
                "notes": list(result.notes),
            }

        try:
            payload = stress_cache.get_or_set(("stress", shocks), _compute)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"stress failed: {exc}") from exc
        return JSONResponse(_decimalize(payload))

    @app.get("/api/groups")
    def api_groups() -> Any:
        cache_key = ("groups", _closed_groups_cache_key(accounts))

        def _compute() -> dict[str, Any]:
            return _aggregate_groups(accounts, exchange_prefetch_cache=exchange_prefetch_cache)

        try:
            payload = copy.deepcopy(groups_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"groups failed: {exc}") from exc
        try:
            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
            _apply_spot_native_backfill(payload, spot_idx)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("groups spot native backfill skipped: %s", exc)
        return JSONResponse(_decimalize(payload))

    @app.get("/api/equity_series")
    def api_equity_series(days: int = Query(default=30, ge=1, le=3650)) -> Any:
        since_ms = utc_now_ms() - days * 86400 * 1000
        rows = []
        for account in accounts:
            rows.extend(_read_ledger(account.ledger_root, since_ms=since_ms))
        rows.sort(key=lambda row: int(row.get("ts_ms") or 0))
        return JSONResponse({
            "days_requested": days,
            "row_count": len(rows),
            "rows": rows,
        })

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
            return _aggregate_realized_summary(
                accounts,
                days=days,
                status_payload=status_payload,
                effective_capital_override=override,
            )

        try:
            payload = copy.deepcopy(series_cache.get_or_set(cache_key, _compute))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"realized summary failed: {exc}") from exc
        try:
            spot_idx = _spot_index_decimals(spot_cache.get_or_set("spot", _fetch_spot))
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
        return JSONResponse({
            "since_days": since_days,
            "row_count": len(rows[:limit]),
            "rows": rows[:limit],
        })

    @app.get("/api/cumulative_pnl_series")
    def api_cumulative_pnl_series() -> Any:
        cache_key = ("cumulative_pnl", _closed_groups_cache_key(accounts))

        def _compute() -> dict[str, Any]:
            return _cumulative_pnl_series_from_store(accounts)

        try:
            series = series_cache.get_or_set(cache_key, _compute)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"cumulative pnl failed: {exc}") from exc
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
            raise HTTPException(status_code=500, detail=f"apr series failed: {exc}") from exc
        return JSONResponse(payload)

    # ------------------------------------------------------------------
    # Static frontend
    # ------------------------------------------------------------------

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

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

        for _html_name in ("index.html", "investor.html", "investor.zh.html"):
            _html_path = frontend_dir / _html_name

            def _make_html_handler(path: Path) -> Any:
                def _html_handler() -> Any:
                    if not path.is_file():
                        raise HTTPException(status_code=404, detail=f"{path.name} not found")
                    return FileResponse(
                        path,
                        media_type="text/html",
                        headers={"Cache-Control": "no-cache, must-revalidate"},
                    )

                return _html_handler

            app.add_api_route(
                f"/{_html_name}",
                _make_html_handler(_html_path),
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
        raise RuntimeError(
            "uvicorn not installed; run `pip install -r requirements.txt`"
        ) from exc

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


__all__ = ["create_app", "serve"]
