"""Aggregate realized perpetual-hedge PnL from trade journal rows."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from .trade_journal import TradeJournalStore, journal_db_path_for_state, scope_key_for_state
from .utils import format_decimal, to_decimal, utc_now_ms

LOGGER = logging.getLogger(__name__)

_ZERO = Decimal("0")
HEDGE_PERFORMANCE_WINDOW_DAYS = (7, 14, 30, 60)


def is_hedge_perp_label(label: str) -> bool:
    return "-hedge-" in str(label or "")


def is_hedge_perp_instrument(instrument_name: str) -> bool:
    name = str(instrument_name or "").upper()
    return name.endswith("_USDC-PERPETUAL") or name.endswith("-PERPETUAL")


def _row_is_hedge_execution(row: dict[str, Any]) -> bool:
    if str(row.get("event_type") or "").lower() == "hedge":
        return True
    label = str(row.get("label") or "")
    instrument = str(row.get("instrument_name") or "")
    return is_hedge_perp_label(label) and is_hedge_perp_instrument(instrument)


def _profit_loss_usdc_from_row(row: dict[str, Any]) -> Decimal:
    extra = row.get("extra")
    if not isinstance(extra, dict):
        raw = row.get("extra_json")
        if raw:
            try:
                extra = json.loads(raw)
            except json.JSONDecodeError:
                extra = {}
        else:
            extra = {}
    for key in ("profit_loss_usdc", "profit_loss"):
        if key in extra and extra[key] not in (None, ""):
            return to_decimal(extra[key])
    return _ZERO


def _fee_usdc_from_row(row: dict[str, Any]) -> Decimal:
    fee_raw = row.get("fee_usdc")
    if fee_raw not in (None, ""):
        fee = to_decimal(fee_raw)
        return fee if fee > 0 else _ZERO
    return _ZERO


def _summarize_hedge_execution_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_currency: dict[str, dict[str, Decimal | int]] = {}
    total_pl = _ZERO
    total_fees = _ZERO
    trade_count = 0
    for row in rows:
        if not _row_is_hedge_execution(row):
            continue
        trade_count += 1
        instrument = str(row.get("instrument_name") or "").upper()
        currency = instrument.split("_")[0] if "_" in instrument else instrument.split("-")[0]
        currency = currency.upper()
        pl = _profit_loss_usdc_from_row(row)
        fee = _fee_usdc_from_row(row)
        total_pl += pl
        total_fees += fee
        bucket = by_currency.setdefault(
            currency,
            {
                "trade_count": 0,
                "realized_pnl_usdc": _ZERO,
                "fees_usdc": _ZERO,
            },
        )
        bucket["trade_count"] = int(bucket["trade_count"]) + 1
        bucket["realized_pnl_usdc"] = to_decimal(bucket["realized_pnl_usdc"]) + pl
        bucket["fees_usdc"] = to_decimal(bucket["fees_usdc"]) + fee

    net = total_pl - total_fees
    by_ccy_out: dict[str, dict[str, str | int]] = {}
    for currency, bucket in sorted(by_currency.items()):
        pl = to_decimal(bucket["realized_pnl_usdc"])
        fees = to_decimal(bucket["fees_usdc"])
        by_ccy_out[currency] = {
            "trade_count": int(bucket["trade_count"]),
            "realized_pnl_usdc": format_decimal(pl, 4),
            "fees_usdc": format_decimal(fees, 4),
            "net_pnl_usdc": format_decimal(pl - fees, 4),
        }
    return {
        "trade_count": trade_count,
        "realized_pnl_usdc": format_decimal(total_pl, 4),
        "fees_usdc": format_decimal(total_fees, 4),
        "net_pnl_usdc": format_decimal(net, 4),
        "by_currency": by_ccy_out,
    }


def summarize_hedge_pnl_for_scope(
    store: TradeJournalStore,
    scope_key: str,
    *,
    since_ms: int | None = None,
) -> dict[str, Any]:
    """Lifetime or window realized hedge stats for one bot state scope."""
    rows = store.list_executions(scope_key, limit=5000, since_ms=since_ms)
    return _summarize_hedge_execution_rows(rows)


def summarize_hedge_pnl_for_state(state_file: Path, *, since_ms: int | None = None) -> dict[str, Any]:
    path = state_file.resolve()
    store = TradeJournalStore(journal_db_path_for_state(path))
    scope_key = scope_key_for_state(path)
    try:
        return summarize_hedge_pnl_for_scope(store, scope_key, since_ms=since_ms)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("hedge pnl summary failed for %s: %s", path, exc)
        return {
            "trade_count": 0,
            "realized_pnl_usdc": "0",
            "fees_usdc": "0",
            "net_pnl_usdc": "0",
            "by_currency": {},
        }


def merge_hedge_pnl_summaries(parts: list[dict[str, Any]]) -> dict[str, Any]:
    total_pl = _ZERO
    total_fees = _ZERO
    trade_count = 0
    by_currency: dict[str, dict[str, Decimal | int]] = {}
    for part in parts:
        if not part:
            continue
        trade_count += int(part.get("trade_count") or 0)
        total_pl += to_decimal(part.get("realized_pnl_usdc"))
        total_fees += to_decimal(part.get("fees_usdc"))
        for currency, row in (part.get("by_currency") or {}).items():
            cur = str(currency).upper()
            bucket = by_currency.setdefault(
                cur,
                {"trade_count": 0, "realized_pnl_usdc": _ZERO, "fees_usdc": _ZERO},
            )
            bucket["trade_count"] = int(bucket["trade_count"]) + int(row.get("trade_count") or 0)
            bucket["realized_pnl_usdc"] = to_decimal(bucket["realized_pnl_usdc"]) + to_decimal(
                row.get("realized_pnl_usdc")
            )
            bucket["fees_usdc"] = to_decimal(bucket["fees_usdc"]) + to_decimal(row.get("fees_usdc"))

    net = total_pl - total_fees
    by_ccy_out: dict[str, dict[str, str | int]] = {}
    for currency, bucket in sorted(by_currency.items()):
        pl = to_decimal(bucket["realized_pnl_usdc"])
        fees = to_decimal(bucket["fees_usdc"])
        by_ccy_out[currency] = {
            "trade_count": int(bucket["trade_count"]),
            "realized_pnl_usdc": format_decimal(pl, 4),
            "fees_usdc": format_decimal(fees, 4),
            "net_pnl_usdc": format_decimal(pl - fees, 4),
        }
    return {
        "trade_count": trade_count,
        "realized_pnl_usdc": format_decimal(total_pl, 4),
        "fees_usdc": format_decimal(total_fees, 4),
        "net_pnl_usdc": format_decimal(net, 4),
        "by_currency": by_ccy_out,
    }


def hedge_window_since_ms(window_days: int, *, now_ms: int | None = None) -> int:
    end_ms = now_ms if now_ms is not None else utc_now_ms()
    return end_ms - max(int(window_days), 0) * 24 * 3600 * 1000


def hedge_performance_window_net_by_days(
    state_files: list[Path],
    *,
    window_days: tuple[int, ...] = HEDGE_PERFORMANCE_WINDOW_DAYS,
    now_ms: int | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for days in window_days:
        since_ms = hedge_window_since_ms(days, now_ms=now_ms)
        parts = [summarize_hedge_pnl_for_state(path, since_ms=since_ms) for path in state_files]
        merged = merge_hedge_pnl_summaries(parts)
        out[str(days)] = str(merged.get("net_pnl_usdc") or "0")
    return out


def hedge_performance_adjustments(
    state_files: list[Path],
    *,
    window_days: int,
    now_ms: int | None = None,
) -> tuple[Decimal, Decimal]:
    """Return (lifetime net, window net) USDC hedge PnL for performance totals."""
    if not state_files:
        return _ZERO, _ZERO
    lifetime = merge_hedge_pnl_summaries([summarize_hedge_pnl_for_state(path) for path in state_files])
    since_ms = hedge_window_since_ms(window_days, now_ms=now_ms)
    window = merge_hedge_pnl_summaries([summarize_hedge_pnl_for_state(path, since_ms=since_ms) for path in state_files])
    return to_decimal(lifetime.get("net_pnl_usdc")), to_decimal(window.get("net_pnl_usdc"))


def attach_hedge_performance_windows(summary: dict[str, Any], state_files: list[Path]) -> dict[str, Any]:
    """Add rolling window net hedge PnL for dashboard performance charts."""
    if int(summary.get("trade_count") or 0) <= 0:
        return summary
    enriched = dict(summary)
    enriched["window_net_pnl_by_days"] = hedge_performance_window_net_by_days(state_files)
    return enriched
