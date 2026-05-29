from __future__ import annotations

import copy
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import BotConfig
from ..engine import DeribitOptionTrialBot, ExchangePrefetch
from ..models import (
    OrderBookSnapshot,
    is_phantom_reconcile_close,
    open_short_instrument_names,
)
from ..state import StrategyStateStore, load_performance_exclusion_group_ids
from ..trade_apr import entry_dte_days_at_open, realized_apr_from_close
from ..trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state
from ..utils import format_decimal, parse_option_name, to_decimal
from .types import (
    DashboardAccount,
    _TtlCache,
)

LOGGER = logging.getLogger(__name__)

from .helpers import (
    _apply_spot_native_backfill,
    _contract_size_for_instrument,
    _dec,
    _decimalize,
    _dedupe_trade_group_rows,
    _has_private_creds,
    _live_api_identity,
    _state_path_mtime,
    _tag_rows,
)

_closed_groups_payload_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_closed_groups_payload_cache_lock = threading.Lock()
_closed_groups_payload_load_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


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
    idx = (
        index_usd.get(coll) or _dec(group.get("close_index_usd")) or _dec(group.get("entry_index_usd")) or Decimal("0")
    )
    return idx


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
    from ..models import TradeGroup

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
            import deribit_engine.frontend_server as pkg

            bot = pkg._bot_for_account(account, require_private=True)
            prefetch = prefetches.get(_live_api_identity(account))
            pkg._enrich_groups_payload_open_unrealized(
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

    import deribit_engine.frontend_server as pkg

    try:
        prefetches = pkg._prefetch_all_accounts(accounts, cache=exchange_prefetch_cache)
    except Exception as exc:  # noqa: BLE001 — closed groups from disk should still load.
        LOGGER.warning("groups exchange prefetch failed; continuing without live marks: %s", exc)
        prefetches = {}
        for account in accounts:
            if not _has_private_creds(account.config):
                continue
            key = _live_api_identity(account)
            if key not in prefetches:
                prefetches[key] = None

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
