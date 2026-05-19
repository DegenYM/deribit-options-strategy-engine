"""Realized performance summary from on-disk closed trade groups (no Deribit report pass)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .utils import format_decimal, safe_div, to_decimal, utc_now_ms


def _closed_timestamp_ms(row: dict[str, Any]) -> int | None:
    raw = row.get("closed_timestamp_ms")
    if raw is not None:
        return int(raw)
    return None


def _entry_timestamp_ms(row: dict[str, Any]) -> int | None:
    raw = row.get("entry_timestamp_ms")
    if raw is not None:
        return int(raw)
    return None


def _holding_days(row: dict[str, Any]) -> Decimal:
    closed = _closed_timestamp_ms(row)
    entry = _entry_timestamp_ms(row)
    if closed is None or entry is None or entry <= 0:
        return Decimal("0")
    return Decimal(str(max(closed - entry, 0))) / Decimal("86400000")


def _realized_sample_days(rows: list[dict[str, Any]]) -> Decimal:
    timestamps = [
        (entry, closed)
        for row in rows
        for entry, closed in [(_entry_timestamp_ms(row), _closed_timestamp_ms(row))]
        if closed is not None and entry is not None and entry > 0
    ]
    if not timestamps:
        return Decimal("0")
    start_ms = min(entry for entry, _ in timestamps)
    end_ms = max(closed for _, closed in timestamps if closed is not None)
    if end_ms <= start_ms:
        return Decimal("0")
    return Decimal(str(end_ms - start_ms)) / Decimal("86400000")


def _window_rows(rows: list[dict[str, Any]], days: int) -> tuple[list[dict[str, Any]], Decimal]:
    if not rows:
        return [], Decimal("0")
    if days <= 0:
        return rows, _realized_sample_days(rows)
    cutoff_ms = utc_now_ms() - (days * 24 * 3600 * 1000)
    windowed = [row for row in rows if (_closed_timestamp_ms(row) or 0) >= cutoff_ms]
    return windowed, Decimal(str(days))


def _annualize_apr(pnl: Decimal, sample_days: Decimal, capital: Decimal) -> Decimal:
    if pnl == 0 or sample_days <= 0 or capital <= 0:
        return Decimal("0")
    return safe_div(pnl, capital) * (Decimal("365") / sample_days)


def realized_summary_from_closed(
    closed_rows: list[dict[str, Any]],
    *,
    effective_capital_usdc: Decimal,
    target_portfolio_apr: Decimal,
    window_days: int = 30,
) -> dict[str, Any]:
    """Build the same ``summary`` shape as ``bot.report()`` from closed group dicts."""
    realized = [
        row
        for row in closed_rows
        if row.get("realized_pnl") is not None and _closed_timestamp_ms(row) is not None
    ]
    unresolved = [
        row
        for row in closed_rows
        if row not in realized
    ]
    total_realized = sum((to_decimal(row.get("realized_pnl")) for row in realized), Decimal("0"))
    total_holding = sum((_holding_days(row) for row in realized), Decimal("0"))
    wins = sum(1 for row in realized if to_decimal(row.get("realized_pnl")) > 0)
    realized_count = Decimal(str(len(realized)))
    lifetime_days = _realized_sample_days(realized)
    window_rows, window_days_used = _window_rows(realized, window_days)
    window_pnl = sum((to_decimal(row.get("realized_pnl")) for row in window_rows), Decimal("0"))

    capital = effective_capital_usdc if effective_capital_usdc > 0 else Decimal("0")

    return {
        "effective_capital_usdc": format_decimal(capital, 8),
        "target_portfolio_apr": format_decimal(target_portfolio_apr, 8),
        "open_group_count": 0,
        "closed_group_count": str(len(closed_rows)),
        "performance_excluded_closed_group_count": "0",
        "realized_closed_group_count": str(len(realized)),
        "unresolved_closed_group_count": str(len(unresolved)),
        "open_max_loss_usdc": "0",
        "realized_pnl_usdc": format_decimal(total_realized, 8),
        "avg_realized_pnl_usdc": format_decimal(safe_div(total_realized, realized_count), 8),
        "realized_win_rate": format_decimal(safe_div(Decimal(str(wins)), realized_count), 8),
        "avg_holding_days": format_decimal(safe_div(total_holding, realized_count), 8),
        "lifetime_sample_days": format_decimal(lifetime_days, 8),
        "lifetime_realized_apr": format_decimal(_annualize_apr(total_realized, lifetime_days, capital), 8),
        "window_days_requested": str(window_days),
        "window_days_used": format_decimal(window_days_used, 8),
        "window_realized_closed_group_count": str(len(window_rows)),
        "window_realized_pnl_usdc": format_decimal(window_pnl, 8),
        "window_realized_apr": format_decimal(
            _annualize_apr(window_pnl, window_days_used, capital),
            8,
        ),
    }
