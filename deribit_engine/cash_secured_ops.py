"""ITM covered-call → cash-secured put helpers (wheel conversion)."""

from __future__ import annotations

from decimal import Decimal

from .models import TradeGroup
from .spot_exit_ops import spot_exit_filled_native, spot_exit_quote_currency, spot_exit_realized_usdt
from .utils import align_option_order_amount, ceil_option_order_amount

CSP_ABORT_RESTORE_REASON = "operator_csp_abort_restore"


def cash_secured_strike_bounds(
    original_strike: Decimal,
    floor_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """Inclusive strike window: original ITM strike down to ``(1 − floor_pct)``."""
    if original_strike <= 0:
        return Decimal("0"), Decimal("0")
    floor = max(Decimal("0"), min(floor_pct, Decimal("1")))
    return original_strike * (Decimal("1") - floor), original_strike


def cash_secured_cover_complete(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> bool:
    """True when ITM cover is already back (parent and/or CSP-child restore)."""
    from .spot_restore_ops import unrestored_spot_exit_native, wheel_spot_restore_filled_native

    if unrestored_spot_exit_native(group, groups=groups) > Decimal("1e-8"):
        return False
    if wheel_spot_restore_filled_native(group, groups) > 0:
        return True
    restore = str(group.spot_restore_status or "").lower()
    return restore == "filled" and (group.spot_restore_amount > 0 or bool(group.spot_restore_order_id))


def cash_secured_target_native(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> Decimal:
    """Native to recover via CSP: unrestored cover, else sold size, capped at cover.

    When parent or CSP-child restore already reconstituted cover, return 0 so the
    engine does not sell or roll another put.
    """
    from .spot_restore_ops import (
        covered_call_cover_native,
        unrestored_spot_exit_native,
        wheel_spot_restore_filled_native,
    )

    cover = covered_call_cover_native(group)
    unrestored = unrestored_spot_exit_native(group, groups=groups)
    sold = spot_exit_filled_native(group)
    if unrestored > 0:
        raw = unrestored
    elif wheel_spot_restore_filled_native(group, groups) > 0 or cash_secured_cover_complete(group, groups):
        return Decimal("0")
    else:
        raw = sold if sold > 0 else cover
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


def itm_sold_ready_for_cash_secured(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> tuple[bool, str]:
    """Whether a closed covered call may fund a cash-secured put.

    After an OTM CSP expires, the same parent may sell another put (roll) as
    long as cover was not restored and no child is still open. Pass ``groups``
    so live manage can see the child book; without it, ``entered`` still blocks.
    """
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
    if status_csp == "skipped":
        reason = str(group.cash_secured_reason or "").lower()
        if reason not in {"operator_cancelled", "ioc_unfilled"}:
            return False, "already_skipped"
    if status_csp == "submitted":
        return False, "already_submitted"
    if cash_secured_cover_complete(group, groups):
        return False, "cover_restored"
    if groups is not None:
        children = cash_secured_children(groups, group)
        if any(str(child.status or "").lower() == "open" for child in children):
            return False, "child_open"
        blocked, block_why = cash_secured_roll_blocked(group, groups)
        if blocked:
            return False, block_why
        if (status_csp == "entered" or group.cash_secured_group_id) and not children:
            return False, "already_entered"
    else:
        if status_csp == "entered":
            return False, "already_entered"
        if group.cash_secured_group_id:
            return False, "child_already_linked"
    if not itm_group_treat_as_usdc_for_csp(group):
        return False, "spot_exit_not_usdc"
    if spot_exit_realized_usdt(group) <= 0:
        return False, "no_usdc_proceeds"
    if spot_exit_filled_native(group) <= 0 and group.quantity <= 0:
        return False, "no_sold_native"
    if cash_secured_target_native(group, groups) <= 0:
        return False, "cover_restored"
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


def cash_secured_put_intrinsic_usdc(
    *,
    index_price: Decimal,
    strike: Decimal,
    quantity: Decimal,
) -> Decimal:
    """USDC intrinsic for a linear short put position (``max(K−S,0) × qty``)."""
    if index_price <= 0 or strike <= 0 or quantity <= 0:
        return Decimal("0")
    return max(strike - index_price, Decimal("0")) * quantity


def cash_secured_put_time_value_usdc(
    *,
    index_price: Decimal,
    strike: Decimal,
    quantity: Decimal,
    current_debit: Decimal,
) -> Decimal:
    """Ask close cost above intrinsic; never negative."""
    intrinsic = cash_secured_put_intrinsic_usdc(
        index_price=index_price,
        strike=strike,
        quantity=quantity,
    )
    debit = max(current_debit, Decimal("0"))
    return max(debit - intrinsic, Decimal("0"))


def cash_secured_self_assign_ready(
    *,
    index_price: Decimal,
    strike: Decimal,
    quantity: Decimal,
    current_debit: Decimal,
    dte_days: Decimal,
    itm_buffer_pct: Decimal,
    max_dte: Decimal,
    max_tv_pct: Decimal,
) -> tuple[bool, str]:
    """Whether closing the put + buying spot is cheap enough to self-assign.

    European CSP does not assign on an intra-period dip. Self-assign is allowed
    only when the put is ITM **and** either near expiry or the remaining time
    value is thin vs intrinsic (so buy-back ≈ assignment economics).

    Callers must also pass :func:`cash_secured_self_assign_liquidity_ok` — short
    DTE books are often too wide/thin to take the ask safely.
    """
    if not cash_secured_put_is_itm(
        index_price=index_price,
        strike=strike,
        buffer_pct=itm_buffer_pct,
    ):
        return False, "not_itm"
    if current_debit <= 0:
        return False, "no_debit"
    intrinsic = cash_secured_put_intrinsic_usdc(
        index_price=index_price,
        strike=strike,
        quantity=quantity,
    )
    if intrinsic <= 0:
        return False, "no_intrinsic"
    tv = cash_secured_put_time_value_usdc(
        index_price=index_price,
        strike=strike,
        quantity=quantity,
        current_debit=current_debit,
    )
    dte = max(dte_days, Decimal("0"))
    if max_dte >= 0 and dte <= max_dte:
        return True, "near_expiry"
    tv_cap = max(Decimal("0"), max_tv_pct)
    if tv_cap > 0 and tv <= intrinsic * tv_cap:
        return True, "thin_time_value"
    return False, "time_value_fat"


def cash_secured_self_assign_liquidity_ok(
    *,
    spread_ratio: Decimal,
    best_bid_price: Decimal,
    best_ask_price: Decimal,
    best_ask_amount: Decimal,
    quantity: Decimal,
    max_spread_ratio: Decimal,
    min_ask_amount: Decimal | None = None,
) -> tuple[bool, str]:
    """Block self-assign when the put book is too wide or too thin to lift safely.

    Short-dated CSP books often quote huge spreads; buying that ask crystallizes
    a worse price than waiting for European settlement. Require a two-sided book,
    a capped spread, and enough ask size to cover our close quantity.
    """
    if best_bid_price <= 0 or best_ask_price <= 0 or best_ask_price < best_bid_price:
        return False, "no_two_sided_book"
    cap = max(Decimal("0"), max_spread_ratio)
    if cap > 0 and spread_ratio > cap:
        return False, "spread_too_wide"
    need = quantity if min_ask_amount is None else max(min_ask_amount, Decimal("0"))
    if need > 0 and best_ask_amount < need:
        return False, "ask_size_thin"
    return True, "ok"


def cash_secured_active_roll_tv_ratio(
    *,
    index_price: Decimal,
    strike: Decimal,
    quantity: Decimal,
    current_debit: Decimal,
    entry_credit: Decimal,
) -> Decimal:
    """Remaining time value as a fraction of the “still worth rolling” yardstick.

    Numerator is :func:`cash_secured_put_time_value_usdc` (ask close cost above
    intrinsic; OTM ⇒ TV ≈ close debit). Denominator is
    ``max(original credit, intrinsic + TV)`` so:

    * OTM: ratio = remaining close cost / original credit when credit still
      exceeds TV (if we used only intrinsic+TV the OTM ratio would always be 1).
    * When the mark-up makes TV > credit, the ratio caps at 1.

    Active roll wants this ratio **fat** (default ≥ 0.25). That is the opposite
    of self-assign, which fires when TV is *thin* vs intrinsic.
    """
    tv = cash_secured_put_time_value_usdc(
        index_price=index_price,
        strike=strike,
        quantity=quantity,
        current_debit=current_debit,
    )
    intrinsic = cash_secured_put_intrinsic_usdc(
        index_price=index_price,
        strike=strike,
        quantity=quantity,
    )
    denom = max(max(entry_credit, Decimal("0")), intrinsic + tv)
    if denom <= 0:
        return Decimal("0")
    return tv / denom


def cash_secured_active_roll_dte_reason(
    *,
    dte_days: Decimal,
    min_dte: Decimal,
    max_dte: Decimal,
) -> str:
    """Empty when current DTE is inside the active-roll window (``min < dte ≤ max``)."""
    if dte_days <= min_dte:
        return "dte_too_short"
    if max_dte >= 0 and dte_days > max_dte:
        return "dte_out_of_window"
    return ""


def cash_secured_active_roll_fee_edge(
    *,
    new_credit: Decimal,
    close_debit: Decimal,
    close_fee: Decimal,
    open_fee: Decimal,
    min_net_usdc: Decimal,
) -> tuple[Decimal, bool]:
    """Net USDC vs holding: ``new_credit − close_debit − close_fee − open_fee``.

    Caller prices the close at the **ask** (buy-to-close) and the replacement
    at the **bid** (IOC sell, same as CSP entry). ``ok`` iff
    ``net − min_net_usdc ≥ 0`` (strictly non-negative after the dust floor).
    Live active-roll now gates on daily yield instead; this helper remains for
    tests and diagnostics.
    """
    net = new_credit - close_debit - close_fee - open_fee
    return net, net >= max(min_net_usdc, Decimal("0"))


def cash_secured_active_roll_daily_usdc(*, credit: Decimal, dte_days: Decimal) -> Decimal:
    """USDC per remaining day. Zero when credit or DTE is not positive."""
    if credit <= 0 or dte_days <= 0:
        return Decimal("0")
    return credit / dte_days


def cash_secured_active_roll_daily_beats_hold(
    *,
    hold_credit: Decimal,
    hold_dte: Decimal,
    new_credit: Decimal,
    new_dte: Decimal,
    switch_fees: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal, bool]:
    """Whether replacement daily yield beats holding remaining TV.

    Hold daily = close-ask remaining / remaining DTE. Roll daily =
    (new bid credit − switch fees) / new DTE. Same or earlier expiry is
    allowed; the only economic gate is ``roll_daily > hold_daily``.
    """
    hold_daily = cash_secured_active_roll_daily_usdc(credit=hold_credit, dte_days=hold_dte)
    roll_daily = cash_secured_active_roll_daily_usdc(
        credit=new_credit - max(switch_fees, Decimal("0")),
        dte_days=new_dte,
    )
    return hold_daily, roll_daily, roll_daily > hold_daily


def cash_secured_last_active_roll_child(
    parent: TradeGroup,
    groups: list[TradeGroup],
) -> TradeGroup | None:
    """Most recently closed CSP child that was bought back for an active roll."""
    closed: list[TradeGroup] = []
    for child in cash_secured_children(groups, parent):
        if str(child.status or "").lower() != "closed":
            continue
        if str(child.close_reason or "").lower() != "csp_active_roll":
            continue
        closed.append(child)
    if not closed:
        return None
    return max(closed, key=lambda item: int(item.closed_timestamp_ms or 0))


def cash_secured_hold_credit_dte_from_closed_roll(child: TradeGroup) -> tuple[Decimal, Decimal]:
    """Remaining TV and DTE at the active-roll close, for retry daily-yield gates."""
    from .utils import dte_days, ms_to_datetime

    qty = child.quantity if child.quantity > 0 else Decimal("0")
    close_px = child.short_close_average_price
    if close_px > 0 and qty > 0:
        hold_credit = close_px * qty
    else:
        debit = child.realized_close_debit if child.realized_close_debit is not None else Decimal("0")
        fee = child.realized_close_fee or Decimal("0")
        hold_credit = max(debit - fee, Decimal("0"))
    closed_at = ms_to_datetime(child.closed_timestamp_ms) if child.closed_timestamp_ms else None
    hold_dte = dte_days(child.expiration_timestamp_ms, now=closed_at)
    return hold_credit, hold_dte


def cash_secured_later_expiry_in_window(
    *,
    expiration_timestamp_ms: int,
    current_expiry_ms: int,
    instrument_name: str,
    current_instrument: str,
    dte_days: Decimal,
    dte_min: Decimal,
    dte_max: Decimal,
    strike: Decimal,
    min_strike: Decimal,
    max_strike: Decimal,
) -> bool:
    """True when ``instrument`` is a later expiry still inside the CSP strike/DTE window."""
    if not instrument_name or instrument_name == current_instrument:
        return False
    if expiration_timestamp_ms <= current_expiry_ms:
        return False
    if dte_days < dte_min or dte_days > dte_max:
        return False
    if strike < min_strike or strike > max_strike:
        return False
    return True


def cash_secured_cover_unrestored(group: TradeGroup) -> Decimal:
    """Native still needed to refill cover after a CSP assignment buy."""
    target = group.quantity if group.quantity > 0 else group.covered_underlying_quantity
    if target <= 0:
        return Decimal("0")
    restored = max(group.spot_restore_amount, Decimal("0"))
    return max(target - restored, Decimal("0"))


def cash_secured_children(state_groups: list[TradeGroup], parent: TradeGroup) -> list[TradeGroup]:
    """CSP groups funded by ``parent``, latest last."""
    pid = str(parent.group_id or "").strip()
    known = {str(item).strip() for item in (parent.cash_secured_group_ids or []) if str(item).strip()}
    latest = str(parent.cash_secured_group_id or "").strip()
    if latest:
        known.add(latest)
    out: list[TradeGroup] = []
    seen: set[str] = set()
    for group in state_groups:
        gid = str(group.group_id or "").strip()
        if not gid or gid == pid or gid in seen:
            continue
        linked = str(group.cash_secured_from_group_id or "").strip() == pid or gid in known
        if not linked:
            continue
        if group.is_cash_secured_group() or gid in known:
            seen.add(gid)
            out.append(group)
    return out


def cash_secured_child_expired_itm(child: TradeGroup) -> bool:
    """Closed short put expired ITM when close index is below strike."""
    if str(child.status or "").lower() != "closed":
        return False
    index_price = child.close_index_usd
    strike = child.short_strike
    if index_price <= 0 or strike <= 0:
        return False
    return index_price < strike


def cash_secured_roll_blocked(parent: TradeGroup, groups: list[TradeGroup]) -> tuple[bool, str]:
    """Stop selling/rolling puts once cover is back, or the operator closed the last put.

    Also stop after an ITM CSP expiry (assignment path owns the cover buy).
    """
    children = cash_secured_children(groups, parent)
    for child in children:
        restore = str(child.spot_restore_status or "").lower()
        if restore in {"submitted", "pending"}:
            return True, "child_restore_in_flight"
        if restore == "filled" and (child.spot_restore_amount > 0 or child.spot_restore_order_id):
            return True, "cover_restored"
        if cash_secured_child_expired_itm(child):
            return True, "child_expired_itm"
    if cash_secured_cover_complete(parent, groups):
        return True, "cover_restored"
    if any(str(child.close_reason or "").lower() == "manual_close" for child in children):
        return True, "operator_closed_child"
    return False, ""


def cash_secured_wheel_realized_pnl(parent: TradeGroup, groups: list[TradeGroup]) -> Decimal:
    """Call credit (ITM cover sale) or parent realized PnL, plus every closed CSP child."""
    if str(parent.spot_exit_status or "").lower() == "filled":
        total = parent.entry_credit if parent.entry_credit is not None else Decimal("0")
    else:
        total = parent.realized_pnl if parent.realized_pnl is not None else Decimal("0")
    for child in cash_secured_children(groups, parent):
        if str(child.status or "").lower() != "closed":
            continue
        if child.realized_pnl is None:
            continue
        total += child.realized_pnl
    return total


def cash_secured_cover_restore_order_label(group: TradeGroup, order_label_prefix: str) -> str:
    prefix = str(order_label_prefix or "trial").strip() or "trial"
    return f"{prefix}-csp-restore-{group.currency.lower()}-{group.group_id}"


def cash_secured_child_is_open(state_groups: list[TradeGroup], parent: TradeGroup) -> bool:
    return any(str(group.status or "").lower() == "open" for group in cash_secured_children(state_groups, parent))


def _same_group_id(left: str, right: str) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if a == b:
        return True
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return False


def resolve_csp_abort_restore_pair(
    groups: list[TradeGroup],
    group_id: str,
) -> tuple[TradeGroup | None, TradeGroup | None]:
    """Parent covered-call and its open CSP child, if any, for a force-recover.

    ``group_id`` may be the ITM parent or a CSP child. Returns ``(None, None)``
    when the id is not part of a cash-secured wheel.
    """
    wanted = str(group_id or "").strip()
    if not wanted:
        return None, None
    hit = next((item for item in groups if _same_group_id(item.group_id, wanted)), None)
    if hit is None:
        return None, None
    if hit.is_cash_secured_group() or str(hit.cash_secured_from_group_id or "").strip():
        parent_id = str(hit.cash_secured_from_group_id or "").strip()
        parent = next((item for item in groups if _same_group_id(item.group_id, parent_id)), None)
        open_child = hit if str(hit.status or "").lower() == "open" else None
        if open_child is None and parent is not None:
            open_child = next(
                (child for child in cash_secured_children(groups, parent) if str(child.status or "").lower() == "open"),
                None,
            )
        return parent, open_child
    if hit.is_covered_call_group():
        open_child = next(
            (child for child in cash_secured_children(groups, hit) if str(child.status or "").lower() == "open"),
            None,
        )
        return hit, open_child
    return None, None


def _cash_secured_preview_parent_note(group: TradeGroup, groups: list[TradeGroup]) -> str | None:
    ready, reason = itm_sold_ready_for_cash_secured(group, groups)
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
        note = _cash_secured_preview_parent_note(group, groups)
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
