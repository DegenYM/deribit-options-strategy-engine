"""Infer cross-book reallocations for per-book drawdown gates.

Spot swaps (e.g. USDT → BTC) and similar margin moves on one sub-account
often appear as spot ``trade`` rows, not ``transfer`` rows, so
``day_net_flow_*`` stays zero while the stable book shows a phantom
drawdown. Match stable ↔ crypto native balance moves before computing
``day_drawdown_pct``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal

from .utils import safe_div

STABLE_BOOKS = frozenset({"USDC", "USDT", "USDE"})


def cross_book_flow_adjustments_native(
    *,
    per_book_native_equities: dict[str, Decimal],
    per_book_native_day_start: dict[str, Decimal],
    day_net_flow_native_by_book: dict[str, Decimal],
    day_net_flow_usdc_by_book: dict[str, Decimal],
    index_price_by_book: dict[str, Decimal],
    min_match_usdc: Decimal = Decimal("10"),
) -> dict[str, Decimal]:
    """Return per-book native flow deltas to fold into drawdown ``net_flow``.

    Negative adjustment on a book means "treat as outbound internal move"
    (lowers ``adjusted_start``). Positive means inbound internal move.
    """
    if not per_book_native_equities:
        return {}

    def _external_flow_native(book: str) -> Decimal:
        if book in STABLE_BOOKS:
            return day_net_flow_usdc_by_book.get(book, Decimal("0"))
        return day_net_flow_native_by_book.get(book, Decimal("0"))

    def _native_delta(book: str) -> Decimal:
        equity = per_book_native_equities.get(book, Decimal("0"))
        start = per_book_native_day_start.get(book, equity)
        return equity - start - _external_flow_native(book)

    def _spot(book: str) -> Decimal:
        if book in STABLE_BOOKS:
            return Decimal("1")
        return index_price_by_book.get(book, Decimal("0"))

    adjustments: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    stable_out = [
        (book, -delta)
        for book in per_book_native_equities
        if book in STABLE_BOOKS and (delta := _native_delta(book)) < 0
    ]
    crypto_in = [
        (book, delta)
        for book in per_book_native_equities
        if book not in STABLE_BOOKS and (delta := _native_delta(book)) > 0
    ]
    _match_cross_book_flows(stable_out, crypto_in, adjustments, _spot, min_match_usdc)

    crypto_out = [
        (book, -delta)
        for book in per_book_native_equities
        if book not in STABLE_BOOKS and (delta := _native_delta(book)) < 0
    ]
    stable_in = [
        (book, delta)
        for book in per_book_native_equities
        if book in STABLE_BOOKS and (delta := _native_delta(book)) > 0
    ]
    _match_cross_book_flows(crypto_out, stable_in, adjustments, _spot, min_match_usdc)

    return {book: amount for book, amount in adjustments.items() if amount != 0}


def _match_cross_book_flows(
    sources: list[tuple[str, Decimal]],
    sinks: list[tuple[str, Decimal]],
    adjustments: dict[str, Decimal],
    spot_fn: Callable[[str], Decimal],
    min_match_usdc: Decimal,
) -> None:
    src_remaining = [(book, amount) for book, amount in sources if amount > 0]
    sink_remaining = [(book, amount) for book, amount in sinks if amount > 0]
    src_remaining.sort(key=lambda item: item[1] * spot_fn(item[0]), reverse=True)
    sink_remaining.sort(key=lambda item: item[1] * spot_fn(item[0]), reverse=True)

    i = 0
    j = 0
    while i < len(src_remaining) and j < len(sink_remaining):
        src_book, src_native = src_remaining[i]
        sink_book, sink_native = sink_remaining[j]
        src_spot = spot_fn(src_book)
        sink_spot = spot_fn(sink_book)
        if src_spot <= 0 or sink_spot <= 0:
            break
        src_usdc = src_native * src_spot
        sink_usdc = sink_native * sink_spot
        if src_usdc < min_match_usdc:
            i += 1
            continue
        if sink_usdc < min_match_usdc:
            j += 1
            continue
        match_usdc = min(src_usdc, sink_usdc)
        match_src_native = safe_div(match_usdc, src_spot)
        match_sink_native = safe_div(match_usdc, sink_spot)

        adjustments[src_book] -= match_src_native
        adjustments[sink_book] += match_sink_native

        src_remaining[i] = (src_book, src_native - match_src_native)
        sink_remaining[j] = (sink_book, sink_native - match_sink_native)
        if src_remaining[i][1] <= 0:
            i += 1
        if sink_remaining[j][1] <= 0:
            j += 1
