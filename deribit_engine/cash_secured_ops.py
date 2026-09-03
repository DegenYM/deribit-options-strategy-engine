"""ITM covered-call → cash-secured put helpers (wheel conversion)."""

from __future__ import annotations

from decimal import Decimal

from .models import TradeGroup
from .spot_exit_ops import spot_exit_filled_native, spot_exit_quote_currency, spot_exit_realized_usdt
from .utils import align_option_order_amount, ceil_option_order_amount


def cash_secured_strike_bounds(
    original_strike: Decimal,
    floor_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """Inclusive strike window: original ITM strike down to ``(1 − floor_pct)``."""
    if original_strike <= 0:
        return Decimal("0"), Decimal("0")
    floor = max(Decimal("0"), min(floor_pct, Decimal("1")))
    return original_strike * (Decimal("1") - floor), original_strike


def cash_secured_target_native(group: TradeGroup) -> Decimal:
    """Native to recover via CSP: unrestored cover, else sold size, capped at cover."""
    from .spot_restore_ops import covered_call_cover_native, unrestored_spot_exit_native

    cover = covered_call_cover_native(group)
    unrestored = unrestored_spot_exit_native(group)
    sold = spot_exit_filled_native(group)
    raw = unrestored if unrestored > 0 else (sold if sold > 0 else cover)
    if raw <= 0:
        raw = group.quantity
    if cover > 0:
        return min(raw, cover)
    return raw


def cash_secured_desired_quantity(
    *,
    target_native: Decimal,
    contract_size: Decimal,
    min_trade_amount: Decimal,
    cap: Decimal | None = None,
) -> Decimal:
    """Ceil unrestored cover onto the option grid so assignment can refill cover."""
    return ceil_option_order_amount(
        target_native,
        contract_size,
        min_trade_amount,
        cap=cap,
    )


def cash_secured_quantity(
    *,
    sold_native: Decimal,
    usdc_available: Decimal,
    strike: Decimal,
    contract_size: Decimal,
    min_trade_amount: Decimal,
    cap: Decimal | None = None,
) -> Decimal:
    """Size a USDC linear put so cash covers ``strike × qty`` and the cover target.

    Desired qty is ceiled to the option lot (refill cover). If fees / settlement
    leave slightly too little USDC at this strike, size down instead — the
    scanner should then pick a lower strike that still funds the full lot.
    """
    if sold_native <= 0 or usdc_available <= 0 or strike <= 0:
        return Decimal("0")
    desired = cash_secured_desired_quantity(
        target_native=sold_native,
        contract_size=contract_size,
        min_trade_amount=min_trade_amount,
        cap=cap,
    )
    if desired <= 0:
        return Decimal("0")
    if usdc_available >= desired * strike:
        return desired
    return align_option_order_amount(
        usdc_available / strike,
        contract_size,
        min_trade_amount,
    )


def cash_secured_scan_rank(
    *,
    quantity: Decimal,
    strike: Decimal,
    dte: Decimal,
    net_apr: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Prefer full cover qty, then the highest (closest) strike, then shorter DTE / APR."""
    return (-quantity, -strike, dte, -net_apr)


def itm_group_sold_to_usdc(group: TradeGroup) -> bool:
    return spot_exit_quote_currency(group) == "USDC"


def itm_group_treat_as_usdc_for_csp(group: TradeGroup) -> bool:
    """Filled ITM cover sales fund CSP, including USDT journals after a manual USDC convert."""
    if str(group.spot_exit_status or "").lower() != "filled":
        return False
    return True


def itm_sold_ready_for_cash_secured(group: TradeGroup) -> tuple[bool, str]:
    """Whether a closed covered call may fund a cash-secured put."""
    if not group.is_covered_call_group():
        return False, "not_covered_call"
    if str(group.status or "").lower() != "closed":
        return False, "not_closed"
    status = str(group.spot_exit_status or "").lower()
    if status != "filled":
        return False, "spot_exit_not_filled"
    restore_status = str(group.spot_restore_status or "").lower()
    if restore_status not in {"skipped", "cancelled", "canceled"}:
        if restore_status in {"submitted", "pending", "filled"}:
            if group.spot_restore_amount > 0 or group.spot_restore_order_id:
                return False, "restore_in_flight"
    status_csp = str(group.cash_secured_status or "").lower()
    if status_csp == "entered":
        return False, "already_entered"
    if status_csp == "skipped":
        reason = str(group.cash_secured_reason or "").lower()
        if reason not in {"operator_cancelled", "ioc_unfilled"}:
            return False, "already_skipped"
    if status_csp == "submitted":
        return False, "already_submitted"
    if group.cash_secured_group_id:
        return False, "child_already_linked"
    if not itm_group_treat_as_usdc_for_csp(group):
        return False, "spot_exit_not_usdc"
    if spot_exit_realized_usdt(group) <= 0:
        return False, "no_usdc_proceeds"
    if spot_exit_filled_native(group) <= 0 and group.quantity <= 0:
        return False, "no_sold_native"
    return True, ""


def is_cash_secured_group(group: TradeGroup) -> bool:
    return group.is_cash_secured_group()


def cash_secured_put_is_itm(
    *,
    index_price: Decimal,
    strike: Decimal,
    buffer_pct: Decimal = Decimal("0"),
) -> bool:
    """Short put is ITM when spot is below strike (optional buffer)."""
    if index_price <= 0 or strike <= 0:
        return False
    floor = max(Decimal("0"), min(buffer_pct, Decimal("1")))
    return index_price < strike * (Decimal("1") - floor)


def cash_secured_cover_unrestored(group: TradeGroup) -> Decimal:
    """Native still needed to refill cover after a CSP assignment buy."""
    target = group.quantity if group.quantity > 0 else group.covered_underlying_quantity
    if target <= 0:
        return Decimal("0")
    restored = max(group.spot_restore_amount, Decimal("0"))
    return max(target - restored, Decimal("0"))


def cash_secured_cover_restore_order_label(group: TradeGroup, order_label_prefix: str) -> str:
    prefix = str(order_label_prefix or "trial").strip() or "trial"
    return f"{prefix}-csp-restore-{group.currency.lower()}-{group.group_id}"


def cash_secured_child_is_open(state_groups: list[TradeGroup], parent: TradeGroup) -> bool:
    child_id = str(parent.cash_secured_group_id or "").strip()
    if not child_id:
        return False
    for group in state_groups:
        if str(group.group_id) != child_id:
            continue
        return str(group.status or "").lower() == "open"
    return False


def _cash_secured_preview_parent_note(group: TradeGroup) -> str | None:
    ready, reason = itm_sold_ready_for_cash_secured(group)
    if ready:
        return "ready"
    if not group.is_covered_call_group():
        return None
    if str(group.spot_exit_status or "").lower() == "filled":
        return reason or "itm_sold"
    if str(group.status or "").lower() == "open":
        return "open_covered_call"
    return None


def list_cash_secured_preview_parents(
    groups: list[TradeGroup],
    from_group_id: str | None = None,
) -> list[tuple[TradeGroup, str]]:
    """ITM-sold covered calls to dry-run, or one group when ``from_group_id`` is set.

    ``from_group_id`` may be the parent or the open CSP child. With no id: every
    cover-sold CC (ready or already entered). If none, fall back to open CCs.
    """
    wanted = str(from_group_id or "").strip().lstrip("#")
    if wanted:
        match = next((group for group in groups if str(group.group_id) == wanted), None)
        if match is None:
            return []
        if match.is_cash_secured_group():
            parent_id = str(match.cash_secured_from_group_id or "").strip()
            parent = next((group for group in groups if str(group.group_id) == parent_id), None)
            return [(parent, "from_child")] if parent is not None else []
        return [(match, "from_group")]

    sold: list[tuple[TradeGroup, str]] = []
    open_cc: list[tuple[TradeGroup, str]] = []
    for group in groups:
        note = _cash_secured_preview_parent_note(group)
        if note is None:
            continue
        if note == "open_covered_call":
            open_cc.append((group, note))
            continue
        sold.append((group, note))
    chosen = sold or open_cc
    chosen.sort(
        key=lambda item: (
            item[0].currency,
            int(item[0].closed_timestamp_ms or item[0].entry_timestamp_ms or 0),
            str(item[0].group_id),
        )
    )
    return chosen


def resolve_cash_secured_preview_parent(
    groups: list[TradeGroup],
    from_group_id: str | None = None,
) -> tuple[TradeGroup | None, str]:
    """First parent from :func:`list_cash_secured_preview_parents` (compat helper)."""
    wanted = str(from_group_id or "").strip().lstrip("#")
    rows = list_cash_secured_preview_parents(groups, from_group_id)
    if rows:
        return rows[0]
    if wanted:
        match = next((group for group in groups if str(group.group_id) == wanted), None)
        if match is None:
            return None, "group_not_found"
        if match.is_cash_secured_group():
            return None, "parent_not_found"
    return None, "no_itm_parent"
