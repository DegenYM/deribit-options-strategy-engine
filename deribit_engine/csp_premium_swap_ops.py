"""Cash-secured put premium → native spot swap helpers.

When ``COVERED_CALL_CSP_PREMIUM_TARGET=spot`` the engine converts the *realized*
CSP premium into native coin **after the put is closed** (expiry or buyback) —
not at entry. Realized = max(0, entry credit − close debit − close fee).

ITM cover restore (buy spot to refill cover) is a separate path and must finish
or be skipped before premium is swapped, so both do not fight for USDC.
"""

from __future__ import annotations

from decimal import Decimal

from .models import TradeGroup
from .utils import to_decimal

#: Statuses that mean the swap is done and must not be retried.
CSP_PREMIUM_SWAP_TERMINAL: frozenset[str] = frozenset({"filled", "skipped"})
#: Leftover below this (USDC) counts as done — soft floor before exchange min-lot checks.
CSP_PREMIUM_SWAP_DUST_USDC = Decimal("1")
#: Reason tag when remaining premium cannot buy even one exchange min lot.
CSP_PREMIUM_SWAP_DUST_TAG = "dust_below_min_omitted"


def csp_premium_swap_target_is_spot(premium_target: str) -> bool:
    """True when the CSP premium should be swapped into native spot."""
    return str(premium_target or "").strip().lower() == "spot"


def csp_premium_group_is_closed(group: TradeGroup) -> bool:
    return str(group.status or "").lower() == "closed"


def csp_premium_waiting_cover_restore(group: TradeGroup) -> bool:
    """True while an ITM assignment cover buy still needs USDC."""
    return str(group.spot_restore_status or "").lower() in {"pending", "submitted"}


def csp_premium_realized_usdc(group: TradeGroup) -> Decimal:
    """USDC premium kept after the put is closed; 0 while still open.

    ``entry_credit`` already nets the entry fee. Subtract the close debit (buyback
    or settlement) and close fee so only money actually kept is swapped.
    """
    if not csp_premium_group_is_closed(group):
        return Decimal("0")
    credit = group.entry_credit
    if credit is None or credit <= 0:
        return Decimal("0")
    close_debit = group.realized_close_debit
    if close_debit is None:
        close_debit = Decimal("0")
    close_fee = group.realized_close_fee or Decimal("0")
    kept = credit - max(close_debit, Decimal("0")) - max(close_fee, Decimal("0"))
    return kept if kept > 0 else Decimal("0")


def csp_premium_net_usdc(group: TradeGroup) -> Decimal:
    """Alias for realized premium (swap budget). Prefer :func:`csp_premium_realized_usdc`."""
    return csp_premium_realized_usdc(group)


def csp_premium_swap_spent_usdc(group: TradeGroup) -> Decimal:
    """Cumulative USDC already spent on premium→spot fills for this child."""
    spent = group.csp_premium_swap_amount
    if spent is None or spent <= 0:
        return Decimal("0")
    # Before the first fill, schedule used to stash the *target* premium in
    # ``csp_premium_swap_amount``. Treat a pending/submitted row whose amount
    # still equals the full realized premium as "nothing spent yet".
    status = str(group.csp_premium_swap_status or "").lower()
    premium = csp_premium_realized_usdc(group)
    if status in {"pending", "submitted", ""} and premium > 0 and spent == premium:
        return Decimal("0")
    return spent


def csp_premium_swap_remaining_usdc(group: TradeGroup) -> Decimal:
    """USDC still allowed to swap; never exceeds unpaid realized premium."""
    remaining = csp_premium_realized_usdc(group) - csp_premium_swap_spent_usdc(group)
    if remaining <= CSP_PREMIUM_SWAP_DUST_USDC:
        return Decimal("0")
    return remaining


def csp_premium_leftover_usdc(group: TradeGroup) -> Decimal:
    """Unspent realized premium, including dust below the retry floor."""
    leftover = csp_premium_realized_usdc(group) - csp_premium_swap_spent_usdc(group)
    return leftover if leftover > 0 else Decimal("0")


def csp_premium_disposition_split(group: TradeGroup) -> dict[str, Decimal | str] | None:
    """Split closed CSP PnL into leftover USDC vs native already bought.

    Used by Total profit / Profit composition so swapped premium sits on BTC/ETH
    Remaining and unswept dust stays on USDC Remaining.
    """
    if not group.is_cash_secured_group():
        return None
    leftover = csp_premium_leftover_usdc(group)
    native = group.csp_premium_swap_native
    if native is None or native <= 0:
        native = Decimal("0")
    spot_book = str(group.currency or "").upper()
    if spot_book not in {"BTC", "ETH"}:
        spot_book = ""
    if leftover <= 0 and native <= 0:
        pnl = group.realized_pnl
        if pnl is None or pnl == 0:
            return None
        leftover = pnl
        native = Decimal("0")
        spot_book = ""
    return {
        "remaining_usdc": leftover,
        "spot_book": spot_book if native > 0 else "",
        "spot_native": native,
    }


def csp_premium_swap_ready(group: TradeGroup) -> tuple[bool, str]:
    """Whether a closed CSP child may swap realized premium into spot."""
    if not group.is_cash_secured_group():
        return False, "not_cash_secured"
    status = str(group.csp_premium_swap_status or "").lower()
    if status in CSP_PREMIUM_SWAP_TERMINAL:
        return False, f"already_{status}"
    if status == "submitted":
        return False, "already_submitted"
    if not csp_premium_group_is_closed(group):
        return False, "not_closed_yet"
    if csp_premium_waiting_cover_restore(group):
        return False, "waiting_cover_restore"
    realized = csp_premium_realized_usdc(group)
    if realized <= 0:
        return False, "no_realized_premium"
    if csp_premium_swap_remaining_usdc(group) <= 0:
        return False, "premium_already_swapped"
    return True, ""


def csp_premium_swap_order_label(order_label_prefix: str, group: TradeGroup) -> str:
    prefix = str(order_label_prefix or "trial").strip() or "trial"
    return f"{prefix}-csp-premium-swap-{group.currency.lower()}-{group.group_id}"


def schedule_csp_premium_swap(group: TradeGroup, *, reason: str) -> Decimal:
    """Queue a premium→spot swap on the CSP child; returns remaining USDC to swap.

    A no-op (returning ``0``) when the swap is already terminal / in flight, the
    put is still open, cover restore still needs USDC, or there is no realized
    premium. A prior ``failed`` swap is rescheduled to pending.
    ``csp_premium_swap_amount`` tracks cumulative spent and is not reset here.
    """
    status = str(group.csp_premium_swap_status or "").lower()
    if status in CSP_PREMIUM_SWAP_TERMINAL or status == "submitted":
        return Decimal("0")
    if not csp_premium_group_is_closed(group):
        return Decimal("0")
    if csp_premium_waiting_cover_restore(group):
        return Decimal("0")
    remaining = csp_premium_swap_remaining_usdc(group)
    if remaining <= 0:
        if csp_premium_realized_usdc(group) > 0:
            group.csp_premium_swap_status = "filled"
        else:
            group.csp_premium_swap_status = "skipped"
            if not group.csp_premium_swap_reason:
                group.csp_premium_swap_reason = reason or "no_realized_premium"
        return Decimal("0")
    group.csp_premium_swap_status = "pending"
    group.csp_premium_swap_reason = reason
    return remaining


def apply_csp_premium_swap_fill(
    group: TradeGroup,
    *,
    spent_usdc: Decimal,
    native_bought: Decimal,
) -> Decimal:
    """Accumulate a fill; return remaining premium after applying it."""
    spent = max(spent_usdc, Decimal("0"))
    native = max(native_bought, Decimal("0"))
    prior = csp_premium_swap_spent_usdc(group)
    total = prior + spent
    group.csp_premium_swap_amount = total
    if native > 0:
        group.csp_premium_swap_native = max(group.csp_premium_swap_native, Decimal("0")) + native
    premium = csp_premium_realized_usdc(group)
    leftover = premium - total if premium > total else Decimal("0")
    remaining = Decimal("0") if leftover <= CSP_PREMIUM_SWAP_DUST_USDC else leftover
    if remaining <= 0:
        group.csp_premium_swap_status = "filled"
    else:
        group.csp_premium_swap_status = "pending"
    return remaining


def is_csp_premium_swap_below_min_notional(reason: str | BaseException | None) -> bool:
    """True when sizing failed because spend cannot buy one exchange min lot."""
    text = str(reason or "").lower()
    return "below minimum notional" in text or "below min notional" in text


def mark_csp_premium_swap_dust_complete(
    group: TradeGroup,
    *,
    reason: str = "",
) -> None:
    """Omit sub-min remainder — keep leftover USDC, do not retry forever.

    Matches cover-restore dust policy: never round up past the remaining premium.
    If nothing was bought yet, status is ``skipped``; otherwise ``filled``.
    """
    tag = CSP_PREMIUM_SWAP_DUST_TAG
    native = max(group.csp_premium_swap_native or Decimal("0"), Decimal("0"))
    spent = csp_premium_swap_spent_usdc(group)
    if native > 0 or spent > 0:
        group.csp_premium_swap_status = "filled"
    else:
        group.csp_premium_swap_status = "skipped"
    detail = str(reason or "").strip()
    prior = str(group.csp_premium_swap_reason or "").strip()
    parts: list[str] = []
    if prior:
        parts.append(prior)
    if detail and detail not in prior:
        parts.append(detail)
    if tag not in ";".join(parts):
        parts.append(tag)
    group.csp_premium_swap_reason = ";".join(parts)


def csp_premium_swap_base_filled_from_trades(trades: list[dict] | None) -> Decimal:
    """Sum native coin bought across the swap's buy fills."""
    if not trades:
        return Decimal("0")
    total = Decimal("0")
    for trade in trades:
        if str(trade.get("direction") or "").lower() != "buy":
            continue
        amount = to_decimal(trade.get("amount"))
        if amount > 0:
            total += amount
    return total
