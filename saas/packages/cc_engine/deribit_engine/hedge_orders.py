"""Perp hedge order pricing and placement (market vs limit IOC)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import OptionInstrument, OrderBookSnapshot
from .utils import ceil_to_step, floor_to_step, format_decimal, to_decimal

_HEDGE_ORDER_TYPES = frozenset({"market", "limit_ioc"})


def normalize_hedge_order_type(raw: str) -> str:
    value = str(raw or "limit_ioc").strip().lower()
    if value not in _HEDGE_ORDER_TYPES:
        raise ValueError(f"unsupported hedge order type: {value}")
    return value


def resolve_hedge_perp_ioc_limit_price(
    *,
    direction: str,
    book: OrderBookSnapshot,
    instrument: OptionInstrument,
    max_slippage_pct: Decimal,
) -> tuple[Decimal | None, str | None]:
    """Return (limit_price, skip_reason) for an aggressive IOC hedge limit."""
    is_buy = direction.lower() == "buy"
    mark = book.mark_price if book.mark_price > 0 else book.index_price
    slippage = max(Decimal("0"), to_decimal(max_slippage_pct))

    if is_buy:
        ref = book.best_ask_price if book.best_ask_price > 0 else mark
        if ref <= 0:
            return None, "hedge_book_unavailable"
        if slippage > 0 and mark > 0:
            cap = mark * (Decimal("1") + slippage)
            if book.best_ask_price > 0 and book.best_ask_price > cap:
                return None, "hedge_slippage_exceeded"
            ref = min(ref, cap)
        price = ceil_to_step(ref, instrument.tick_size_for_price(ref))
        return price, None

    ref = book.best_bid_price if book.best_bid_price > 0 else mark
    if ref <= 0:
        return None, "hedge_book_unavailable"
    if slippage > 0 and mark > 0:
        floor = mark * (Decimal("1") - slippage)
        if book.best_bid_price > 0 and book.best_bid_price < floor:
            return None, "hedge_slippage_exceeded"
        ref = max(ref, floor)
    price = floor_to_step(ref, instrument.tick_size_for_price(ref))
    return price, None


def place_hedge_perp_order(
    client: Any,
    *,
    hedge_order_type: str,
    hedge_limit_slippage_pct: Decimal,
    book: OrderBookSnapshot,
    instrument: OptionInstrument,
    direction: str,
    instrument_name: str,
    amount: Decimal | str,
    label: str,
    reduce_only: bool,
) -> dict[str, Any]:
    """Place a perp hedge order using market or limit IOC."""
    order_type = normalize_hedge_order_type(hedge_order_type)
    if order_type == "market":
        return client.place_order(
            direction=direction,
            instrument_name=instrument_name,
            amount=amount,
            label=label,
            order_type="market",
            reduce_only=reduce_only,
        )

    limit_price, skip_reason = resolve_hedge_perp_ioc_limit_price(
        direction=direction,
        book=book,
        instrument=instrument,
        max_slippage_pct=hedge_limit_slippage_pct,
    )
    if limit_price is None or limit_price <= 0:
        return {"skipped": True, "reason": skip_reason or "hedge_limit_price_unavailable"}

    return client.place_order(
        direction=direction,
        instrument_name=instrument_name,
        amount=amount,
        label=label,
        order_type="limit",
        price=limit_price,
        time_in_force="immediate_or_cancel",
        reduce_only=reduce_only,
    )


def hedge_order_action_meta(
    *,
    hedge_order_type: str,
    book: OrderBookSnapshot,
    instrument: OptionInstrument,
    direction: str,
    hedge_limit_slippage_pct: Decimal,
) -> dict[str, str]:
    """Preview metadata for dry-run hedge actions."""
    order_type = normalize_hedge_order_type(hedge_order_type)
    meta: dict[str, str] = {"hedge_order_type": order_type}
    if order_type == "market":
        return meta
    limit_price, skip_reason = resolve_hedge_perp_ioc_limit_price(
        direction=direction,
        book=book,
        instrument=instrument,
        max_slippage_pct=hedge_limit_slippage_pct,
    )
    if limit_price is not None and limit_price > 0:
        meta["hedge_limit_price"] = format_decimal(limit_price, 8)
    if skip_reason:
        meta["hedge_skip_reason"] = skip_reason
    return meta
