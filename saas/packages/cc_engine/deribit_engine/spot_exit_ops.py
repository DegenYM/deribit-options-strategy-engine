"""Covered-call ITM / settlement spot-exit helpers (separate from premium profit sweep)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .models import TradeGroup
from .utils import format_decimal, to_decimal
from .wallet_ops import spot_sell_quote_proceeds_from_trades

if TYPE_CHECKING:
    from .client import DeribitClient

LOGGER = logging.getLogger(__name__)


def record_spot_exit_lifetime_proceeds(group: TradeGroup, proceeds: Decimal) -> None:
    """Immutable lifetime USDT from ITM/settlement spot-exit fills."""
    if proceeds <= 0:
        return
    if proceeds > group.spot_exit_quote_proceeds_lifetime:
        group.spot_exit_quote_proceeds_lifetime = proceeds


def spot_exit_order_label(short_label: str) -> str:
    return f"{short_label}-spot-exit"


def is_spot_exit_label(label: str) -> bool:
    return "spot-exit" in str(label or "")


def spot_exit_realized_usdt(group: TradeGroup) -> Decimal:
    lifetime = group.spot_exit_quote_proceeds_lifetime
    if lifetime > 0:
        return lifetime
    quote = group.spot_exit_quote_proceeds
    if quote > 0 and str(group.spot_exit_status or "").lower() == "filled":
        return quote
    return Decimal("0")


def apply_spot_exit_quote_proceeds(
    group: TradeGroup,
    trades: list[dict[str, Any]],
    *,
    cumulative: bool = False,
) -> Decimal:
    proceeds = spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
    if proceeds <= 0:
        return Decimal("0")
    if cumulative:
        group.spot_exit_quote_proceeds += proceeds
    else:
        group.spot_exit_quote_proceeds = proceeds
    record_spot_exit_lifetime_proceeds(group, group.spot_exit_quote_proceeds)
    return proceeds


def _iter_spot_sell_trades(client: DeribitClient, currency: str):
    fetch = getattr(client, "get_user_trades_by_currency", None)
    if not callable(fetch):
        return
    seen: set[Any] = set()

    def _yield(batch_trades: list[dict[str, Any]]):
        for trade in batch_trades:
            trade_id = trade.get("trade_id")
            if trade_id in seen:
                continue
            if trade_id is not None:
                seen.add(trade_id)
            if str(trade.get("direction") or "").lower() != "sell":
                continue
            yield trade

    try:
        recent = fetch(currency, kind="spot", count=1000, historical=False)
        yield from _yield(list(recent.get("trades") or []))
    except Exception:  # noqa: BLE001
        LOGGER.debug("spot_exit: recent trades fetch failed currency=%s", currency, exc_info=True)

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
            LOGGER.debug("spot_exit: historical trades fetch failed currency=%s", currency, exc_info=True)
            break
        trades = list(batch.get("trades") or [])
        if not trades:
            break
        yield from _yield(trades)
        if not batch.get("has_more"):
            break
        last_ts = int(trades[-1].get("timestamp") or 0)
        if last_ts <= 0 or last_ts <= cursor_ts:
            break
        cursor_ts = last_ts + 1


def spot_exit_sell_trades_for_currency(client: DeribitClient, currency: str) -> list[dict[str, Any]]:
    return [t for t in _iter_spot_sell_trades(client, currency) if is_spot_exit_label(str(t.get("label") or ""))]


def spot_exit_fill_stats_for_currency(client: DeribitClient, currency: str) -> dict[str, str]:
    """Exchange VWAP stats for labeled ITM/settlement spot-exit sells only."""
    trades = spot_exit_sell_trades_for_currency(client, currency)
    native = sum((to_decimal(t.get("amount")) for t in trades), Decimal("0"))
    usdt = spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")

    def _avg(quote: Decimal, sold: Decimal) -> str:
        if sold <= 0:
            return "0"
        return format_decimal(quote / sold, 2)

    return {
        "native_sold": format_decimal(native, 8),
        "usdt": format_decimal(usdt, 4),
        "avg_price_usd": _avg(usdt, native),
    }


def spot_exit_fill_stats_by_book(client: DeribitClient) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for currency in ("BTC", "ETH"):
        stats = spot_exit_fill_stats_for_currency(client, currency)
        if to_decimal(stats["native_sold"]) > 0 or to_decimal(stats["usdt"]) > 0:
            out[currency] = stats
    return out


def _spot_exit_amount_tolerance(amount: Decimal) -> Decimal:
    return max(Decimal("1e-8"), amount * Decimal("0.001"))


def spot_exit_attribution_keys_from_groups(groups: list[TradeGroup]) -> tuple[set[str], list[tuple[str, Decimal, int]]]:
    """Order ids and (currency, amount, close_ms) hints for excluding ITM exits from premium unlabeled."""
    order_ids: set[str] = set()
    amount_hints: list[tuple[str, Decimal, int]] = []
    for group in groups:
        if str(group.spot_exit_status or "").lower() != "filled":
            continue
        order_id = str(group.spot_exit_order_id or "").strip()
        if order_id:
            order_ids.add(order_id)
        amount = group.spot_exit_amount
        if amount <= 0:
            continue
        amount_hints.append(
            (
                group.currency.upper(),
                amount,
                int(group.closed_timestamp_ms or 0),
            )
        )
    return order_ids, amount_hints


def trade_matches_spot_exit_attribution(
    trade: dict[str, Any],
    *,
    order_ids: set[str],
    amount_hints: list[tuple[str, Decimal, int]],
    currency: str,
) -> bool:
    """True when an unlabeled sell is attributable to a known filled ITM spot exit."""
    order_id = str(trade.get("order_id") or "").strip()
    if order_id and order_id in order_ids:
        return True
    amount = to_decimal(trade.get("amount"))
    if amount <= 0:
        return False
    ts = int(trade.get("timestamp") or 0)
    book = currency.upper()
    for hint_book, hint_amount, close_ms in amount_hints:
        if hint_book != book:
            continue
        if abs(amount - hint_amount) > _spot_exit_amount_tolerance(hint_amount):
            continue
        if close_ms > 0:
            if ts < close_ms - 86400000:
                continue
            if ts > close_ms + 7 * 86400000:
                continue
        return True
    return False


def filter_unlabeled_trades_excluding_spot_exits(
    trades: list[dict[str, Any]],
    *,
    currency: str,
    groups: list[TradeGroup] | None,
) -> list[dict[str, Any]]:
    if not groups:
        return list(trades)
    order_ids, amount_hints = spot_exit_attribution_keys_from_groups(groups)
    if not order_ids and not amount_hints:
        return list(trades)
    return [
        trade
        for trade in trades
        if not trade_matches_spot_exit_attribution(
            trade,
            order_ids=order_ids,
            amount_hints=amount_hints,
            currency=currency,
        )
    ]


def reconcile_spot_exit_from_exchange(
    group: TradeGroup,
    *,
    client: DeribitClient,
) -> bool:
    """Backfill spot_exit_quote_proceeds from labeled spot-exit sells / order id."""
    if not group.is_covered_call_group():
        return False
    status = str(group.spot_exit_status or "").lower()
    # Include failed: partial fill then cancel still leaves labeled exchange sells.
    if status not in {"filled", "submitted", "pending", "failed"}:
        return False
    if group.spot_exit_quote_proceeds > 0 and status == "filled":
        record_spot_exit_lifetime_proceeds(group, group.spot_exit_quote_proceeds)
        return False

    short_label = str(group.short_label or "").strip()
    label = spot_exit_order_label(short_label) if short_label else ""
    trades: list[dict[str, Any]] = []

    order_id = str(group.spot_exit_order_id or "").strip()
    if order_id:
        try:
            trades = list(client.get_user_trades_by_order(order_id) or [])
            trades = [t for t in trades if str(t.get("direction") or "").lower() == "sell"]
        except Exception:  # noqa: BLE001
            LOGGER.debug(
                "spot_exit reconcile: trades by order failed order=%s group=%s",
                order_id,
                group.group_id,
                exc_info=True,
            )
            trades = []

    if not trades and label:
        for trade in spot_exit_sell_trades_for_currency(client, group.currency.upper()):
            if str(trade.get("label") or "") != label:
                continue
            trades.append(trade)

    if not trades:
        return False

    trades.sort(key=lambda row: int(row.get("timestamp") or 0))
    amount = sum(to_decimal(row.get("amount")) for row in trades)
    proceeds = spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
    last = trades[-1]
    last_order_id = str(last.get("order_id") or "").strip()
    instrument_name = str(last.get("instrument_name") or f"{group.currency.upper()}_USDT")

    group.spot_exit_status = "filled"
    group.spot_exit_instrument_name = instrument_name
    if last_order_id:
        group.spot_exit_order_id = last_order_id
    if amount > 0:
        group.spot_exit_amount = amount
    if proceeds > 0:
        group.spot_exit_quote_proceeds = proceeds
        record_spot_exit_lifetime_proceeds(group, proceeds)
    if not group.spot_exit_reason:
        group.spot_exit_reason = "covered_call_settlement_exit"
    return True


def reconcile_spot_exits_in_groups(
    groups: list[TradeGroup],
    client: DeribitClient,
) -> int:
    repaired = 0
    for group in groups:
        if reconcile_spot_exit_from_exchange(group, client=client):
            repaired += 1
    return repaired


def journal_spot_exit_totals_by_book(groups: list[TradeGroup]) -> dict[str, dict[str, Decimal]]:
    """Sum filled ITM spot-exit native/USDT from journal groups."""
    out: dict[str, dict[str, Decimal]] = {
        "BTC": {"native": Decimal("0"), "usdt": Decimal("0")},
        "ETH": {"native": Decimal("0"), "usdt": Decimal("0")},
    }
    for group in groups:
        if str(group.spot_exit_status or "").lower() != "filled":
            continue
        book = group.currency.upper()
        if book not in out:
            continue
        native = group.spot_exit_amount
        usdt = spot_exit_realized_usdt(group)
        if native > 0:
            out[book]["native"] += native
        if usdt > 0:
            out[book]["usdt"] += usdt
    return out
