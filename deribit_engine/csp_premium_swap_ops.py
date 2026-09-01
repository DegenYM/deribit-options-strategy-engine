"""Cash-secured put premium → native spot swap helpers.

When ``COVERED_CALL_CSP_PREMIUM_TARGET=spot`` the engine converts the *net*
premium a freshly entered CSP collected (USDC fill credit minus entry fee) into
native coin. Only the premium is swapped: the ``strike × qty`` cash reserved to
honour a put assignment is never touched (Deribit already holds it as the short
put's initial margin, so it is excluded from ``available_funds``).
"""

from __future__ import annotations

from decimal import Decimal

from .models import TradeGroup
from .utils import to_decimal

#: Statuses that mean the swap is done and must not be retried.
CSP_PREMIUM_SWAP_TERMINAL: frozenset[str] = frozenset({"filled", "skipped"})


def csp_premium_swap_target_is_spot(premium_target: str) -> bool:
    """True when the CSP premium should be swapped into native spot."""
    return str(premium_target or "").strip().lower() == "spot"


def csp_premium_net_usdc(group: TradeGroup) -> Decimal:
    """Net CSP premium in USDC (entry credit already nets the entry fee)."""
    credit = group.entry_credit
    if credit is None or credit <= 0:
        return Decimal("0")
    return credit


def csp_premium_swap_ready(group: TradeGroup) -> tuple[bool, str]:
    """Whether an entered CSP child may swap its premium into spot."""
    if not group.is_cash_secured_group():
        return False, "not_cash_secured"
    status = str(group.csp_premium_swap_status or "").lower()
    if status in CSP_PREMIUM_SWAP_TERMINAL:
        return False, f"already_{status}"
    if status == "submitted":
        return False, "already_submitted"
    if csp_premium_net_usdc(group) <= 0:
        return False, "no_premium"
    return True, ""


def csp_premium_swap_order_label(order_label_prefix: str, group: TradeGroup) -> str:
    prefix = str(order_label_prefix or "trial").strip() or "trial"
    return f"{prefix}-csp-premium-swap-{group.currency.lower()}-{group.group_id}"


def schedule_csp_premium_swap(group: TradeGroup, *, reason: str) -> Decimal:
    """Queue a premium→spot swap on the CSP child; returns the USDC amount to swap.

    A no-op (returning ``0``) when the swap is already terminal / in flight or the
    child collected no premium. A prior ``failed`` swap is rescheduled to pending.
    """
    status = str(group.csp_premium_swap_status or "").lower()
    if status in CSP_PREMIUM_SWAP_TERMINAL or status == "submitted":
        return Decimal("0")
    amount = csp_premium_net_usdc(group)
    if amount <= 0:
        return Decimal("0")
    group.csp_premium_swap_status = "pending"
    group.csp_premium_swap_reason = reason
    group.csp_premium_swap_amount = amount
    return amount


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
