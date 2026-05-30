"""Entry pacing gates shared by scanner, entry, and portfolio snapshot."""

from __future__ import annotations

from .models import TradeGroup


def last_entry_timestamp_ms_by_book(groups: list[TradeGroup]) -> dict[str, int]:
    """Most recent ``entry_timestamp_ms`` per collateral book across all groups."""
    out: dict[str, int] = {}
    for group in groups:
        ts = int(group.entry_timestamp_ms or 0)
        if ts <= 0:
            continue
        book = group.collateral_book()
        out[book] = max(out.get(book, 0), ts)
    return out


def entry_cooldown_active(
    *,
    book: str,
    last_entry_by_book: dict[str, int],
    now_ms: int,
    cooldown_minutes: int,
) -> bool:
    """Return True when ``book`` is still inside the post-entry cooldown window."""
    if cooldown_minutes <= 0:
        return False
    last_ms = last_entry_by_book.get(book.upper(), 0)
    if last_ms <= 0:
        return False
    return now_ms - last_ms < cooldown_minutes * 60 * 1000


def open_group_count_for_book(
    groups: list[TradeGroup],
    book: str,
    *,
    strategy: str | None = None,
) -> int:
    book_u = book.upper()
    count = 0
    for group in groups:
        if group.status != "open":
            continue
        if group.collateral_book() != book_u:
            continue
        if strategy is not None and (group.strategy or "").strip() != strategy:
            continue
        count += 1
    return count
