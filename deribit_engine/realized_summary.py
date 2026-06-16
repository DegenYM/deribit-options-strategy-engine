"""Realized performance summary from on-disk closed trade groups (no Deribit report pass)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import TradeGroup
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


def _entry_timestamps_ms(
    rows: list[dict[str, Any]],
    *,
    open_rows: list[dict[str, Any]] | None = None,
) -> list[int]:
    entries: list[int] = []
    for row in rows:
        entry = _entry_timestamp_ms(row)
        if entry is not None and entry > 0:
            entries.append(entry)
    for row in open_rows or []:
        entry = _entry_timestamp_ms(row)
        if entry is not None and entry > 0:
            entries.append(entry)
    return entries


def _realized_sample_days(
    rows: list[dict[str, Any]],
    *,
    open_rows: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> Decimal:
    """Earliest entry (closed or open) through live UTC now (not last close)."""
    entries = _entry_timestamps_ms(rows, open_rows=open_rows)
    if not entries:
        return Decimal("0")
    start_ms = min(entries)
    end_ms = now_ms if now_ms is not None else utc_now_ms()
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


def profit_sweep_lifetime_usdt(group: TradeGroup) -> Decimal:
    """Journal lifetime USDT high-water from premium swap reconcile."""
    lifetime = group.profit_sweep_quote_proceeds_lifetime
    if lifetime > 0:
        return lifetime
    quote = group.profit_sweep_quote_proceeds
    if quote > 0 and str(group.profit_sweep_status or "").lower() == "filled":
        return quote
    return Decimal("0")


def profit_sweep_realized_usdt(group: TradeGroup) -> Decimal:
    """USDT actually received from premium swap fills (exchange quote, not reconcile high-water)."""
    quote = group.profit_sweep_quote_proceeds
    if quote > 0 and str(group.profit_sweep_status or "").lower() == "filled":
        return quote
    return profit_sweep_lifetime_usdt(group)


def _profit_sweep_display_usdc(
    group: TradeGroup,
    native: Decimal,
    spot: Decimal,
) -> Decimal:
    """Coin profit swapped to USDT uses lifetime quote proceeds, not live wallet."""
    if native <= 0:
        return native * spot
    sweep = str(group.profit_sweep_status or "").lower()
    swept_usdt = profit_sweep_realized_usdt(group)
    sweep_amt = group.profit_sweep_amount if group.profit_sweep_amount > 0 else native
    if sweep == "filled" and swept_usdt > 0:
        swept_native = min(sweep_amt, native)
        held_native = max(Decimal("0"), native - swept_native)
        if held_native > 0:
            return swept_usdt + held_native * spot
        return swept_usdt
    return native * spot


def realized_pnl_usdc_at_spot(
    row: dict[str, Any],
    spot_index: dict[str, Decimal] | None,
) -> Decimal | None:
    """USDC-linear uses stored USDC PnL; coin collateral uses native × live index."""
    if row.get("realized_pnl") is None:
        return None
    group = TradeGroup.from_dict(row)
    book = group.collateral_book()
    if book == "USDC":
        return group.realized_pnl
    if not spot_index:
        return to_decimal(row.get("realized_pnl"))
    spot = spot_index.get(book)
    if spot is None or spot <= 0:
        return to_decimal(row.get("realized_pnl"))
    native_raw = row.get("realized_pnl_collateral_native")
    native = to_decimal(native_raw) if native_raw is not None else group.realized_pnl_collateral_native
    if native is None:
        group.backfill_realized_pnl_collateral_native(spot_index_usd=spot)
        native = group.realized_pnl_collateral_native
    if native is not None:
        return _profit_sweep_display_usdc(group, native, spot)
    return None


def _row_realized_pnl_usdc(
    row: dict[str, Any],
    spot_index: dict[str, Decimal] | None,
) -> Decimal:
    if spot_index:
        at_spot = realized_pnl_usdc_at_spot(row, spot_index)
        if at_spot is not None:
            return at_spot
    return to_decimal(row.get("realized_pnl"))


def realized_summary_from_closed(
    closed_rows: list[dict[str, Any]],
    *,
    effective_capital_usdc: Decimal,
    target_portfolio_apr: Decimal,
    window_days: int = 30,
    spot_index: dict[str, Decimal] | None = None,
    open_rows: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Build the same ``summary`` shape as ``bot.report()`` from closed group dicts."""
    realized = [
        row for row in closed_rows if row.get("realized_pnl") is not None and _closed_timestamp_ms(row) is not None
    ]
    unresolved = [row for row in closed_rows if row not in realized]
    total_realized = sum((_row_realized_pnl_usdc(row, spot_index) for row in realized), Decimal("0"))
    total_holding = sum((_holding_days(row) for row in realized), Decimal("0"))
    wins = sum(1 for row in realized if _row_realized_pnl_usdc(row, spot_index) > 0)
    realized_count = Decimal(str(len(realized)))
    lifetime_days = _realized_sample_days(realized, open_rows=open_rows, now_ms=now_ms)
    window_rows, window_days_used = _window_rows(realized, window_days)
    window_pnl = sum((_row_realized_pnl_usdc(row, spot_index) for row in window_rows), Decimal("0"))

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


_SPOT_PATCHED_SUMMARY_KEYS = (
    "realized_pnl_usdc",
    "avg_realized_pnl_usdc",
    "realized_win_rate",
    "lifetime_realized_apr",
    "window_realized_pnl_usdc",
    "window_realized_apr",
    "window_realized_closed_group_count",
    "window_days_used",
)


def patch_realized_report_spot_pnl(
    report_payload: dict[str, Any],
    closed_rows: list[dict[str, Any]],
    *,
    spot_index: dict[str, Decimal] | None,
    window_days: int,
) -> None:
    """Refresh lifetime/window USD PnL and APR on a cached report using live index."""
    if not spot_index:
        return
    summary = report_payload.get("summary")
    if not isinstance(summary, dict):
        return
    capital = to_decimal(summary.get("effective_capital_usdc"))
    target = to_decimal(summary.get("target_portfolio_apr"))
    patched = realized_summary_from_closed(
        closed_rows,
        effective_capital_usdc=capital,
        target_portfolio_apr=target,
        window_days=window_days,
        spot_index=spot_index,
    )
    for key in _SPOT_PATCHED_SUMMARY_KEYS:
        if key in patched:
            summary[key] = patched[key]
