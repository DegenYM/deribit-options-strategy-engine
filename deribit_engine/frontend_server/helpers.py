from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import BotConfig, has_private_creds_config
from ..engine import DeribitOptionTrialBot, ExchangePrefetch
from ..env_layout import (
    account_slug_from_env_path,
    find_repo_root,
    investor_frontend_ledger_dir,
    investor_metrics_db_path,
    load_investor_manifest,
    resolve_investor_scope,
    shared_market_db_path,
)
from ..exceptions import ConfigurationError
from ..metrics_store import MetricsStore, fingerprint_from_cache_key, performance_scope_key
from ..models import (
    TradeGroup,
    normalize_strategy_name,
)
from ..utils import format_decimal, json_default, to_decimal, utc_now_ms
from .constants import (
    _INSTRUMENT_CONTRACT_SIZE_CACHE,
    DEFAULT_SNAPSHOT_INTERVAL_SEC,
    LEDGER_DIR,
    LEGACY_METRICS_DB_PATH,
    ROLLING_APR_MAX_CHART_DAYS,
    _active_metrics_db_path,
)
from .types import (
    DashboardAccount,
    SnapshotState,
)

LOGGER = logging.getLogger(__name__)


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


def _dashboard_strategies(
    *,
    investor_id: str | None,
    repo_root: Path | None,
    accounts: list[DashboardAccount],
) -> list[str]:
    """Strategy ids shown on the dashboard (from accounts.toml enabled rows when scoped)."""
    if investor_id and repo_root is not None:
        try:
            manifest = load_investor_manifest(investor_id, repo_root=repo_root)
            ordered: list[str] = []
            seen: set[str] = set()
            for account in manifest.enabled_accounts():
                strategy = normalize_strategy_name(account.strategy)
                if strategy in seen:
                    continue
                seen.add(strategy)
                ordered.append(strategy)
            return ordered
        except ConfigurationError:
            pass

    ordered = []
    seen: set[str] = set()
    for account in accounts:
        strategy = normalize_strategy_name(account.config.option_strategy)
        if strategy in seen:
            continue
        seen.add(strategy)
        ordered.append(strategy)
    return ordered


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
    import deribit_engine.frontend_server as pkg

    for item in env_files:
        cfg = pkg.load_config(item, require_private=False)
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


def _spot_series_cache_key(spot_payload: dict[str, Any] | None) -> tuple[tuple[str, str], ...] | None:
    """Hashable spot fingerprint for ``_TtlCache`` keys (raw spot dicts are unhashable)."""
    if spot_payload is None:
        return None
    idx = _spot_index_decimals(spot_payload)
    if not idx:
        return None
    return tuple(sorted((sym, str(px)) for sym, px in idx.items()))


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
        from .groups_service import _journal_executions_for_group

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
    from .groups_service import _aggregate_closed_groups, _closed_groups_cache_key

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
    value_key: str = "pnl_usdc",
) -> dict[str, Any]:
    days_sorted = sorted(daily_total.keys())
    books_sorted = sorted(daily_by_book.keys())

    cumulative_total: list[dict[str, Any]] = []
    running_total = Decimal("0")
    for day in days_sorted:
        running_total += daily_total[day]
        cumulative_total.append({"date": day, value_key: str(running_total)})

    cumulative_by_book: dict[str, list[dict[str, Any]]] = {}
    for book in books_sorted:
        running = Decimal("0")
        rows: list[dict[str, Any]] = []
        for day in days_sorted:
            running += daily_by_book[book].get(day, Decimal("0"))
            rows.append({"date": day, value_key: str(running)})
        cumulative_by_book[book] = rows

    daily_total_rows = [{"date": day, value_key: str(daily_total[day])} for day in days_sorted]
    daily_by_book_rows = {
        book: [{"date": day, value_key: str(daily_by_book[book].get(day, Decimal("0")))} for day in days_sorted]
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


def _native_realized_pnl_for_row(
    row: dict[str, Any],
    spot_index: dict[str, Decimal] | None,
) -> Decimal | None:
    """Realized PnL in each book's native unit (premium profit for coin collateral)."""
    if row.get("realized_pnl") is None:
        return None
    group = TradeGroup.from_dict(row)
    book = str(row.get("collateral_currency") or row.get("currency") or "USDC").upper()
    if book == "USDC":
        return to_decimal(row.get("realized_pnl"))
    native_raw = row.get("realized_pnl_collateral_native")
    if native_raw is not None:
        return to_decimal(native_raw)
    if spot_index:
        spot = spot_index.get(book)
        if spot is not None and spot > 0:
            group.backfill_realized_pnl_collateral_native(spot_index_usd=spot)
            if group.realized_pnl_collateral_native is not None:
                return group.realized_pnl_collateral_native
    return None


def _cumulative_spot_pnl_series(
    closed: list[dict[str, Any]],
    *,
    spot_index: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Per-book cumulative realized PnL in native spot units (BTC / ETH only)."""
    spot_books = frozenset({"BTC", "ETH"})
    daily_by_book: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    daily_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    realized = [g for g in closed if g.get("closed_timestamp_ms") is not None and g.get("realized_pnl") is not None]
    included = 0
    for g in realized:
        book = str(g.get("collateral_currency") or g.get("currency") or "USDC").upper()
        if book not in spot_books:
            continue
        pnl = _native_realized_pnl_for_row(g, spot_index)
        if pnl is None:
            continue
        day = _bucket_day_utc(int(g["closed_timestamp_ms"]))
        daily_by_book[book][day] += pnl
        daily_total[day] += pnl
        included += 1

    return _cumulative_pnl_series_from_daily(
        {book: dict(days) for book, days in daily_by_book.items()},
        dict(daily_total),
        realized_count=included,
        value_key="pnl_native",
    )


def _cumulative_spot_pnl_series_from_accounts(
    accounts: list[DashboardAccount],
    *,
    spot_index: dict[str, Decimal] | None,
) -> dict[str, Any]:
    from .groups_service import _aggregate_closed_groups

    closed = _aggregate_closed_groups(accounts)
    return _cumulative_spot_pnl_series(closed, spot_index=spot_index)


def _stable_realized_pnl_usdc(
    row: dict[str, Any],
    spot_index: dict[str, Decimal] | None,
) -> Decimal | None:
    """Stablecoin PnL: USDC rows + coin rows at live spot / profit-sweep USDT."""
    if row.get("realized_pnl") is None:
        return None
    if spot_index:
        from ..realized_summary import realized_pnl_usdc_at_spot

        at_spot = realized_pnl_usdc_at_spot(row, spot_index)
        if at_spot is not None:
            return at_spot
    return to_decimal(row.get("realized_pnl"))


def _cumulative_pnl_series(
    closed: list[dict[str, Any]],
    *,
    spot_index: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Return per-book + total cumulative realized PnL by UTC day.

    When ``spot_index`` is set, coin-collateral rows use stablecoin display rules
    (profit-sweep USDT proceeds + live index for unswept native). Otherwise each
    closed group's stored ``realized_pnl`` is used.
    """
    daily_by_book: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    daily_total: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    realized = [g for g in closed if g.get("closed_timestamp_ms") is not None and g.get("realized_pnl") is not None]
    for g in realized:
        day = _bucket_day_utc(int(g["closed_timestamp_ms"]))
        pnl = _stable_realized_pnl_usdc(g, spot_index)
        if pnl is None:
            continue
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

    daily_total_rows = [{"date": day, "pnl_usdc": str(daily_total[day])} for day in days_sorted]
    daily_by_book_rows = {
        book: [{"date": day, "pnl_usdc": str(daily_by_book[book].get(day, Decimal("0")))} for day in days_sorted]
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


def _cumulative_stable_pnl_series(
    accounts: list[DashboardAccount],
    *,
    spot_index: dict[str, Decimal] | None,
) -> dict[str, Any]:
    """Cumulative realized PnL in stablecoin terms (matches dashboard overview KPIs)."""
    from .groups_service import _aggregate_closed_groups

    closed = _aggregate_closed_groups(accounts)
    return _cumulative_pnl_series(closed, spot_index=spot_index)


_CRYPTO_NATIVE_HINT_MAX: dict[str, Decimal] = {
    "BTC": Decimal("10"),
    "ETH": Decimal("100"),
}


def _coin_book_equity_looks_native(raw: Decimal, book: str) -> bool:
    """Legacy ledger rows sometimes store coin-book ``equity_by_book`` in native units."""
    book = str(book or "").upper()
    cap = _CRYPTO_NATIVE_HINT_MAX.get(book)
    if cap is None or raw <= 0:
        return False
    return raw <= cap


def compute_equity_native_by_book_for_row(
    row: dict[str, Any],
    *,
    index_btc: Decimal | None = None,
    index_eth: Decimal | None = None,
    books: tuple[str, ...] = ("BTC", "ETH", "USDC", "USDT"),
) -> dict[str, Decimal]:
    """Return native balances for each book present on a ledger snapshot row."""
    out: dict[str, Decimal] = {}
    for book in books:
        native = _equity_native_from_ledger_row(
            row,
            book,
            index_btc=index_btc,
            index_eth=index_eth,
        )
        if native is not None:
            out[book] = native
    return out


def _resolve_index_prices_for_ledger_row(
    row: dict[str, Any],
    *,
    market_store: Any | None,
    index_btc_series: list[tuple[int, Decimal]] | None = None,
    index_eth_series: list[tuple[int, Decimal]] | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    btc = to_decimal(row.get("index_btc_usd")) if row.get("index_btc_usd") else None
    eth = to_decimal(row.get("index_eth_usd")) if row.get("index_eth_usd") else None
    if btc is not None and btc > 0 and eth is not None and eth > 0:
        return btc, eth
    ts = int(row.get("ts_ms") or 0)
    if market_store is not None and ts > 0:
        snap = market_store.nearest_at_or_before(ts)
        if snap is None:
            snap = market_store.latest()
        if snap is not None:
            if btc is None or btc <= 0:
                btc = snap.btc_usd
            if eth is None or eth <= 0:
                eth = snap.eth_usd
    if ts > 0:
        if (btc is None or btc <= 0) and index_btc_series:
            btc = _index_price_at_series(index_btc_series, ts)
        if (eth is None or eth <= 0) and index_eth_series:
            eth = _index_price_at_series(index_eth_series, ts)
    return btc, eth


def _index_price_at_series(series: list[tuple[int, Decimal]], ts_ms: int) -> Decimal | None:
    if not series:
        return None
    best_ts, best_px = min(series, key=lambda item: abs(item[0] - ts_ms))
    return best_px if best_px > 0 else None


def _equity_native_from_ledger_row(
    row: dict[str, Any],
    book: str,
    *,
    index_btc: Decimal | None = None,
    index_eth: Decimal | None = None,
) -> Decimal | None:
    """Native book equity from ledger row.

    Prefer stored ``equity_native_by_book``; for legacy rows derive coin books from
    ``equity_by_book`` (USDC equivalent at snapshot) ÷ index price at snapshot time.
    """
    book = str(book or "").upper()
    native_map = row.get("equity_native_by_book") or {}
    equity_map = row.get("equity_by_book") or {}
    usdc_eq_raw = equity_map.get(book)
    usdc_eq = to_decimal(usdc_eq_raw) if usdc_eq_raw is not None else None

    if book in native_map and native_map.get(book) is not None:
        native = to_decimal(native_map[book])
        if book in ("USDC", "USDT") or native > 0 or usdc_eq is None or usdc_eq <= 0:
            return native

    if usdc_eq is None:
        return None

    if book in ("USDC", "USDT"):
        return usdc_eq

    row_btc_idx = to_decimal(row.get("index_btc_usd")) if row.get("index_btc_usd") else None
    row_eth_idx = to_decimal(row.get("index_eth_usd")) if row.get("index_eth_usd") else None
    if book == "BTC":
        idx = row_btc_idx if row_btc_idx is not None and row_btc_idx > 0 else index_btc
    elif book == "ETH":
        idx = row_eth_idx if row_eth_idx is not None and row_eth_idx > 0 else index_eth
    else:
        return None
    if idx is None or idx <= 0:
        return None
    if _coin_book_equity_looks_native(usdc_eq, book):
        return usdc_eq
    return usdc_eq / idx


def _market_index_lookup(accounts: list[DashboardAccount]) -> Any | None:
    if not accounts:
        return None
    repo_root = find_repo_root(accounts[0].env_file)
    if repo_root is None:
        return None
    from ..market_snapshot_store import MarketSnapshotStore

    path = shared_market_db_path(repo_root)
    if not path.exists():
        return None
    return MarketSnapshotStore(path)


def _index_prices_for_ledger_row(
    row: dict[str, Any],
    *,
    market_store: Any | None,
    index_btc_series: list[tuple[int, Decimal]] | None = None,
    index_eth_series: list[tuple[int, Decimal]] | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    return _resolve_index_prices_for_ledger_row(
        row,
        market_store=market_store,
        index_btc_series=index_btc_series,
        index_eth_series=index_eth_series,
    )


def _equity_native_by_book_series(
    accounts: list[DashboardAccount],
    *,
    max_chart_days: int = ROLLING_APR_MAX_CHART_DAYS,
) -> dict[str, Any]:
    """Daily last native equity per book from ledger snapshots (de-duped by API login)."""
    books = ("BTC", "ETH", "USDC")
    market_store = _market_index_lookup(accounts)
    last_by_day_identity: dict[tuple[date, str], tuple[int, dict[str, Any]]] = {}
    for account in accounts:
        identity = _live_api_identity(account)
        for row in _read_ledger(account.ledger_root):
            ts = int(row.get("ts_ms") or 0)
            if ts <= 0:
                continue
            day = datetime.fromtimestamp(ts / 1000, tz=UTC).date()
            key = (day, identity)
            prev = last_by_day_identity.get(key)
            if prev is None or ts >= prev[0]:
                last_by_day_identity[key] = (ts, row)

    daily_by_book: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for (day, _identity), (_ts, row) in last_by_day_identity.items():
        index_btc, index_eth = _index_prices_for_ledger_row(row, market_store=market_store)
        for book in books:
            native = _equity_native_from_ledger_row(
                row,
                book,
                index_btc=index_btc,
                index_eth=index_eth,
            )
            if native is None:
                continue
            daily_by_book[day][book] += native

    if not daily_by_book:
        return {
            "books": list(books),
            "series_by_book": {book: [] for book in books},
            "day_count": 0,
        }

    first_day = min(daily_by_book.keys())
    last_day = max(daily_by_book.keys())
    chart_start = first_day
    if max_chart_days > 0 and (last_day - first_day).days + 1 > max_chart_days:
        chart_start = last_day - timedelta(days=max_chart_days - 1)

    series_by_book: dict[str, list[dict[str, Any]]] = {book: [] for book in books}
    cursor = chart_start
    while cursor <= last_day:
        day_str = cursor.strftime("%Y-%m-%d")
        for book in books:
            equity = daily_by_book[cursor].get(book, Decimal("0"))
            series_by_book[book].append({"date": day_str, "equity_native": str(equity)})
        cursor += timedelta(days=1)

    return {
        "books": list(books),
        "series_by_book": series_by_book,
        "day_count": (last_day - chart_start).days + 1,
    }


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
    return {book: _ratio(numerators[book], denominators[book]) for book in sorted(set(numerators) | set(denominators))}
