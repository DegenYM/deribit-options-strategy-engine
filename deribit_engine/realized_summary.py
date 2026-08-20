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
    if not _profit_sweep_has_exchange_fill(group):
        return Decimal("0")
    exchange_quote = group.profit_sweep_exchange_quote_proceeds
    if exchange_quote > 0:
        return exchange_quote
    quote = group.profit_sweep_quote_proceeds
    if quote > 0:
        return quote
    return profit_sweep_lifetime_usdt(group)


def _profit_sweep_filled_native_sold(group: TradeGroup, native: Decimal) -> Decimal:
    """Native sold for disposition: prefer exchange qty over dust-padded journal amount."""
    if group.profit_sweep_exchange_native > 0:
        return min(group.profit_sweep_exchange_native, native)
    if group.profit_sweep_amount > 0:
        return min(group.profit_sweep_amount, native)
    return native


def _profit_sweep_has_exchange_fill(group: TradeGroup) -> bool:
    status = str(group.profit_sweep_status or "").lower()
    if status != "filled":
        return False
    reason = str(group.profit_sweep_reason or "").lower()
    if "proceeds_reconciled" in reason:
        if group.profit_sweep_exchange_native > 0 or "exchange_fully_swept" in reason:
            return True
        if "premium_amount_synced" in reason and str(group.profit_sweep_order_id or "").strip():
            return True
        # Manual / unlabeled may lack per-group order_id; dust needs exchange qty.
        if "unlabeled_premium_reconciled" in reason:
            return True
        if "manual_swap" in reason:
            return True
        if "dust_pool_sweep" in reason:
            return group.profit_sweep_exchange_native > 0
        return False
    if str(group.profit_sweep_order_id or "").strip():
        return True
    if "exchange_fully_swept" in reason:
        return True
    if "unlabeled_premium_reconciled" in reason:
        return True
    if "manual_swap" in reason:
        return True
    if "dust_pool_sweep" in reason:
        return group.profit_sweep_exchange_native > 0
    return True


def _is_premium_proceeds_pool_excluded(row: dict[str, Any]) -> bool:
    reason = str(row.get("profit_sweep_reason") or "").lower()
    return "unlabeled_premium_reconciled" in reason or "manual_swap" in reason


def _profit_disposition_for_row(row: dict[str, Any]) -> dict[str, Any] | None:
    group = TradeGroup.from_dict(row)
    book = group.collateral_book()
    if book == "USDC":
        pnl = group.realized_pnl
        if pnl is None:
            return None
        return {
            "book": "USDC",
            "held": pnl,
            "pending": Decimal("0"),
            "swept_native": Decimal("0"),
            "swept_usdt": Decimal("0"),
        }
    # ITM + legacy premium-folded spot exit: attribute premium slice to Profit swap
    # (Sold). Cover round-trip stays in exit−restore (with folded USDT subtracted).
    # ITM without fold: premium remains native — use the normal held/sweep path below.
    from .spot_restore_ops import (
        group_has_itm_spot_exit_fills,
        itm_folded_premium_native,
        itm_folded_premium_usdt,
        itm_spot_exit_premium_folded,
    )

    itm_spot_exit = book in ("BTC", "ETH") and group_has_itm_spot_exit_fills(group)
    native = group.realized_pnl_collateral_native
    if native is None:
        group.backfill_realized_pnl_collateral_native()
        native = group.realized_pnl_collateral_native
    if itm_spot_exit and itm_spot_exit_premium_folded(group):
        premium = itm_folded_premium_native(group)
        prem_usdt = itm_folded_premium_usdt(group)
        if premium > 0 and prem_usdt > 0:
            return {
                "book": book,
                "held": Decimal("0"),
                "pending": Decimal("0"),
                "swept_native": premium,
                "swept_usdt": prem_usdt,
                "from_itm_fold": True,
            }
        return None
    # ITM cover−settle path: only positive premium belongs in Profit swap.
    # Settlement losses stay on the ITM exit−restore net after restore.
    if itm_spot_exit and (native is None or native <= 0):
        return None
    if native is None or native == 0:
        return None
    if book not in ("BTC", "ETH"):
        return None
    if native <= 0:
        return {
            "book": book,
            "held": native,
            "pending": Decimal("0"),
            "swept_native": Decimal("0"),
            "swept_usdt": Decimal("0"),
        }
    sweep = str(group.profit_sweep_status or "").lower()
    swept_usdt = profit_sweep_realized_usdt(group)
    sweep_amt = group.profit_sweep_amount if group.profit_sweep_amount > 0 else native
    if sweep == "filled":
        if not _profit_sweep_has_exchange_fill(group):
            return {
                "book": book,
                "held": native,
                "pending": Decimal("0"),
                "swept_native": Decimal("0"),
                "swept_usdt": Decimal("0"),
            }
        swept_native = _profit_sweep_filled_native_sold(group, native)
        remainder = max(Decimal("0"), native - swept_native)
        return {
            "book": book,
            "held": remainder,
            "pending": Decimal("0"),
            "swept_native": swept_native,
            "swept_usdt": swept_usdt,
        }
    if sweep in {"pending", "submitted"}:
        reason = str(group.profit_sweep_reason or "").lower()
        # Cover top-up retain: count as held BTC, not queued pending.
        if "retained_for_cover" in reason:
            prior_exchange = (
                group.profit_sweep_exchange_native if group.profit_sweep_exchange_native > 0 else Decimal("0")
            )
            swept_native = min(prior_exchange, native) if prior_exchange > 0 else Decimal("0")
            held = max(Decimal("0"), native - swept_native)
            return {
                "book": book,
                "held": held,
                "pending": Decimal("0"),
                "swept_native": swept_native,
                "swept_usdt": swept_usdt,
                "cover_retained": held,
            }
        prior_exchange = group.profit_sweep_exchange_native if group.profit_sweep_exchange_native > 0 else Decimal("0")
        prior_journal = (
            group.profit_sweep_amount
            if group.profit_sweep_amount > 0 and group.profit_sweep_amount < native
            else Decimal("0")
        )
        prior_native = prior_exchange if prior_exchange > 0 else prior_journal
        if prior_native > 0 and prior_native < native:
            remainder = max(Decimal("0"), native - prior_native)
            return {
                "book": book,
                "held": Decimal("0"),
                "pending": remainder,
                "swept_native": prior_native,
                "swept_usdt": swept_usdt,
            }
        return {
            "book": book,
            "held": Decimal("0"),
            "pending": sweep_amt,
            "swept_native": Decimal("0"),
            "swept_usdt": Decimal("0"),
        }
    return {
        "book": book,
        "held": native,
        "pending": Decimal("0"),
        "swept_native": Decimal("0"),
        "swept_usdt": Decimal("0"),
    }


def _aggregate_profit_disposition(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    held_native: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0"), "USDC": Decimal("0")}
    loss_native: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    cover_retained_native: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    pending_sweep_native: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    swept_native_ref: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    swept_quote_proceeds: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    folded_swept_native: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    folded_swept_quote: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    excluded_swept_native: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    excluded_swept_quote: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    any_row = False
    for row in rows:
        disp = _profit_disposition_for_row(row)
        if not disp:
            continue
        book = str(disp["book"]).upper()
        any_row = True
        if book == "USDC":
            held_native["USDC"] += disp["held"]
            continue
        if disp["held"] < 0:
            loss_native[book] += disp["held"]
        else:
            held_native[book] += disp["held"]
        cover_retained = to_decimal(disp.get("cover_retained"))
        if cover_retained > 0:
            cover_retained_native[book] += cover_retained
        pending_sweep_native[book] += disp["pending"]
        swept_usdt = disp["swept_usdt"]
        if swept_usdt > 0 and _is_premium_proceeds_pool_excluded(row):
            # Manual / unlabeled: native+quote only in excluded buckets (avoid Sold double-count).
            excluded_swept_quote[book] += swept_usdt
            if disp["swept_native"] > 0:
                excluded_swept_native[book] += disp["swept_native"]
        else:
            swept_native_ref[book] += disp["swept_native"]
            if swept_usdt > 0:
                swept_quote_proceeds[book] += swept_usdt
            if disp.get("from_itm_fold"):
                if disp["swept_native"] > 0:
                    folded_swept_native[book] += disp["swept_native"]
                if swept_usdt > 0:
                    folded_swept_quote[book] += swept_usdt
    if not any_row:
        return None
    return {
        "held_native": held_native,
        "loss_native": loss_native,
        "cover_retained_native": cover_retained_native,
        "pending_sweep_native": pending_sweep_native,
        "swept_native_ref": swept_native_ref,
        "swept_quote_proceeds": swept_quote_proceeds,
        "folded_swept_native": folded_swept_native,
        "folded_swept_quote": folded_swept_quote,
        "excluded_swept_native": excluded_swept_native,
        "excluded_swept_quote": excluded_swept_quote,
    }


def _summarize_profit_disposition(
    disposition: dict[str, Any],
    *,
    spot_index: dict[str, Decimal],
    fill_stats: dict[str, dict[str, str]] | None,
) -> dict[str, Any]:
    spot_held = dict(disposition["held_native"])
    spot_pending = dict(disposition["pending_sweep_native"])
    spot_sold_quote: dict[str, Decimal] = {}
    spot_sold: dict[str, Decimal] = {}
    for book in ("BTC", "ETH"):
        held = disposition["held_native"].get(book, Decimal("0"))
        pending = disposition["pending_sweep_native"].get(book, Decimal("0"))
        journal_sold = disposition["swept_native_ref"].get(book, Decimal("0"))
        journal_quote = disposition["swept_quote_proceeds"].get(book, Decimal("0"))
        excluded_native = disposition["excluded_swept_native"].get(book, Decimal("0"))
        excluded_quote = disposition["excluded_swept_quote"].get(book, Decimal("0"))
        folded_native = disposition.get("folded_swept_native", {}).get(book, Decimal("0"))
        folded_quote = disposition.get("folded_swept_quote", {}).get(book, Decimal("0"))
        earned = held + pending + journal_sold + excluded_native
        exchange = (fill_stats or {}).get(book) or {}
        display_usdt = to_decimal(exchange.get("display_usdt"))
        display_native = to_decimal(exchange.get("display_native_sold"))
        if display_native > 0 and display_usdt > 0:
            # Exchange premium_sweep fills omit legacy ITM-folded premium (sold via spot exit).
            sold_native = display_native + folded_native
            sold_quote = display_usdt + folded_quote
        else:
            net_usdt = to_decimal(exchange.get("net_usdt"))
            net_native = to_decimal(exchange.get("net_native_sold"))
            sold_native = journal_sold
            if net_native > 0:
                sold_native = net_native
            elif sold_native <= 0 and net_native > 0:
                sold_native = net_native
            if earned > 0 and sold_native > earned and net_native <= 0:
                sold_native = earned
            sold_quote = journal_quote
            if net_usdt > 0:
                sold_quote = net_usdt
            sold_native += excluded_native
            sold_quote += excluded_quote
        spot_sold[book] = sold_native
        spot_sold_quote[book] = sold_quote
        remainder = max(Decimal("0"), earned - sold_native - pending)
        spot_held[book] = remainder
        cover_retained = disposition.get("cover_retained_native", {}).get(book, Decimal("0"))
        if cover_retained > 0:
            spot_held[book] = max(spot_held[book], cover_retained)
    return {
        "spot_held": spot_held,
        "spot_pending": spot_pending,
        "spot_sold_quote": spot_sold_quote,
        "spot_sold": spot_sold,
    }


def total_realized_usdc_from_swap_disposition(
    closed_rows: list[dict[str, Any]],
    *,
    spot_index: dict[str, Decimal],
    fill_stats: dict[str, dict[str, str]] | None = None,
) -> Decimal | None:
    """Total profit = premium swap USDT + unswept native × spot + ITM exit−restore net (+ USDC)."""
    from .spot_restore_ops import sum_itm_spot_exit_net_usdt_for_total_profit

    disposition = _aggregate_profit_disposition(closed_rows)
    itm_net = sum_itm_spot_exit_net_usdt_for_total_profit(closed_rows)
    total = Decimal("0")
    any_book = False
    if disposition:
        summary = _summarize_profit_disposition(disposition, spot_index=spot_index, fill_stats=fill_stats)
        for book in ("BTC", "ETH"):
            swapped = summary["spot_sold_quote"].get(book, Decimal("0"))
            held = summary["spot_held"].get(book, Decimal("0"))
            pending = summary["spot_pending"].get(book, Decimal("0"))
            loss = disposition["loss_native"].get(book, Decimal("0"))
            unswept = held + pending + loss
            spot = spot_index.get(book)
            unswept_usd = unswept * spot if spot is not None and spot > 0 and abs(unswept) > 0 else Decimal("0")
            if swapped > Decimal("0.005") or abs(unswept_usd) >= Decimal("0.005"):
                total += swapped + unswept_usd
                any_book = True
        usdc = disposition["held_native"].get("USDC", Decimal("0"))
        if abs(usdc) >= Decimal("0.005"):
            total += usdc
            any_book = True
    if abs(itm_net) >= Decimal("0.005"):
        total += itm_net
        any_book = True
    return total if any_book else None


def _profit_sweep_display_usdc(
    group: TradeGroup,
    native: Decimal,
    spot: Decimal,
) -> Decimal:
    """Coin profit swapped to USDT uses exchange/fill quote, not live wallet."""
    if native <= 0:
        return native * spot
    sweep = str(group.profit_sweep_status or "").lower()
    swept_usdt = profit_sweep_realized_usdt(group)
    if sweep == "filled" and swept_usdt > 0:
        swept_native = _profit_sweep_filled_native_sold(group, native)
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
    fill_stats: dict[str, dict[str, str]] | None = None,
    open_rows: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
    hedge_lifetime_usdc: Decimal = Decimal("0"),
    hedge_window_usdc: Decimal = Decimal("0"),
) -> dict[str, Any]:
    """Build the same ``summary`` shape as ``bot.report()`` from closed group dicts."""
    realized = [
        row for row in closed_rows if row.get("realized_pnl") is not None and _closed_timestamp_ms(row) is not None
    ]
    unresolved = [row for row in closed_rows if row not in realized]
    if spot_index:
        swap_total = total_realized_usdc_from_swap_disposition(
            realized,
            spot_index=spot_index,
            fill_stats=fill_stats,
        )
        if swap_total is not None:
            total_realized = swap_total
        else:
            total_realized = sum((_row_realized_pnl_usdc(row, spot_index) for row in realized), Decimal("0"))
    else:
        total_realized = sum((_row_realized_pnl_usdc(row, spot_index) for row in realized), Decimal("0"))
    total_realized += hedge_lifetime_usdc
    total_holding = sum((_holding_days(row) for row in realized), Decimal("0"))
    wins = sum(1 for row in realized if _row_realized_pnl_usdc(row, spot_index) > 0)
    realized_count = Decimal(str(len(realized)))
    lifetime_days = _realized_sample_days(realized, open_rows=open_rows, now_ms=now_ms)
    window_rows, window_days_used = _window_rows(realized, window_days)
    if spot_index:
        window_swap_total = total_realized_usdc_from_swap_disposition(
            window_rows,
            spot_index=spot_index,
            fill_stats=fill_stats,
        )
        if window_swap_total is not None:
            window_pnl = window_swap_total
        else:
            window_pnl = sum((_row_realized_pnl_usdc(row, spot_index) for row in window_rows), Decimal("0"))
    else:
        window_pnl = sum((_row_realized_pnl_usdc(row, spot_index) for row in window_rows), Decimal("0"))
    window_pnl += hedge_window_usdc

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
        "hedge_net_pnl_usdc": format_decimal(hedge_lifetime_usdc, 4),
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
    "hedge_net_pnl_usdc",
)


def patch_realized_report_spot_pnl(
    report_payload: dict[str, Any],
    closed_rows: list[dict[str, Any]],
    *,
    spot_index: dict[str, Decimal] | None,
    window_days: int,
    hedge_lifetime_usdc: Decimal = Decimal("0"),
    hedge_window_usdc: Decimal = Decimal("0"),
    fill_stats: dict[str, dict[str, str]] | None = None,
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
        fill_stats=fill_stats,
        hedge_lifetime_usdc=hedge_lifetime_usdc,
        hedge_window_usdc=hedge_window_usdc,
    )
    for key in _SPOT_PATCHED_SUMMARY_KEYS:
        if key in patched:
            summary[key] = patched[key]
