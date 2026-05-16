"""Local dashboard backend.

Exposes a small FastAPI app that re-uses :class:`DeribitOptionTrialBot` and
``compute_current_stress`` to surface the same data as the CLI but as JSON
endpoints, plus a background scheduler that periodically appends an
equity snapshot to ``data/frontend_ledger/equity_<UTC date>.jsonl``.

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .client import DeribitClient
from .config import BotConfig, load_config
from .env_layout import account_slug_from_env_path
from .current_stress import compute_current_stress
from .engine import DeribitOptionTrialBot
from .exceptions import ConfigurationError
from .models import OrderBookSnapshot, normalize_strategy_name
from .stress import black_swan_strategy_analysis
from .state import StrategyStateStore, load_performance_exclusion_group_ids
from .fees import annualized_return
from .utils import format_decimal, json_default, to_decimal, utc_now, utc_now_ms

LOGGER = logging.getLogger(__name__)

LEDGER_DIR = Path("data/frontend_ledger")
DEFAULT_SNAPSHOT_INTERVAL_SEC = 300
STATUS_CACHE_TTL_SEC = 15
REPORT_CACHE_TTL_SEC = 15
STRATEGY_DISPLAY_ORDER = ("covered_call", "naked_short", "bull_put_spread")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decimalize(value: Any) -> Any:
    """Recursively convert Decimal / datetime payloads to JSON-friendly forms."""
    return json.loads(json.dumps(value, default=json_default, ensure_ascii=False))


def _has_private_creds(config: BotConfig) -> bool:
    return bool(config.client_id and config.client_secret)


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

    ledger_base = Path(os.environ.get("FRONTEND_LEDGER_DIR", str(LEDGER_DIR)))
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


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


class _TtlCache:
    """Trivial single-key TTL cache — just enough to avoid hammering Deribit."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[Any, tuple[float, Any]] = {}

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
        value = factory()
        with self._lock:
            self._store[key] = (time.monotonic(), value)
        return value


# ---------------------------------------------------------------------------
# Domain helpers (closed-group → time series)
# ---------------------------------------------------------------------------


def _closed_groups_payload(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"open": [], "closed": []}
    store = StrategyStateStore(state_path)
    state = store.load()
    excluded_group_ids = load_performance_exclusion_group_ids(state_path)
    open_groups = [g.to_dict() for g in state.groups if g.status != "closed"]
    all_closed_groups = [g for g in state.groups if g.status == "closed"]
    closed_groups = [g.to_dict() for g in all_closed_groups if g.group_id not in excluded_group_ids]
    return {
        "open": _decimalize(open_groups),
        "closed": _decimalize(closed_groups),
        "performance_excluded_closed_group_count": len(all_closed_groups) - len(closed_groups),
        "next_group_id": state.next_group_id,
    }


def _entry_dte_days_at_open(group: dict[str, Any]) -> Decimal:
    entry_ms = int(group.get("entry_timestamp_ms") or 0)
    exp_ms = int(group.get("expiration_timestamp_ms") or 0)
    if entry_ms <= 0 or exp_ms <= entry_ms:
        return Decimal("0")
    return Decimal(str(exp_ms - entry_ms)) / Decimal("86400000")


def _ensure_entry_net_apr(
    group: dict[str, Any],
    *,
    equity_by_book: dict[str, Decimal],
    index_usd: dict[str, Decimal],
) -> None:
    if _dec(group.get("entry_net_apr")) > 0:
        return
    credit_usdc = _dec(group.get("entry_credit"))
    dte = _entry_dte_days_at_open(group)
    coll = str(group.get("collateral_currency") or group.get("currency") or "USDC").upper()
    equity = equity_by_book.get(coll) or Decimal("0")
    if credit_usdc <= 0 or dte <= 0 or equity <= 0:
        group["entry_net_apr"] = format_decimal(Decimal("0"), 8)
        return
    if coll == "USDC":
        net_credit = credit_usdc
        capital = equity
    else:
        idx = index_usd.get(coll) or Decimal("0")
        if idx <= 0:
            group["entry_net_apr"] = format_decimal(Decimal("0"), 8)
            return
        net_credit = credit_usdc / idx
        capital = equity
    apr = annualized_return(net_credit=net_credit, capital_base=capital, dte_days=dte)
    group["entry_net_apr"] = format_decimal(apr, 8)


def _enrich_groups_payload_open_unrealized(bot: DeribitOptionTrialBot, payload: dict[str, Any]) -> None:
    """Mirror engine open-row fields so the UI works from ``/api/groups`` alone.

    Persisted state rows omit ``unrealized_*`` and index data; without this,
    BTC/ETH collateral rows can show USD unrealized while native stays ``—``.
    """
    open_rows = payload.get("open") or []
    if not open_rows:
        return
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
    equity_by_book: dict[str, Decimal] = {}
    try:
        for ccy, summary in bot._account_summaries_by_currency().items():
            book = str(ccy).upper()
            eq = to_decimal(summary.equity)
            if eq > 0:
                equity_by_book[book] = eq
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("entry_net_apr equity lookup skipped: %s", exc)
    for g in open_rows:
        _ensure_entry_net_apr(g, equity_by_book=equity_by_book, index_usd=index_usd)
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


def _bucket_day_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


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

    return {
        "books": books_sorted,
        "cumulative_total": cumulative_total,
        "cumulative_by_book": cumulative_by_book,
        "daily_total": daily_total_rows,
        "daily_by_book": daily_by_book_rows,
        "realized_count": len(realized),
    }


def _rolling_apr_series(
    closed: list[dict[str, Any]],
    *,
    window_days: int,
    effective_capital_usdc: Decimal,
) -> list[dict[str, Any]]:
    """Rolling annualized APR sampled per UTC day."""
    if effective_capital_usdc <= 0:
        return []
    realized = sorted(
        [
            g for g in closed
            if g.get("closed_timestamp_ms") is not None and g.get("realized_pnl") is not None
        ],
        key=lambda g: int(g["closed_timestamp_ms"]),
    )
    if not realized:
        return []
    first_day = datetime.fromtimestamp(int(realized[0]["closed_timestamp_ms"]) / 1000, tz=UTC).date()
    last_day = datetime.fromtimestamp(int(realized[-1]["closed_timestamp_ms"]) / 1000, tz=UTC).date()
    today = datetime.now(tz=UTC).date()
    if today > last_day:
        last_day = today
    pnl_by_day: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for g in realized:
        day = _bucket_day_utc(int(g["closed_timestamp_ms"]))
        pnl_by_day[day] += to_decimal(g["realized_pnl"])

    rows: list[dict[str, Any]] = []
    cursor = first_day
    while cursor <= last_day:
        window_start = cursor - timedelta(days=window_days - 1)
        window_pnl = Decimal("0")
        d = window_start
        while d <= cursor:
            window_pnl += pnl_by_day.get(d.strftime("%Y-%m-%d"), Decimal("0"))
            d = d + timedelta(days=1)
        sample_days = Decimal(str(window_days))
        annualized = (window_pnl * Decimal("365") / sample_days) / effective_capital_usdc
        rows.append({"date": cursor.strftime("%Y-%m-%d"), "apr": str(annualized), "window_pnl_usdc": str(window_pnl)})
        cursor = cursor + timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Multi-account aggregation
# ---------------------------------------------------------------------------


def _bot_for_account(account: DashboardAccount, *, require_private: bool) -> DeribitOptionTrialBot:
    cfg = load_config(account.env_file, require_private=require_private)
    client = DeribitClient(cfg)
    return DeribitOptionTrialBot(cfg, client)


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


def _aggregate_status(accounts: list[DashboardAccount]) -> dict[str, Any]:
    statuses: list[dict[str, Any]] = []
    trade_groups: list[dict[str, Any]] = []
    open_orders: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    account_summaries: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    underlying_index_usd: dict[str, str] = {}
    seen_balance_identity: set[str] = set()

    for account in accounts:
        payload = _bot_for_account(account, require_private=True).status()
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

    equity_statuses = _dedupe_statuses_for_equity_aggregate(accounts, statuses)
    return {
        "env": "multi" if len(accounts) > 1 else accounts[0].config.env,
        "portfolio": _aggregate_portfolios(accounts, statuses, equity_statuses=equity_statuses),
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
                "env": status.get("env"),
                "option_strategy": account.config.option_strategy,
                "portfolio": status.get("portfolio"),
                "trade_group_count": status.get("trade_group_count"),
            }
            for account, status in zip(accounts, statuses, strict=False)
        ],
    }


def _aggregate_groups(accounts: list[DashboardAccount]) -> dict[str, Any]:
    merged_open: list[dict[str, Any]] = []
    merged_closed: list[dict[str, Any]] = []
    underlying_index_usd: dict[str, str] = {}
    next_group_id: dict[str, Any] = {}
    excluded_closed_count = 0

    for account in accounts:
        payload = _closed_groups_payload(account.state_path)
        if _has_private_creds(account.config):
            try:
                _enrich_groups_payload_open_unrealized(
                    _bot_for_account(account, require_private=True),
                    payload,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("groups enrich skipped for %s: %s", account.name, exc)
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
) -> "Any":
    """Build the FastAPI application.

    Imports are local so the rest of the package stays usable on machines
    that haven't installed FastAPI/uvicorn yet.
    """
    try:
        from contextlib import asynccontextmanager

        from fastapi import FastAPI, HTTPException, Query
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse, Response
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover — surfaces a clear hint.
        raise RuntimeError(
            "fastapi/uvicorn not installed; run `pip install -r requirements.txt`"
        ) from exc

    accounts = _make_dashboard_accounts(
        env_file=env_file,
        account_env_files=account_env_files,
    )
    config_public = accounts[0].config
    multi_account = len(accounts) > 1
    interval = int(
        snapshot_interval_sec
        if snapshot_interval_sec is not None
        else os.environ.get("FRONTEND_SNAPSHOT_INTERVAL_SEC", DEFAULT_SNAPSHOT_INTERVAL_SEC)
    )
    state_path = accounts[0].state_path
    ledger_root = accounts[0].ledger_root if not multi_account else accounts[0].ledger_root.parent

    status_cache = _TtlCache(STATUS_CACHE_TTL_SEC)
    report_cache = _TtlCache(REPORT_CACHE_TTL_SEC)
    stress_cache = _TtlCache(STATUS_CACHE_TTL_SEC)
    # Serialize heavy portfolio endpoints so parallel browser tabs / dashboard waves
    # do not stack duplicate Deribit JSON-RPC bursts (often surfaced as 502/timeouts).
    _heavy_portfolio_lock = threading.Lock()

    def _account_bot_factory(account: DashboardAccount) -> Callable[[], DeribitOptionTrialBot]:
        return lambda: _bot_for_account(account, require_private=True)

    schedulers = [
        EquitySnapshotScheduler(
            account_name=account.name,
            bot_factory=_account_bot_factory(account),
            interval_sec=interval,
            ledger_root=account.ledger_root,
            config=account.config,
        )
        for account in accounts
    ]

    @asynccontextmanager
    async def _lifespan(_app: "FastAPI"):
        if enable_scheduler:
            for scheduler in schedulers:
                scheduler.start()
        try:
            yield
        finally:
            for scheduler in schedulers:
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

    @app.get("/api/spot")
    def api_spot() -> dict[str, Any]:
        """Public BTC/ETH USD index for dashboard header (no private auth)."""
        try:
            client = DeribitClient(config_public)
            btc_raw = client.get_index_price("btc_usd")
            eth_raw = client.get_index_price("eth_usd")
            btc_px = to_decimal(btc_raw.get("index_price") or 0)
            eth_px = to_decimal(eth_raw.get("index_price") or 0)
            return {
                "BTC": str(btc_px) if btc_px > 0 else None,
                "ETH": str(eth_px) if eth_px > 0 else None,
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"spot failed: {exc}") from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        all_have_creds = all(_has_private_creds(account.config) for account in accounts)
        any_scheduler_running = any(scheduler.state.running for scheduler in schedulers)
        last_attempts = [s.state.last_attempt_ms for s in schedulers if s.state.last_attempt_ms is not None]
        last_successes = [s.state.last_success_ms for s in schedulers if s.state.last_success_ms is not None]
        last_errors = [
            f"{account.name}: {scheduler.state.last_error}"
            for account, scheduler in zip(accounts, schedulers, strict=False)
            if scheduler.state.last_error
        ]
        return {
            "env": "multi" if multi_account else config_public.env,
            "has_private_creds": all_have_creds,
            "scheduler_running": any_scheduler_running,
            "snapshot_interval_sec": interval,
            "last_snapshot_attempt_ms": max(last_attempts, default=None),
            "last_snapshot_success_ms": max(last_successes, default=None),
            "last_snapshot_error": "; ".join(last_errors) if last_errors else None,
            "state_file": str(state_path) if not multi_account else "multi",
            "ledger_dir": str(ledger_root),
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

    def _locked_aggregate_status() -> dict[str, Any]:
        with _heavy_portfolio_lock:
            return _aggregate_status(accounts)

    def _locked_aggregate_report(d: int) -> dict[str, Any]:
        with _heavy_portfolio_lock:
            return _aggregate_report(accounts, days=d)

    @app.get("/api/status")
    def api_status() -> Any:
        if not all(_has_private_creds(account.config) for account in accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            payload = status_cache.get_or_set("status", _locked_aggregate_status)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/status aggregate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"status failed: {exc}") from exc
        return JSONResponse(_decimalize(payload))

    @app.get("/api/report")
    def api_report(days: int = Query(default=30, ge=0, le=3650)) -> Any:
        if not all(_has_private_creds(account.config) for account in accounts):
            raise HTTPException(status_code=401, detail="DERIBIT_CLIENT_ID/SECRET not set in env")
        try:
            payload = report_cache.get_or_set(("report", days), lambda: _locked_aggregate_report(days))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("dashboard /api/report aggregate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"report failed: {exc}") from exc
        return JSONResponse(_decimalize(payload))

    @app.get("/api/stress")
    def api_stress(shocks: str = Query(default="0.10,0.20,0.30,0.40,0.50")) -> Any:
        if not all(_has_private_creds(account.config) for account in accounts):
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
        try:
            payload = _aggregate_groups(accounts)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"groups failed: {exc}") from exc
        return JSONResponse(payload)

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

    @app.get("/api/cumulative_pnl_series")
    def api_cumulative_pnl_series() -> Any:
        groups_payload = _aggregate_groups(accounts)
        series = _cumulative_pnl_series(groups_payload["closed"])
        return JSONResponse(series)

    @app.get("/api/apr_series")
    def api_apr_series(
        window_days: int = Query(default=30, ge=1, le=365),
        effective_capital_usdc: float | None = Query(default=None, ge=0),
    ) -> Any:
        groups_payload = _aggregate_groups(accounts)
        capital = (
            Decimal(str(effective_capital_usdc))
            if effective_capital_usdc is not None and effective_capital_usdc > 0
            else sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
        )
        rows = _rolling_apr_series(
            groups_payload["closed"],
            window_days=window_days,
            effective_capital_usdc=capital,
        )
        return JSONResponse({
            "window_days": window_days,
            "effective_capital_usdc": str(capital),
            "rows": rows,
        })

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

    if frontend_dir.is_dir():
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
    log_level: str = "info",
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
    )
    LOGGER.info("serving dashboard on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=int(port), log_level=log_level)


__all__ = ["create_app", "serve"]
