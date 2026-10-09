"""Realized P&L of exchange spot fills that no trade-group journal records.

Total profit is built bottom-up from per-group journals (premium swaps, ITM
spot exit / restore, CSP assignment buys, profit sweeps). Spot fills placed
outside the strategy label prefix — operator unwinds, ``overbuy-unwind-*``,
manual unlabeled orders, stablecoin conversions — never reach a journal, so
their realized result is invisible to the dashboard.

This module replays the full spot fill history and books realized P&L only for
those unattributed *sells* (and stable conversions). Each unattributed sell is
matched, at average cost, against earlier buy lots in priority order:

1. lots carrying the same 4-digit group id in their label (``overbuy-unwind-0033``
   unwinds ``…-csp-restore-btc-0033``);
2. strategy CSP premium-swap lots when the sell label names a CSP swap
   (``operator-unwind-premature-csp-swap`` unwinds runaway premium-swap buys);
3. earlier unattributed buy lots (manual buys).

Any remainder has no known basis and is booked at the sale price (zero P&L):
selling principal coin is an asset swap, not profit. Unattributed buys only add
lots; coin still held is never marked to market here.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .utils import format_decimal, to_decimal

LOGGER = logging.getLogger(__name__)

ZERO = Decimal("0")
COIN_BOOKS = ("BTC", "ETH")
STABLES = ("USDC", "USDT")
CACHE_TTL_SEC = 300.0

_GROUP_ID_RE = re.compile(r"-(\d{4})(?=-|$)")
_cache_lock = threading.Lock()
_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}


@dataclass
class _Lot:
    ts: int
    native: Decimal
    unit_cost: Decimal
    kind: str  # "unattributed" | "csp_swap" | "strategy"
    group_id: str | None


def is_strategy_label(label: str, order_label_prefix: str) -> bool:
    prefix = str(order_label_prefix or "").strip()
    return bool(prefix) and str(label or "").startswith(f"{prefix}-")


def label_group_id(label: str) -> str | None:
    match = _GROUP_ID_RE.search(str(label or ""))
    return match.group(1) if match else None


def _is_csp_swap_label(label: str) -> bool:
    text = str(label or "").lower()
    return "csp" in text and "swap" in text


def _split_instrument(name: str) -> tuple[str, str] | None:
    parts = str(name or "").upper().split("_")
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _fee_in_quote(trade: dict[str, Any], base: str) -> Decimal:
    fee = to_decimal(trade.get("fee"))
    if fee == 0:
        return ZERO
    if str(trade.get("fee_currency") or "").upper() == base:
        return fee * to_decimal(trade.get("price"))
    return fee


def _consume(lots: list[_Lot], need: Decimal) -> tuple[Decimal, Decimal]:
    """Average-cost consume up to ``need`` native from ``lots``; returns (qty, basis)."""
    avail = sum((lot.native for lot in lots), ZERO)
    if avail <= 0 or need <= 0:
        return ZERO, ZERO
    qty = min(need, avail)
    cost = sum((lot.native * lot.unit_cost for lot in lots), ZERO)
    basis = cost * qty / avail
    ratio = (avail - qty) / avail
    for lot in lots:
        lot.native *= ratio
    return qty, basis


def compute_unattributed_spot_pnl(
    trades: Iterable[dict[str, Any]],
    order_label_prefix: str,
) -> dict[str, Any]:
    """Replay spot fills (any order) and return unattributed realized P&L in USD."""
    ordered = sorted(
        (t for t in trades if isinstance(t, dict)),
        key=lambda t: (int(t.get("timestamp") or 0), str(t.get("trade_id") or "")),
    )
    lots: dict[str, list[_Lot]] = {book: [] for book in COIN_BOOKS}
    by_book = {book: ZERO for book in (*COIN_BOOKS, "USDC")}
    by_label: dict[str, dict[str, Any]] = {}
    unmatched = {book: ZERO for book in COIN_BOOKS}
    events: list[dict[str, Any]] = []
    trade_count = 0

    def _book_event(ts: int, book: str, usd: Decimal, label: str) -> None:
        by_book[book] += usd
        row = by_label.setdefault(label, {"trades": 0, "usd": ZERO})
        row["trades"] += 1
        row["usd"] += usd
        events.append({"ts_ms": ts, "book": book, "usd": usd, "label": label})

    for trade in ordered:
        pair = _split_instrument(trade.get("instrument_name"))
        if pair is None:
            continue
        base, quote = pair
        amount = to_decimal(trade.get("amount"))
        price = to_decimal(trade.get("price"))
        if amount <= 0 or price <= 0 or quote not in STABLES:
            continue
        label = str(trade.get("label") or "").strip()
        ts = int(trade.get("timestamp") or 0)
        direction = str(trade.get("direction") or "").lower()
        attributed = is_strategy_label(label, order_label_prefix)
        fee = _fee_in_quote(trade, base)
        shown_label = label or "(unlabeled)"

        if base in STABLES:
            # Stable↔stable conversion at $1 parity; journals never record these.
            if attributed:
                continue
            notional = amount * price
            usd = (amount - notional) if direction == "buy" else (notional - amount)
            trade_count += 1
            _book_event(ts, "USDC", usd - fee, shown_label)
            continue
        if base not in COIN_BOOKS:
            continue

        if direction == "buy":
            if attributed:
                kind = "csp_swap" if "csp-premium-swap" in label else "strategy"
            else:
                kind = "unattributed"
                trade_count += 1
            lots[base].append(
                _Lot(
                    ts=ts,
                    native=amount,
                    unit_cost=(amount * price + fee) / amount,
                    kind=kind,
                    group_id=label_group_id(label),
                )
            )
            continue

        if direction != "sell" or attributed:
            continue
        trade_count += 1
        proceeds = amount * price - fee
        prior = [lot for lot in lots[base] if lot.ts <= ts and lot.native > 0]
        gid = label_group_id(label)
        tiers: list[list[_Lot]] = []
        if gid:
            tiers.append([lot for lot in prior if lot.group_id == gid])
        if _is_csp_swap_label(label):
            tiers.append([lot for lot in prior if lot.kind == "csp_swap"])
        tiers.append([lot for lot in prior if lot.kind == "unattributed"])
        need = amount
        basis = ZERO
        for tier in tiers:
            if need <= 0:
                break
            qty, tier_basis = _consume(tier, need)
            need -= qty
            basis += tier_basis
        if need > 0:
            # No known basis: treat as an asset swap at the sale price.
            unmatched[base] += need
            basis += need * price
        _book_event(ts, base, proceeds - basis, shown_label)

    open_lots = {
        book: sum((lot.native for lot in lots[book] if lot.kind == "unattributed"), ZERO) for book in COIN_BOOKS
    }
    total = sum(by_book.values(), ZERO)
    return {
        "total_usd": format_decimal(total, 4),
        "by_book": {book: format_decimal(value, 4) for book, value in by_book.items()},
        "by_label": {
            label: {"trades": row["trades"], "usd": format_decimal(row["usd"], 4)}
            for label, row in sorted(by_label.items())
        },
        "unmatched_native_sold": {book: format_decimal(v, 8) for book, v in unmatched.items()},
        "open_unattributed_native": {book: format_decimal(v, 8) for book, v in open_lots.items()},
        "events": [
            {
                "ts_ms": row["ts_ms"],
                "book": row["book"],
                "usd": format_decimal(row["usd"], 4),
                "label": row["label"],
            }
            for row in events
        ],
        "trade_count": trade_count,
    }


def _iter_spot_trades(client: Any, currency: str) -> Iterable[dict[str, Any]]:
    fetch = getattr(client, "get_user_trades_by_currency", None)
    if not callable(fetch):
        return
    try:
        recent = fetch(currency, kind="spot", count=1000, historical=False)
        yield from list(recent.get("trades") or [])
    except Exception:  # noqa: BLE001
        LOGGER.debug("unattributed_spot: recent trades fetch failed currency=%s", currency, exc_info=True)
    cursor_ts = 0
    while True:
        try:
            batch = fetch(
                currency,
                kind="spot",
                count=1000,
                sorting="asc",
                historical=True,
                start_timestamp=cursor_ts if cursor_ts > 0 else None,
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug("unattributed_spot: historical fetch failed currency=%s", currency, exc_info=True)
            break
        trades = list(batch.get("trades") or [])
        if not trades:
            break
        yield from trades
        if not batch.get("has_more"):
            break
        last_ts = int(trades[-1].get("timestamp") or 0)
        if last_ts <= 0 or last_ts <= cursor_ts:
            break
        cursor_ts = last_ts + 1


def fetch_spot_trades(client: Any) -> list[dict[str, Any]]:
    seen: dict[Any, dict[str, Any]] = {}
    for currency in (*COIN_BOOKS, "USDC"):
        for trade in _iter_spot_trades(client, currency):
            key = trade.get("trade_id") or id(trade)
            seen[key] = trade
    return list(seen.values())


def unattributed_spot_pnl(client: Any, order_label_prefix: str) -> dict[str, Any] | None:
    """Exchange-backed unattributed spot P&L (cached ``CACHE_TTL_SEC`` per client)."""
    key = (id(client), str(order_label_prefix or ""))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]
    result = compute_unattributed_spot_pnl(fetch_spot_trades(client), order_label_prefix)
    payload = result if result["trade_count"] > 0 else None
    with _cache_lock:
        _cache[key] = (now, payload)
    return payload


# ---- exchange balance yield -------------------------------------------------

USDC_REWARD_TYPE = "usdc_reward"
USDC_REWARD_CACHE_TTL_SEC = 1800.0
# Deribit launched USDC balance rewards long after this; bounds the log scan.
_REWARD_LOG_START_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z
_reward_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}


def summarize_usdc_rewards(logs: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Sum ``usdc_reward`` transaction-log credits (USD at $1 parity)."""
    events: list[dict[str, Any]] = []
    total = ZERO
    for row in logs:
        if not isinstance(row, dict) or str(row.get("type") or "") != USDC_REWARD_TYPE:
            continue
        change = to_decimal(row.get("change"))
        if change == 0:
            continue
        total += change
        events.append({"ts_ms": int(row.get("timestamp") or 0), "usd": change})
    if not events:
        return None
    events.sort(key=lambda e: e["ts_ms"])
    return {
        "total_usd": format_decimal(total, 4),
        "events": [{"ts_ms": e["ts_ms"], "usd": format_decimal(e["usd"], 4)} for e in events],
        "count": len(events),
    }


def usdc_reward_income(client: Any) -> dict[str, Any] | None:
    """USDC balance yield credited by Deribit (cached ``USDC_REWARD_CACHE_TTL_SEC``)."""
    key = id(client)
    now = time.monotonic()
    with _cache_lock:
        hit = _reward_cache.get(key)
        if hit and now - hit[0] < USDC_REWARD_CACHE_TTL_SEC:
            return hit[1]
    iterate = getattr(client, "iter_transaction_log", None)
    if not callable(iterate):
        return None
    logs = iterate(
        currency="USDC",
        start_timestamp=_REWARD_LOG_START_MS,
        end_timestamp=int(time.time() * 1000),
        count=250,
    )
    payload = summarize_usdc_rewards(logs)
    with _cache_lock:
        _reward_cache[key] = (now, payload)
    return payload
