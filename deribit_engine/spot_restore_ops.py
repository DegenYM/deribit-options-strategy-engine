"""CLI-driven cover restore after ITM / settlement spot exit (separate from Profit swap)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .exceptions import ExchangeError
from .models import OrderBookSnapshot, TradeGroup
from .utils import (
    align_option_order_amount,
    ceil_option_order_amount,
    ceil_to_step,
    format_decimal,
    is_post_only_reject,
    to_decimal,
)
from .wallet_ops import spot_buy_quote_spent_from_trades

if TYPE_CHECKING:
    from .client import DeribitClient
    from .engine import DeribitOptionTrialBot

LOGGER = logging.getLogger(__name__)
SleepFn = Callable[[float], None]
DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT = Decimal("0.001")
SPOT_RESTORE_OPERATOR_CANCELLED = "operator_cancelled"


def record_spot_restore_lifetime_spent(group: TradeGroup, spent: Decimal) -> None:
    if spent <= 0:
        return
    if spent > group.spot_restore_quote_spent_lifetime:
        group.spot_restore_quote_spent_lifetime = spent


def spot_restore_order_label(group: TradeGroup, order_label_prefix: str) -> str:
    short = str(group.short_label or "").strip()
    if short:
        return f"{short}-spot-restore"
    return f"{order_label_prefix}-spot-restore-{group.currency.lower()}-{group.group_id}"


def is_spot_restore_label(label: str) -> bool:
    return "spot-restore" in str(label or "")


def spot_restore_realized_usdt(group: TradeGroup) -> Decimal:
    lifetime = group.spot_restore_quote_spent_lifetime
    if lifetime > 0:
        return lifetime
    spent = group.spot_restore_quote_spent
    if spent > 0 and str(group.spot_restore_status or "").lower() == "filled":
        return spent
    return Decimal("0")


def covered_call_cover_native(group: TradeGroup) -> Decimal:
    if group.covered_underlying_quantity > 0:
        return group.covered_underlying_quantity
    return group.quantity if group.quantity > 0 else Decimal("0")


def spot_restore_filled_native(group: TradeGroup) -> Decimal:
    status = str(group.spot_restore_status or "").lower()
    if status == "filled":
        return group.spot_restore_amount if group.spot_restore_amount > 0 else Decimal("0")
    if status in {"pending", "submitted"} and group.spot_restore_amount > 0:
        return group.spot_restore_amount
    return Decimal("0")


def wheel_spot_restore_filled_native(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> Decimal:
    """Parent restore plus CSP-child assignment buys that refill the same cover."""
    restored = spot_restore_filled_native(group)
    if not groups or not group.is_covered_call_group():
        return restored
    from .cash_secured_ops import cash_secured_children

    for child in cash_secured_children(groups, group):
        restored += spot_restore_filled_native(child)
    return restored


def wheel_spot_restore_realized_usdt(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> Decimal:
    """Parent restore quote plus CSP-child assignment spend for the same cover."""
    spent = spot_restore_realized_usdt(group)
    if not groups or not group.is_covered_call_group():
        return spent
    from .cash_secured_ops import cash_secured_children

    for child in cash_secured_children(groups, group):
        spent += spot_restore_realized_usdt(child)
    return spent


def _spot_exit_swap_native(group: TradeGroup) -> Decimal:
    """Native actually sold via ITM spot exit (includes partial pending fills)."""
    from .spot_exit_ops import spot_exit_filled_native

    return spot_exit_filled_native(group)


def _spot_restore_entry_fee_native(group: TradeGroup) -> Decimal:
    """Option entry fee in collateral coin (informational; already netted in premium)."""
    fee = group.resolved_entry_fee_collateral()
    if fee is None or fee <= 0:
        return Decimal("0")
    return fee


def _spot_restore_premium_native(group: TradeGroup) -> Decimal:
    """Net entry premium (fill − entry fee). May still sit as native if not fully swapped."""
    from .covered_call_settlement import covered_call_spot_exit_premium_native

    return max(covered_call_spot_exit_premium_native(group), Decimal("0"))


def plan_spot_restore_to_cover(
    group: TradeGroup,
    *,
    settlement_loss: Decimal | None = None,
    groups: list[TradeGroup] | None = None,
) -> dict[str, Decimal | str | bool]:
    """Restore plan to original cover: ``swap + settle − premium``.

    Coin identity after ITM: remaining ≈ cover + premium − settle − swap.
    Buy-to-cover = cover − remaining = swap + settle − premium.

    - **swap**: native *actually filled* on ITM spot exit (partial counts)
    - **settle**: settlement debit (must be known / backfilled)
    - **premium**: net entry premium — subtract because it is still (or was) inventory
      credit. If premium was sold inside swap, this still yields buy ≈ cover.
      If premium was *not* sold (common when spot exit only did cover−settle),
      subtracting avoids buying a second copy of premium on restore.
    - **fee**: shown for info only (already inside net premium)

    Example (AN 0035-style): cover=1, swap=0.9355, settle=0.0645, premium≈0.00923
    → buy ≈ 0.99077; wallet keeps ~0.00923 premium → ends at 1.0 cover.
    """
    cover = covered_call_cover_native(group)
    swap = _spot_exit_swap_native(group)
    if settlement_loss is None:
        settlement_loss = group.spot_exit_settlement_loss
    settle = max(settlement_loss or Decimal("0"), Decimal("0"))
    premium = _spot_restore_premium_native(group)
    fee = _spot_restore_entry_fee_native(group)
    raw_target = swap + settle - premium
    if raw_target < 0:
        raw_target = Decimal("0")
    if cover > 0:
        target = min(raw_target, cover)
    else:
        target = raw_target
    # Heuristic: treat premium as folded into the exit when filled size is closer to
    # cover+premium−settle than to cover−settle (tolerates exchange rounding dust).
    structural_with_premium = max(cover + premium - settle, Decimal("0"))
    structural_cover_only = max(cover - settle, Decimal("0"))
    if cover > 0 and premium > 0 and swap > 0:
        premium_in_swap = abs(swap - structural_with_premium) <= abs(swap - structural_cover_only)
    else:
        premium_in_swap = False
    restored = wheel_spot_restore_filled_native(group, groups)
    unrestored = max(target - restored, Decimal("0"))
    return {
        "cover": cover,
        "swap": swap,
        "settle": settle,
        "premium": premium,
        "fee": fee,
        "target": target,
        "restored": restored,
        "unrestored": unrestored,
        "premium_in_swap": premium_in_swap,
        "premium_still_held_est": (not premium_in_swap) and premium > 0,
        "spot_exit_status": str(group.spot_exit_status or ""),
        "settlement_loss_source": str(group.spot_exit_settlement_loss_source or ""),
    }


def spot_restore_spot_instrument_name(group: TradeGroup) -> str:
    """Spot pair used to buy cover back. Follows the ITM exit quote (USDC vs USDT).

    Prefer the exit instrument over a parked restore pair so a USDT auto-restore
    GTC does not force a USDC wheel to buy the wrong quote.
    """
    exit_name = str(group.spot_exit_instrument_name or "").strip()
    if exit_name:
        return exit_name
    existing = str(group.spot_restore_instrument_name or "").strip()
    if existing:
        return existing
    from .spot_exit_ops import spot_exit_quote_currency

    currency = str(group.currency or "").upper() or "BTC"
    quote = spot_exit_quote_currency(group)
    return f"{currency}_{quote}"


def unrestored_spot_exit_native(
    group: TradeGroup,
    *,
    settlement_loss: Decimal | None = None,
    groups: list[TradeGroup] | None = None,
) -> Decimal:
    """Native still needed after ITM spot exit (honours partial SWAP fills)."""
    swap = _spot_exit_swap_native(group)
    settle = (
        max(settlement_loss, Decimal("0"))
        if settlement_loss is not None
        else max(group.spot_exit_settlement_loss, Decimal("0"))
    )
    if swap <= 0 and settle <= 0:
        return Decimal("0")
    plan = plan_spot_restore_to_cover(group, settlement_loss=settlement_loss, groups=groups)
    return to_decimal(plan["unrestored"])


def group_has_itm_spot_exit_fills(group: TradeGroup) -> bool:
    """True when ITM / settlement spot exit has real fill proceeds or filled native."""
    from .spot_exit_ops import spot_exit_filled_native, spot_exit_realized_usdt

    return spot_exit_realized_usdt(group) > 0 or spot_exit_filled_native(group) > 0


def itm_spot_exit_premium_folded(group: TradeGroup) -> bool:
    """True when journal spot-exit size includes entry premium (legacy SWAP fold)."""
    if not group_has_itm_spot_exit_fills(group):
        return False
    plan = plan_spot_restore_to_cover(group)
    return bool(plan.get("premium_in_swap"))


def itm_folded_premium_native(group: TradeGroup) -> Decimal:
    """Entry premium native sold inside a legacy folded ITM spot exit (else 0)."""
    if not itm_spot_exit_premium_folded(group):
        return Decimal("0")
    plan = plan_spot_restore_to_cover(group)
    return max(to_decimal(plan["premium"]), Decimal("0"))


def itm_folded_premium_usdt(group: TradeGroup) -> Decimal:
    """USDT proceeds attributable to premium when it was sold inside spot exit."""
    premium = itm_folded_premium_native(group)
    if premium <= 0:
        return Decimal("0")
    plan = plan_spot_restore_to_cover(group)
    swap = to_decimal(plan["swap"])
    if swap <= 0:
        return Decimal("0")
    from .spot_exit_ops import spot_exit_realized_usdt

    exit_u = spot_exit_realized_usdt(group)
    if exit_u <= 0:
        return Decimal("0")
    share = premium / swap
    if share > 1:
        share = Decimal("1")
    return exit_u * share


def spot_restore_lot_threshold(currency: str) -> Decimal:
    """Conservative exchange min lot when instrument lookup is unavailable."""
    return Decimal("0.001") if str(currency or "").upper() == "ETH" else Decimal("0.0001")


def default_option_restore_lot(currency: str) -> Decimal:
    """USDC linear ``min_trade_amount`` fallback (live BTC 0.01 / ETH 0.1)."""
    return Decimal("0.1") if str(currency or "").upper() == "ETH" else Decimal("0.01")


def lookup_usdc_linear_option_lot(client: DeribitClient | None, currency: str) -> Decimal:
    """USDC linear option min lot from the catalog, else the documented fallback."""
    typical = default_option_restore_lot(currency)
    if client is None:
        return typical
    try:
        rows = client.get_instruments("USDC", kind="option", expired=False) or []
    except Exception:  # noqa: BLE001
        LOGGER.debug("spot_restore: USDC linear lot lookup failed currency=%s", currency, exc_info=True)
        return typical
    prefix = f"{str(currency or '').upper()}_USDC-"
    found: list[Decimal] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("instrument_name") or "")
        if not name.startswith(prefix) or "PERPETUAL" in name.upper():
            continue
        amount = to_decimal(row.get("min_trade_amount"))
        if amount > 0:
            found.append(amount)
    return min(found) if found else typical


def align_spot_restore_buy_amount(
    *,
    amount: Decimal,
    spot_contract_size: Decimal,
    spot_min_trade: Decimal,
    option_lot: Decimal,
    cap: Decimal | None,
    size_mode: str,
) -> Decimal:
    """Ceil default restore buys to the USDC linear lot (BTC 0.01 / ETH 0.1), then the spot grid.

    Dust below the *spot* min is omitted. Explicit ``--amount`` / ``--usdt`` only
    ceil to the spot step so a partial manual size is not inflated to a full lot.
    """
    if amount <= 0:
        return Decimal("0")
    if spot_min_trade > 0 and amount < spot_min_trade:
        return Decimal("0")
    cover = cap if cap is not None and cap > 0 else None
    if str(size_mode or "") == "full_unrestored" and option_lot > 0:
        aligned = ceil_to_step(amount, option_lot)
        if cover is not None and aligned > cover:
            aligned = cover
        amount = aligned
    return ceil_option_order_amount(amount, spot_contract_size, spot_min_trade, cap=cover)


def align_spot_restore_amount(
    client: DeribitClient,
    *,
    instrument_name: str,
    amount: Decimal,
    cap: Decimal | None = None,
) -> Decimal:
    """Ceil restore buys to the spot grid; ``0`` means below exchange min. Never exceed ``cap``."""
    from .wallet_ops import _lookup_spot_instrument

    base = instrument_name.split("_", 1)[0]
    instrument = _lookup_spot_instrument(client, instrument_name, base)
    return ceil_option_order_amount(
        amount,
        instrument.contract_size,
        instrument.min_trade_amount,
        cap=cap,
    )


def spot_restore_operator_cancelled(group: TradeGroup) -> bool:
    status = str(group.spot_restore_status or "").lower()
    if status in {"skipped", "cancelled", "canceled"}:
        return True
    reason = str(group.spot_restore_reason or "").lower()
    return SPOT_RESTORE_OPERATOR_CANCELLED in reason


def mark_spot_restore_operator_cancelled(group: TradeGroup) -> None:
    group.spot_restore_status = "skipped"
    group.spot_restore_order_id = ""
    reason = str(group.spot_restore_reason or "").strip()
    tag = SPOT_RESTORE_OPERATOR_CANCELLED
    if tag not in reason:
        group.spot_restore_reason = f"{reason};{tag}" if reason else tag


def is_spot_restore_dust_amount(
    client: DeribitClient,
    *,
    instrument_name: str,
    amount: Decimal,
) -> bool:
    """True when ``amount`` > 0 but cannot be placed (below min / step)."""
    if amount <= 0:
        return False
    return align_spot_restore_amount(client, instrument_name=instrument_name, amount=amount) <= 0


def mark_spot_restore_dust_complete(group: TradeGroup, *, dust_amount: Decimal) -> None:
    """Omit sub-min remainder — do not round up past cover."""
    if dust_amount <= 0:
        return
    if str(group.spot_restore_status or "").lower() != "filled":
        group.spot_restore_status = "filled"
    if not group.spot_restore_instrument_name:
        group.spot_restore_instrument_name = spot_restore_spot_instrument_name(group)
    tag = "dust_below_min_omitted"
    reason = str(group.spot_restore_reason or "").strip()
    if tag not in reason:
        group.spot_restore_reason = f"{reason};{tag}" if reason else tag


def itm_spot_round_trip_complete(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> bool:
    """True when ITM exit/restore round-trip is done for PnL recognition.

    Prefer plan ``unrestored == 0``. Also accept both legs ``filled`` with real
    quote flows — incomplete SWAP journals sometimes keep an overstated
    ``spot_exit_amount`` (structural target) after restore already bought back
    the amount actually sold. Sub-min dust remainders are treated as complete
    (omit, never round up past cover). CSP assignment restores journaled on a
    child count toward the parent's cover.
    """
    plan = plan_spot_restore_to_cover(group, groups=groups)
    unrestored = to_decimal(plan["unrestored"])
    restore_u = wheel_spot_restore_realized_usdt(group, groups)
    if unrestored <= Decimal("1e-8"):
        target = to_decimal(plan["target"])
        restored = to_decimal(plan["restored"])
        if target > 0 and restored <= 0 and restore_u <= 0:
            return False
        return True
    exit_status = str(group.spot_exit_status or "").lower()
    restore_status = str(group.spot_restore_status or "").lower()
    dust_omitted = "dust_below_min_omitted" in str(group.spot_restore_reason or "")
    dust_remainder = unrestored < spot_restore_lot_threshold(group.currency)
    child_filled = restore_u > 0 and to_decimal(plan["restored"]) > 0
    if (restore_status == "filled" or child_filled) and (dust_omitted or dust_remainder):
        from .spot_exit_ops import spot_exit_realized_usdt

        if exit_status == "filled" and spot_exit_realized_usdt(group) > 0:
            return True
    if exit_status != "filled":
        return False
    if restore_status != "filled" and restore_u <= 0:
        return False
    from .spot_exit_ops import spot_exit_realized_usdt

    return spot_exit_realized_usdt(group) > 0 and restore_u > 0


def itm_spot_exit_net_usdt_for_total_profit(
    group: TradeGroup,
    groups: list[TradeGroup] | None = None,
) -> Decimal | None:
    """Recognize cover round-trip ``exit USDT − restore USDT`` after restore-to-cover.

    Before restore completes, do **not** treat raw exit proceeds as Total profit
    (that would count cover collateral as fee basis). When the restore target is
    already zero, net = exit proceeds alone.

    Legacy journals that folded premium into the spot exit attribute that premium
    USDT to Profit swap instead — subtract it here so Total profit does not
    double-count.
    """
    from .spot_exit_ops import spot_exit_realized_usdt

    if not group_has_itm_spot_exit_fills(group):
        return None
    exit_u = spot_exit_realized_usdt(group)
    if exit_u <= 0:
        return None
    if not itm_spot_round_trip_complete(group, groups):
        return None
    restore_u = wheel_spot_restore_realized_usdt(group, groups)
    net = exit_u - restore_u
    folded = itm_folded_premium_usdt(group)
    if folded > 0:
        net -= folded
    return net


def evaluate_auto_spot_restore(
    group: TradeGroup,
    *,
    buy_price: Decimal,
    min_edge_pct: Decimal = DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT,
    groups: list[TradeGroup] | None = None,
) -> dict[str, Any]:
    """Whether buying back ITM-sold cover at ``buy_price`` would lock in USDT profit.

    Restore reconstitutes cover. Net = remaining exit proceeds − estimated buy
    spend (already-restored USDT is subtracted). ``min_edge_pct`` is a buffer so
    fees/slippage do not wipe the edge (max buy = breakeven × (1 − edge)).
    """
    from .spot_exit_ops import spot_exit_realized_usdt

    payload: dict[str, Any] = {
        "ok": False,
        "reason": "",
        "buy_price": format_decimal(buy_price, 4) if buy_price > 0 else None,
        "min_edge_pct": format_decimal(min_edge_pct, 6),
    }
    if group.status != "closed" or not group.is_covered_call_group():
        payload["reason"] = "not_closed_covered_call"
        return payload
    exit_status = str(group.spot_exit_status or "").lower()
    if exit_status == "skipped":
        payload["reason"] = "spot_exit_skipped"
        return payload
    if exit_status != "filled":
        payload["reason"] = "spot_exit_not_filled"
        return payload
    restore_status = str(group.spot_restore_status or "").lower()
    if spot_restore_operator_cancelled(group):
        payload["reason"] = "operator_cancelled"
        return payload
    if restore_status == "submitted":
        payload["reason"] = "restore_in_flight"
        return payload
    if not group_has_itm_spot_exit_fills(group):
        payload["reason"] = "no_spot_exit_fill"
        return payload
    unrestored = unrestored_spot_exit_native(group, groups=groups)
    proceeds = spot_exit_realized_usdt(group)
    already_spent = wheel_spot_restore_realized_usdt(group, groups)
    remaining_proceeds = proceeds - already_spent
    payload["unrestored"] = format_decimal(unrestored, 8)
    payload["exit_proceeds"] = format_decimal(proceeds, 4) if proceeds > 0 else None
    payload["already_spent"] = format_decimal(already_spent, 4) if already_spent > 0 else None
    payload["remaining_proceeds"] = format_decimal(remaining_proceeds, 4) if remaining_proceeds != 0 else "0"
    if unrestored <= 0:
        payload["reason"] = "already_restored" if restore_status == "filled" else "nothing_to_restore"
        return payload
    if proceeds <= 0:
        payload["reason"] = "no_exit_proceeds"
        return payload
    if remaining_proceeds <= 0:
        payload["reason"] = "no_remaining_proceeds"
        return payload
    if buy_price <= 0:
        payload["reason"] = "no_buy_price"
        return payload
    edge = min_edge_pct if min_edge_pct > 0 else Decimal("0")
    if edge >= 1:
        edge = DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT
    breakeven = remaining_proceeds / unrestored
    max_buy = breakeven * (Decimal("1") - edge)
    estimated = unrestored * buy_price * SPOT_RESTORE_ORDER_BUDGET_BUFFER
    estimated_net = remaining_proceeds - estimated
    payload["breakeven_price"] = format_decimal(breakeven, 4)
    payload["max_buy_price"] = format_decimal(max_buy, 4)
    payload["estimated_spend"] = format_decimal(estimated, 4)
    payload["estimated_net_usdt"] = format_decimal(estimated_net, 4)
    if buy_price > max_buy or estimated_net <= 0:
        payload["reason"] = "price_not_favorable"
        return payload
    payload["ok"] = True
    payload["reason"] = "favorable"
    return payload


def auto_spot_restore_cap_price(
    group: TradeGroup,
    *,
    min_edge_pct: Decimal = DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT,
    groups: list[TradeGroup] | None = None,
) -> Decimal:
    """Highest USDT price that still leaves a non-negative restore round-trip."""
    from .spot_exit_ops import spot_exit_realized_usdt

    unrestored = unrestored_spot_exit_native(group, groups=groups)
    remaining = spot_exit_realized_usdt(group) - wheel_spot_restore_realized_usdt(group, groups)
    if unrestored <= 0 or remaining <= 0:
        return Decimal("0")
    edge = min_edge_pct if min_edge_pct > 0 else Decimal("0")
    if edge >= 1:
        edge = DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT
    return (remaining / unrestored) * (Decimal("1") - edge)


def evaluate_auto_spot_restore_park(
    group: TradeGroup,
    *,
    min_edge_pct: Decimal = DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT,
    groups: list[TradeGroup] | None = None,
) -> dict[str, Any]:
    """Whether we can park a GTC limit at the restore cap (ignores current ask)."""
    from .spot_exit_ops import spot_exit_realized_usdt

    payload: dict[str, Any] = {"ok": False, "reason": ""}
    if group.status != "closed" or not group.is_covered_call_group():
        payload["reason"] = "not_closed_covered_call"
        return payload
    exit_status = str(group.spot_exit_status or "").lower()
    if exit_status == "skipped":
        payload["reason"] = "spot_exit_skipped"
        return payload
    if exit_status != "filled":
        payload["reason"] = "spot_exit_not_filled"
        return payload
    if spot_restore_operator_cancelled(group):
        payload["reason"] = "operator_cancelled"
        return payload
    if not group_has_itm_spot_exit_fills(group):
        payload["reason"] = "no_spot_exit_fill"
        return payload
    unrestored = unrestored_spot_exit_native(group, groups=groups)
    remaining = spot_exit_realized_usdt(group) - wheel_spot_restore_realized_usdt(group, groups)
    cap = auto_spot_restore_cap_price(group, min_edge_pct=min_edge_pct, groups=groups)
    payload["unrestored"] = format_decimal(unrestored, 8)
    payload["remaining_proceeds"] = format_decimal(remaining, 4) if remaining != 0 else "0"
    payload["max_buy_price"] = format_decimal(cap, 4) if cap > 0 else None
    payload["min_edge_pct"] = format_decimal(
        min_edge_pct if 0 < min_edge_pct < 1 else DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT,
        6,
    )
    if unrestored <= 0:
        payload["reason"] = "already_restored"
        return payload
    if remaining <= 0:
        payload["reason"] = "no_remaining_proceeds"
        return payload
    if cap <= 0:
        payload["reason"] = "no_cap_price"
        return payload
    payload["ok"] = True
    payload["reason"] = "park_limit"
    return payload


def _spot_restore_order_is_open(client: DeribitClient, order_id: str | None) -> bool:
    oid = str(order_id or "").strip()
    if not oid:
        return False
    try:
        raw = client.get_order_state(oid)
    except Exception:  # noqa: BLE001
        LOGGER.debug("spot_restore: get_order_state failed order=%s", oid, exc_info=True)
        return False
    order = raw.get("order") if isinstance(raw, dict) and isinstance(raw.get("order"), dict) else raw
    if not isinstance(order, dict):
        return False
    state = str(order.get("order_state") or "").lower()
    return state in {"open", "untriggered", "new"}


def attach_auto_spot_restore_preview(
    payload: dict[str, Any],
    group: TradeGroup,
    *,
    buy_price: Decimal,
    min_edge_pct: Decimal | None = None,
    enabled: bool = False,
    price_source: str = "",
    groups: list[TradeGroup] | None = None,
) -> None:
    """Annotate a restore preview with auto-buy threshold vs current ask."""
    edge = min_edge_pct if min_edge_pct is not None else DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT
    if edge < 0 or edge >= 1:
        edge = DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT
    decision = evaluate_auto_spot_restore(group, buy_price=buy_price, min_edge_pct=edge, groups=groups)
    payload["auto_spot_restore_enabled"] = bool(enabled)
    payload["auto_would_buy"] = bool(decision.get("ok"))
    payload["auto_reason"] = decision.get("reason")
    payload["auto_breakeven_price"] = decision.get("breakeven_price")
    payload["auto_max_buy_price"] = decision.get("max_buy_price")
    payload["auto_ask"] = decision.get("buy_price")
    payload["auto_ask_source"] = price_source or None
    payload["auto_estimated_net_usdt"] = decision.get("estimated_net_usdt")
    payload["auto_min_edge_pct"] = decision.get("min_edge_pct")
    payload["auto_unrestored"] = decision.get("unrestored")
    payload["auto_exit_proceeds"] = decision.get("exit_proceeds")
    payload["auto_remaining_proceeds"] = decision.get("remaining_proceeds")


def sum_itm_spot_exit_net_usdt_for_total_profit(rows: list[dict[str, Any]]) -> Decimal:
    """Sum recognized ITM spot round-trip nets across closed (or journal) rows."""
    parsed: list[TradeGroup] = []
    for row in rows:
        try:
            parsed.append(TradeGroup.from_dict(row))
        except Exception:
            continue
    total = Decimal("0")
    for group in parsed:
        net = itm_spot_exit_net_usdt_for_total_profit(group, parsed)
        if net is None:
            continue
        total += net
    return total


def apply_spot_restore_quote_spent(
    group: TradeGroup,
    trades: list[dict[str, Any]],
    *,
    cumulative: bool = False,
) -> Decimal:
    from .spot_exit_ops import spot_exit_quote_currency

    quote = spot_exit_quote_currency(group, group.spot_restore_instrument_name)
    spent = spot_buy_quote_spent_from_trades(trades, quote_currency=quote)
    if spent <= 0:
        return Decimal("0")
    if cumulative:
        group.spot_restore_quote_spent += spent
    else:
        group.spot_restore_quote_spent = spent
    record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
    return spent


@dataclass
class SpotRestoreCandidate:
    group_id: str
    currency: str
    cover_amount: Decimal
    spot_exit_amount: Decimal
    settlement_loss: Decimal
    fee_amount: Decimal
    restore_target: Decimal
    spot_exit_quote_proceeds: Decimal
    restored_amount: Decimal
    restored_quote_spent: Decimal
    unrestored_amount: Decimal
    spot_restore_status: str
    instrument_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "currency": self.currency,
            "cover_amount": format_decimal(self.cover_amount, 8),
            "spot_exit_amount": format_decimal(self.spot_exit_amount, 8),
            "settlement_loss": format_decimal(self.settlement_loss, 8) if self.settlement_loss > 0 else None,
            "fee_amount": format_decimal(self.fee_amount, 8) if self.fee_amount > 0 else None,
            "restore_target": format_decimal(self.restore_target, 8),
            "spot_exit_quote_proceeds": format_decimal(self.spot_exit_quote_proceeds, 4)
            if self.spot_exit_quote_proceeds > 0
            else None,
            "restored_amount": format_decimal(self.restored_amount, 8) if self.restored_amount > 0 else None,
            "restored_quote_spent": format_decimal(self.restored_quote_spent, 4)
            if self.restored_quote_spent > 0
            else None,
            "unrestored_amount": format_decimal(self.unrestored_amount, 8),
            "spot_restore_status": self.spot_restore_status or None,
            "instrument_name": self.instrument_name,
        }


@dataclass
class SpotRestoreRunSummary:
    live: bool
    reconciled: int = 0
    scheduled: int = 0
    saved: bool = False
    candidates: list[SpotRestoreCandidate] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": "spot_restore",
            "live": self.live,
            "reconciled": self.reconciled,
            "scheduled": self.scheduled,
            "saved": self.saved,
            "candidate_count": len(self.candidates),
            "candidates": [row.to_dict() for row in self.candidates],
            "actions": self.actions,
            "previews": [action.get("preview") for action in self.actions if isinstance(action.get("preview"), dict)],
        }


def format_spot_restore_human_report(summary: SpotRestoreRunSummary) -> list[str]:
    """Human-readable dry-run / result lines for CLI (no --json)."""
    lines: list[str] = []
    mode = "LIVE" if summary.live else "PREVIEW (dry-run)"
    lines.append(f"spot-restore · {mode}")
    if summary.reconciled:
        lines.append(f"reconciled from exchange: {summary.reconciled}")
    actions = [a for a in summary.actions if str(a.get("action") or "").startswith("spot_restore")]
    if not actions and summary.candidates:
        lines.append(f"candidates: {len(summary.candidates)}")
        for row in summary.candidates:
            lines.append(
                f"  · group {row.group_id}  unrestored={format_decimal(row.unrestored_amount, 8)} "
                f"{row.currency}  (cover={format_decimal(row.cover_amount, 8)})"
            )
        return lines
    if not actions:
        lines.append("no restore actions")
        return lines
    for action in actions:
        group_id = action.get("group_id") or "?"
        currency = str(action.get("buy_currency") or action.get("currency") or "").upper() or "?"
        status = str(action.get("action") or "")
        if "skipped" in status:
            reason = action.get("reason")
            if reason == "dust_below_min":
                buy = action.get("buy_amount") or action.get("unrestored_amount") or "?"
                min_amt = action.get("min_trade_amount") or "?"
                lines.append(
                    f"  · group {group_id}: skipped dust_below_min "
                    f"(remainder {buy} {currency} < min {min_amt}; omit, not round up)"
                )
                if action.get("marked_complete"):
                    lines.append("      marked restore complete (dust omitted)")
            else:
                lines.append(f"  · group {group_id}: skipped ({reason})")
            continue
        buy_amount = action.get("buy_amount") or action.get("restore_amount") or "—"
        price = action.get("current_price") or action.get("current_price_usdt") or action.get("ref_buy_price") or "—"
        price_src = action.get("current_price_source") or ""
        estimated = action.get("estimated_usdt") or "—"
        price_note = f" ({price_src})" if price_src else ""
        lines.append(f"  · group {group_id}")
        lines.append(f"      預計買回: {buy_amount} {currency}")
        composition = action.get("buy_amount_composition") or (action.get("preview") or {}).get(
            "buy_amount_composition"
        )
        if isinstance(composition, dict):
            expr = composition.get("expression")
            if expr:
                lines.append(f"      組成: {expr}")
            else:
                lines.append(
                    "      組成: swap(已賣)={swap} + settle={settle} + entry_fee={fee} "
                    "− already_restored={restored} → buy={buy}".format(
                        swap=composition.get("swap_sold") or "0",
                        settle=composition.get("settlement_loss") or "0",
                        fee=composition.get("entry_fee") or "0",
                        restored=composition.get("already_restored") or "0",
                        buy=composition.get("this_order_buy_amount") or buy_amount,
                    )
                )
        lines.append(f"      當前價格: {price} USDT{price_note}")
        lines.append(f"      預計花費: {estimated} USDT  (buy × price)")
        order_type = str(action.get("order_type") or "limit").lower()
        if order_type == "limit":
            limit_px = action.get("limit_price") or price
            if action.get("park_resting"):
                lines.append(f"      訂單: limit GTC @買回上限  限價={limit_px}  （掛著等成交，不市價）")
            else:
                wait_s = action.get("wait_seconds") or DEFAULT_SPOT_RESTORE_WAIT_SECONDS
                lines.append(f"      訂單: limit@bid GTC post_only  限價={limit_px}  wait={wait_s}s")
        else:
            order_budget = action.get("order_budget_usdt") or action.get("quote_budget_usdt")
            if order_budget:
                lines.append(
                    f"      USDT 檢查: {order_budget} USDT  (buy × ask × {SPOT_RESTORE_ORDER_BUDGET_BUFFER}；實際下單為 native 數量)"
                )
            lines.append("      訂單: market")
        exit_status = action.get("spot_exit_status") or ""
        exit_filled = action.get("spot_exit_filled_native") or action.get("swap_amount")
        exit_usdt = action.get("spot_exit_quote_proceeds")
        if exit_status or exit_filled or exit_usdt:
            parts = [f"status={exit_status or '—'}"]
            if exit_filled:
                parts.append(f"filled={exit_filled} {currency}")
            if exit_usdt:
                parts.append(f"proceeds={exit_usdt} USDT")
            lines.append(f"      spot_exit: {'  '.join(parts)}")
        if action.get("auto_max_buy_price") or action.get("auto_reason"):
            enabled = "開啟" if action.get("auto_spot_restore_enabled") else "關閉（僅預覽）"
            edge = action.get("auto_min_edge_pct") or format_decimal(DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT, 6)
            lines.append(f"      自動買回: {enabled}  邊距={edge}")
            breakeven = action.get("auto_breakeven_price")
            max_buy = action.get("auto_max_buy_price")
            if breakeven or max_buy:
                lines.append(
                    f"      損益兩平: {breakeven or '—'} USDT  "
                    f"買回上限: {max_buy or '—'} USDT  （自動掛此價 GTC，成交前不再下單）"
                )
            ask = action.get("auto_ask") or "—"
            ask_src = action.get("auto_ask_source") or ""
            ask_note = f" ({ask_src})" if ask_src else ""
            if action.get("auto_would_buy"):
                net = action.get("auto_estimated_net_usdt") or "—"
                lines.append(f"      現況: 會買  ask {ask} USDT{ask_note}  預估淨利 {net} USDT")
            else:
                reason = action.get("auto_reason") or "—"
                lines.append(f"      現況: 不會買  ({reason})  ask {ask} USDT{ask_note}")
    return lines


def list_spot_restore_candidates(
    groups: list[TradeGroup],
    *,
    group_id: str | None = None,
    plan_groups: list[TradeGroup] | None = None,
) -> list[SpotRestoreCandidate]:
    rows: list[SpotRestoreCandidate] = []
    plan_pool = plan_groups if plan_groups is not None else groups
    for group in groups:
        if group_id and group.group_id != group_id:
            continue
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        plan = plan_spot_restore_to_cover(group, groups=plan_pool)
        swap = to_decimal(plan["swap"])
        settle = to_decimal(plan["settle"])
        if swap <= 0 and settle <= 0:
            continue
        unrestored = to_decimal(plan["unrestored"])
        restored_amt = to_decimal(plan["restored"])
        if unrestored <= 0 and restored_amt <= 0:
            continue
        from .spot_exit_ops import spot_exit_realized_usdt

        rows.append(
            SpotRestoreCandidate(
                group_id=group.group_id,
                currency=group.currency.upper(),
                cover_amount=to_decimal(plan["cover"]),
                spot_exit_amount=to_decimal(plan["swap"]),
                settlement_loss=to_decimal(plan["settle"]),
                fee_amount=to_decimal(plan["fee"]),
                restore_target=to_decimal(plan["target"]),
                spot_exit_quote_proceeds=spot_exit_realized_usdt(group),
                restored_amount=restored_amt,
                restored_quote_spent=wheel_spot_restore_realized_usdt(group, plan_pool),
                unrestored_amount=unrestored,
                spot_restore_status=str(group.spot_restore_status or ""),
                instrument_name=spot_restore_spot_instrument_name(group),
            )
        )
    rows.sort(key=lambda row: row.group_id)
    return rows


def _iter_spot_buy_trades(client: DeribitClient, currency: str):
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
            if str(trade.get("direction") or "").lower() != "buy":
                continue
            yield trade

    try:
        recent = fetch(currency, kind="spot", count=1000, historical=False)
        yield from _yield(list(recent.get("trades") or []))
    except Exception:  # noqa: BLE001
        LOGGER.debug("spot_restore: recent trades fetch failed currency=%s", currency, exc_info=True)

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
            LOGGER.debug("spot_restore: historical trades fetch failed currency=%s", currency, exc_info=True)
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


def spot_restore_buy_trades_for_currency(client: DeribitClient, currency: str) -> list[dict[str, Any]]:
    return [t for t in _iter_spot_buy_trades(client, currency) if is_spot_restore_label(str(t.get("label") or ""))]


def spot_restore_fill_stats_for_currency(client: DeribitClient, currency: str) -> dict[str, str]:
    trades = spot_restore_buy_trades_for_currency(client, currency)
    native = sum((to_decimal(t.get("amount")) for t in trades), Decimal("0"))
    usdt = spot_buy_quote_spent_from_trades(trades, quote_currency="USDT")

    def _avg(quote: Decimal, bought: Decimal) -> str:
        if bought <= 0:
            return "0"
        return format_decimal(quote / bought, 2)

    return {
        "native_bought": format_decimal(native, 8),
        "usdt_spent": format_decimal(usdt, 4),
        "avg_price_usd": _avg(usdt, native),
    }


def spot_restore_fill_stats_by_book(client: DeribitClient) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for currency in ("BTC", "ETH"):
        stats = spot_restore_fill_stats_for_currency(client, currency)
        if to_decimal(stats["native_bought"]) > 0 or to_decimal(stats["usdt_spent"]) > 0:
            out[currency] = stats
    return out


def reconcile_spot_restore_from_exchange(
    group: TradeGroup,
    *,
    client: DeribitClient,
    order_label_prefix: str,
) -> bool:
    if not group.is_covered_call_group():
        return False
    if str(group.spot_exit_status or "").lower() != "filled":
        return False
    status = str(group.spot_restore_status or "").lower()
    if status == "filled" and group.spot_restore_quote_spent > 0:
        record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
        return False

    label = spot_restore_order_label(group, order_label_prefix)
    trades: list[dict[str, Any]] = []
    order_id = str(group.spot_restore_order_id or "").strip()
    if order_id:
        try:
            trades = list(client.get_user_trades_by_order(order_id) or [])
            trades = [t for t in trades if str(t.get("direction") or "").lower() == "buy"]
        except Exception:  # noqa: BLE001
            LOGGER.debug(
                "spot_restore reconcile: trades by order failed order=%s group=%s",
                order_id,
                group.group_id,
                exc_info=True,
            )
            trades = []

    if not trades:
        for trade in spot_restore_buy_trades_for_currency(client, group.currency.upper()):
            if str(trade.get("label") or "") != label:
                continue
            trades.append(trade)

    if not trades:
        return False

    trades.sort(key=lambda row: int(row.get("timestamp") or 0))
    amount = sum(to_decimal(row.get("amount")) for row in trades)
    spent = spot_buy_quote_spent_from_trades(trades, quote_currency="USDT")
    last = trades[-1]
    last_order_id = str(last.get("order_id") or "").strip()
    instrument_name = str(last.get("instrument_name") or f"{group.currency.upper()}_USDT")

    group.spot_restore_status = "filled"
    group.spot_restore_instrument_name = instrument_name
    if last_order_id:
        group.spot_restore_order_id = last_order_id
    if amount > 0:
        group.spot_restore_amount = amount
    if spent > 0:
        group.spot_restore_quote_spent = spent
        record_spot_restore_lifetime_spent(group, spent)
    if not group.spot_restore_reason:
        group.spot_restore_reason = "manual_spot_restore"
    return True


def reconcile_spot_restores_in_groups(
    groups: list[TradeGroup],
    client: DeribitClient,
    order_label_prefix: str,
) -> int:
    repaired = 0
    for group in groups:
        if reconcile_spot_restore_from_exchange(
            group,
            client=client,
            order_label_prefix=order_label_prefix,
        ):
            repaired += 1
    return repaired


# Fee/slip cushion for USDT sufficiency + auto-restore estimate only.
# Live market restore buys the native target (never this extra as extra coins).
SPOT_RESTORE_ORDER_BUDGET_BUFFER = Decimal("1.001")
DEFAULT_SPOT_RESTORE_WAIT_SECONDS = 120


def _quote_budget_for_base_buy(
    client: DeribitClient,
    *,
    instrument_name: str,
    base_amount: Decimal,
    order_type: str = "market",
) -> Decimal:
    trade_price, _ = _spot_restore_buy_quote(
        client,
        instrument_name=instrument_name,
        order_type=order_type,
    )
    if trade_price <= 0 or base_amount <= 0:
        return Decimal("0")
    if str(order_type or "").lower() == "limit":
        return base_amount * trade_price
    return base_amount * trade_price * SPOT_RESTORE_ORDER_BUDGET_BUFFER


def _spot_restore_buy_quote(
    client: DeribitClient,
    *,
    instrument_name: str,
    order_type: str = "market",
) -> tuple[Decimal, str]:
    """Return ``(buy_price, price_source)`` for BTC_USDT / ETH_USDT restore buy.

    Limit restore quotes the resting maker bid; market quotes the ask.
    """
    from .wallet_ops import _lookup_spot_instrument, _spot_trade_price_quote

    base = instrument_name.split("_", 1)[0]
    instrument = _lookup_spot_instrument(client, instrument_name, base)
    trade_price, price_source, _ = _spot_trade_price_quote(
        client,
        instrument_name,
        direction="buy",
        order_type=str(order_type or "market").lower(),
        instrument=instrument,
        limit_price=None,
    )
    if trade_price <= 0:
        return Decimal("0"), str(price_source or "unavailable")
    return trade_price, str(price_source or "unknown")


def _spot_restore_buy_price(
    client: DeribitClient,
    *,
    instrument_name: str,
    order_type: str = "market",
) -> Decimal:
    price, _ = _spot_restore_buy_quote(
        client,
        instrument_name=instrument_name,
        order_type=order_type,
    )
    return price


def _response_order(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    order = payload.get("order")
    if isinstance(order, dict):
        return order
    if payload.get("order_id") or payload.get("order_state"):
        return payload
    return {}


def _response_filled_amount(payload: dict[str, Any] | None) -> Decimal:
    order = _response_order(payload)
    filled = to_decimal(order.get("filled_amount"))
    if filled > 0:
        return filled
    trades = payload.get("trades") if isinstance(payload, dict) else None
    if isinstance(trades, list):
        return sum(
            (to_decimal(t.get("amount")) for t in trades if str(t.get("direction") or "").lower() in {"", "buy"}),
            Decimal("0"),
        )
    return Decimal("0")


def await_resting_spot_order(
    client: DeribitClient,
    initial_response: dict[str, Any] | None,
    *,
    max_wait_seconds: int,
    poll_seconds: int = 10,
    sleep_fn: SleepFn | None = None,
) -> dict[str, Any]:
    """Poll a resting spot order, then cancel any remainder after ``max_wait_seconds``."""
    sleeper = sleep_fn or time.sleep
    if not isinstance(initial_response, dict):
        return {}
    order = _response_order(initial_response)
    order_id = str(order.get("order_id") or "").strip()
    state: dict[str, Any] = initial_response
    if not order_id:
        return state
    waited = 0
    wait_limit = max(0, int(max_wait_seconds))
    poll = max(1, int(poll_seconds))
    while waited < wait_limit:
        order = _response_order(state)
        order_state = str(order.get("order_state") or "").lower()
        if order_state in {"filled", "cancelled", "rejected"}:
            break
        step = min(poll, wait_limit - waited)
        if step <= 0:
            break
        sleeper(step)
        waited += step
        state = client.get_order_state(order_id)
    final_order = _response_order(state)
    final_state = str(final_order.get("order_state") or "").lower()
    if final_state not in {"filled", "cancelled", "rejected"}:
        try:
            client.cancel_order(order_id)
        except Exception:  # noqa: BLE001
            LOGGER.debug("spot_restore: cancel failed order=%s", order_id, exc_info=True)
        try:
            state = client.get_order_state(order_id)
        except Exception:  # noqa: BLE001
            LOGGER.debug("spot_restore: get_order_state after cancel failed order=%s", order_id, exc_info=True)
    return state


def place_spot_restore_limit_buy(
    client: DeribitClient,
    *,
    instrument_name: str,
    amount: Decimal,
    label: str,
    wait_seconds: int,
    poll_seconds: int = 10,
    sleep_fn: SleepFn | None = None,
) -> dict[str, Any]:
    """Resting post-only buy@bid for ``wait_seconds``, then cancel unfilled remainder."""
    from .wallet_ops import _align_spot_limit_price, _lookup_spot_instrument

    base = instrument_name.split("_", 1)[0]
    instrument = _lookup_spot_instrument(client, instrument_name, base)
    target = ceil_option_order_amount(amount, instrument.contract_size, instrument.min_trade_amount)
    if target <= 0:
        return {
            "skipped": True,
            "reason": "amount_below_min",
            "requested_amount": format_decimal(amount, 8),
        }

    sleeper = sleep_fn or time.sleep
    wait_limit = max(1, int(wait_seconds))
    poll = max(1, int(poll_seconds))
    waited = 0
    filled_total = Decimal("0")
    last_state: dict[str, Any] = {}
    last_order_id = ""
    last_limit_price = Decimal("0")
    responses: list[dict[str, Any]] = []
    buy_trades: list[dict[str, Any]] = []

    while filled_total < target and waited < wait_limit:
        remaining = align_option_order_amount(
            target - filled_total,
            instrument.contract_size,
            instrument.min_trade_amount,
        )
        if remaining <= 0:
            break
        book = OrderBookSnapshot.from_api(client.get_order_book(instrument_name, depth=1))
        if book.best_bid_price <= 0:
            return {
                "skipped": True,
                "reason": "limit_price_unavailable",
                "filled_native": format_decimal(filled_total, 8),
                "responses": responses,
            }
        limit_px = _align_spot_limit_price(book.best_bid_price, instrument)
        last_limit_price = limit_px
        window = min(poll, wait_limit - waited)
        try:
            response = client.place_buy_order(
                instrument_name=instrument_name,
                amount=remaining,
                price=limit_px,
                label=label,
                order_type="limit",
                time_in_force="good_til_cancelled",
                post_only=True,
                reject_post_only=True,
            )
        except ExchangeError as exc:
            if not is_post_only_reject(exc):
                raise
            LOGGER.info(
                "spot_restore post_only rejected on %s price=%s; repricing (%s)",
                instrument_name,
                format_decimal(limit_px, 4),
                exc,
            )
            if window > 0:
                sleeper(min(1.0, float(window)))
            waited += max(window, 1)
            continue

        state = await_resting_spot_order(
            client,
            response,
            max_wait_seconds=window,
            poll_seconds=poll,
            sleep_fn=sleeper,
        )
        responses.append(state)
        last_state = state
        order = _response_order(state)
        order_id = str(order.get("order_id") or "").strip()
        if order_id:
            last_order_id = order_id
        newly = _response_filled_amount(state)
        if newly <= 0 and order_id:
            try:
                trades = list(client.get_user_trades_by_order(order_id) or [])
            except Exception:  # noqa: BLE001
                trades = []
            newly = sum(
                (to_decimal(t.get("amount")) for t in trades if str(t.get("direction") or "").lower() in {"", "buy"}),
                Decimal("0"),
            )
        else:
            trades = state.get("trades") if isinstance(state.get("trades"), list) else []
            if not trades and order_id:
                try:
                    trades = list(client.get_user_trades_by_order(order_id) or [])
                except Exception:  # noqa: BLE001
                    trades = []
        for trade in trades:
            if str(trade.get("direction") or "").lower() not in {"", "buy"}:
                continue
            buy_trades.append(trade)
        if newly > 0:
            filled_total += newly
        waited += window
        if filled_total >= target:
            break

    by_id: dict[Any, dict[str, Any]] = {}
    for trade in buy_trades:
        key = trade.get("trade_id")
        by_id[key if key is not None else id(trade)] = trade
    buy_trades = list(by_id.values())
    if buy_trades:
        filled_total = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))

    payload: dict[str, Any] = {
        "order_type": "limit",
        "time_in_force": "good_til_cancelled",
        "post_only": True,
        "limit_price": format_decimal(last_limit_price, 4) if last_limit_price > 0 else None,
        "wait_seconds": wait_limit,
        "filled_native": format_decimal(filled_total, 8),
        "order_id": last_order_id or None,
        "responses": responses,
        "trades": buy_trades,
        "response": last_state or None,
    }
    if filled_total <= 0:
        payload["skipped"] = True
        payload["reason"] = "timed_out"
    return payload


def place_spot_restore_market_buy(
    client: DeribitClient,
    *,
    instrument_name: str,
    amount: Decimal,
    label: str,
) -> dict[str, Any]:
    """Market buy the aligned native target — do not spend extra USDT that overshoots cover."""
    from .wallet_ops import _lookup_spot_instrument

    base = instrument_name.split("_", 1)[0]
    instrument = _lookup_spot_instrument(client, instrument_name, base)
    target = ceil_option_order_amount(amount, instrument.contract_size, instrument.min_trade_amount)
    if target <= 0:
        return {
            "skipped": True,
            "reason": "amount_below_min",
            "requested_amount": format_decimal(amount, 8),
            "filled_native": "0",
            "trades": [],
        }
    response = client.place_buy_order(
        instrument_name=instrument_name,
        amount=target,
        label=label,
        order_type="market",
    )
    order = _response_order(response)
    order_id = str(order.get("order_id") or "").strip()
    trades = list(response.get("trades") or []) if isinstance(response, dict) else []
    if not trades and order_id:
        try:
            trades = list(client.get_user_trades_by_order(order_id) or [])
        except Exception:  # noqa: BLE001
            LOGGER.debug("spot_restore market: trades lookup failed order=%s", order_id, exc_info=True)
            trades = []
    buy_trades = [t for t in trades if str(t.get("direction") or "").lower() in {"", "buy"}]
    filled_native = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))
    if filled_native <= 0:
        filled_native = _response_filled_amount(response)
    return {
        "skipped": filled_native <= 0,
        "reason": None if filled_native > 0 else "unfilled",
        "order_type": "market",
        "order_id": order_id or None,
        "filled_native": format_decimal(filled_native, 8),
        "trades": buy_trades,
        "response": response,
        "requested_amount": format_decimal(target, 8),
    }


def place_spot_restore_resting_limit_buy(
    client: DeribitClient,
    *,
    instrument_name: str,
    amount: Decimal,
    price: Decimal,
    label: str,
) -> dict[str, Any]:
    """Park a GTC limit buy at ``price`` and return immediately (do not wait / cancel)."""
    from .wallet_ops import _align_spot_limit_price, _lookup_spot_instrument

    base = instrument_name.split("_", 1)[0]
    instrument = _lookup_spot_instrument(client, instrument_name, base)
    # Never ceil above the caller-sized amount — CSP restore sizes to free USDC;
    # rounding up would trigger not_enough_funds_in_currency and crash the loop.
    target = ceil_option_order_amount(
        amount,
        instrument.contract_size,
        instrument.min_trade_amount,
        cap=amount,
    )
    limit_px = _align_spot_limit_price(price, instrument)
    if target <= 0:
        return {
            "skipped": True,
            "reason": "amount_below_min",
            "requested_amount": format_decimal(amount, 8),
            "filled_native": "0",
            "trades": [],
        }
    if limit_px <= 0:
        return {
            "skipped": True,
            "reason": "limit_price_unavailable",
            "filled_native": "0",
            "trades": [],
        }
    response = client.place_buy_order(
        instrument_name=instrument_name,
        amount=target,
        price=limit_px,
        label=label,
        order_type="limit",
        time_in_force="good_til_cancelled",
        post_only=False,
    )
    order = _response_order(response)
    order_id = str(order.get("order_id") or "").strip()
    trades = list(response.get("trades") or []) if isinstance(response, dict) else []
    if not trades and order_id:
        try:
            trades = list(client.get_user_trades_by_order(order_id) or [])
        except Exception:  # noqa: BLE001
            LOGGER.debug("spot_restore park: trades lookup failed order=%s", order_id, exc_info=True)
            trades = []
    buy_trades = [t for t in trades if str(t.get("direction") or "").lower() in {"", "buy"}]
    filled_native = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))
    if filled_native <= 0:
        filled_native = _response_filled_amount(response)
    order_state = str(order.get("order_state") or "").lower()
    parked = filled_native <= 0 and (order_state in {"", "open", "untriggered", "new"} or bool(order_id))
    return {
        "skipped": filled_native <= 0 and not parked,
        "parked": parked,
        "reason": None if filled_native > 0 or parked else "unfilled",
        "order_type": "limit",
        "time_in_force": "good_til_cancelled",
        "post_only": False,
        "limit_price": format_decimal(limit_px, 4),
        "order_id": order_id or None,
        "order_state": order_state or None,
        "filled_native": format_decimal(filled_native, 8),
        "trades": buy_trades,
        "response": response,
        "requested_amount": format_decimal(target, 8),
    }


def build_spot_restore_buy_amount_composition(
    plan: dict[str, Decimal | str | bool],
    *,
    buy_amount: Decimal,
    size_mode: str | None,
) -> dict[str, Any]:
    """Explain how ``buy_amount`` is derived from swap + settle − premium."""
    swap = to_decimal(plan.get("swap"))
    settle = to_decimal(plan.get("settle"))
    premium = to_decimal(plan.get("premium"))
    fee = to_decimal(plan.get("fee"))
    cover = to_decimal(plan.get("cover"))
    restored = to_decimal(plan.get("restored"))
    plan_target = to_decimal(plan.get("target"))
    unrestored = to_decimal(plan.get("unrestored"))
    raw_sum = swap + settle - premium
    capped = cover > 0 and raw_sum > cover + Decimal("1e-12")
    mode = str(size_mode or "full_unrestored")
    if mode == "usdt":
        formula = "this_order from --usdt (capped at unrestored = min(cover, swap+settle−premium) − restored)"
    elif mode == "native":
        formula = "this_order from --amount (capped at unrestored = min(cover, swap+settle−premium) − restored)"
    else:
        formula = "buy_amount = min(cover, swap + settle − premium) − already_restored"
    premium_note = (
        "premium still held as native (not fully included in spot exit) — subtract so restore ends at cover"
        if plan.get("premium_still_held_est")
        else "premium accounting credit (if already sold inside swap, identity still yields cover)"
    )
    return {
        "swap_sold": format_decimal(swap, 8),
        "settlement_loss": format_decimal(settle, 8),
        "premium_native": format_decimal(premium, 8),
        "premium_note": premium_note,
        "entry_fee_info_only": format_decimal(fee, 8),
        "entry_fee_note": "already netted inside premium_native; not added again",
        "sum_swap_settle_minus_premium": format_decimal(max(raw_sum, Decimal("0")), 8),
        "cover": format_decimal(cover, 8) if cover > 0 else None,
        "capped_at_cover": capped,
        "plan_target": format_decimal(plan_target, 8),
        "already_restored": format_decimal(restored, 8),
        "unrestored_before_order": format_decimal(unrestored, 8),
        "this_order_buy_amount": format_decimal(buy_amount, 8),
        "size_mode": mode,
        "formula": formula,
        "expression": (
            f"{format_decimal(swap, 8)} (swap) + {format_decimal(settle, 8)} (settle) − "
            f"{format_decimal(premium, 8)} (premium) = {format_decimal(max(raw_sum, Decimal('0')), 8)}"
            + (
                f" → capped at cover {format_decimal(cover, 8)} = {format_decimal(plan_target, 8)}"
                if capped
                else f" = plan_target {format_decimal(plan_target, 8)}"
            )
            + (
                f" − already_restored {format_decimal(restored, 8)} = unrestored {format_decimal(unrestored, 8)}"
                if restored > 0
                else ""
            )
            + (
                f" → this_order {format_decimal(buy_amount, 8)} ({mode})"
                if buy_amount != unrestored
                else f" → this_order {format_decimal(buy_amount, 8)}"
            )
        ),
    }


def attach_spot_restore_preview_quote(
    payload: dict[str, Any],
    *,
    buy_amount: Decimal,
    buy_currency: str,
    trade_price: Decimal,
    price_source: str,
    quote_budget: Decimal,
    plan: dict[str, Decimal | str | bool] | None = None,
    size_mode: str | None = None,
) -> None:
    """Attach explicit dry-run quote fields: buy size, current price, estimated USDT."""
    estimated = buy_amount * trade_price if trade_price > 0 and buy_amount > 0 else Decimal("0")
    if estimated <= 0 and quote_budget > 0:
        estimated = quote_budget
    composition = (
        build_spot_restore_buy_amount_composition(plan, buy_amount=buy_amount, size_mode=size_mode)
        if plan is not None
        else None
    )
    payload["buy_amount"] = format_decimal(buy_amount, 8)
    payload["buy_currency"] = buy_currency
    payload["current_price"] = format_decimal(trade_price, 4) if trade_price > 0 else None
    payload["current_price_usdt"] = payload["current_price"]
    payload["current_price_source"] = price_source
    payload["estimated_usdt"] = format_decimal(estimated, 4) if estimated > 0 else None
    order_type = str(payload.get("order_type") or "limit").lower()
    if order_type == "limit":
        payload["estimated_usdt_meaning"] = "notional = buy_amount × current_price (best bid limit)"
        payload["order_budget_usdt_meaning"] = (
            "limit restore places native amount at bid; order_budget_usdt ≈ estimated_usdt"
        )
    else:
        payload["estimated_usdt_meaning"] = "notional = buy_amount × current_price (best ask)"
        payload["order_budget_usdt_meaning"] = (
            f"USDT sufficiency check = buy_amount × ask × {SPOT_RESTORE_ORDER_BUDGET_BUFFER}; "
            "live market order is native buy_amount (no extra coins)"
        )
    # Keep legacy aliases used by earlier previews / scripts.
    payload["restore_amount"] = payload["buy_amount"]
    payload["ref_buy_price"] = payload["current_price"]
    payload["quote_budget_usdt"] = format_decimal(quote_budget, 4) if quote_budget > 0 else None
    payload["order_budget_usdt"] = payload["quote_budget_usdt"]
    if composition is not None:
        payload["buy_amount_composition"] = composition
    payload["preview"] = {
        "buy_amount": payload["buy_amount"],
        "buy_currency": buy_currency,
        "buy_amount_composition": composition,
        "current_price_usdt": payload["current_price"],
        "current_price_source": price_source,
        "estimated_usdt": payload["estimated_usdt"],
        "estimated_usdt_meaning": payload["estimated_usdt_meaning"],
        "order_budget_usdt": payload["order_budget_usdt"],
        "order_budget_usdt_meaning": payload["order_budget_usdt_meaning"],
    }


def resolve_spot_restore_order_size(
    *,
    unrestored: Decimal,
    amount: Decimal | None,
    quote_usdt: Decimal | None,
    trade_price: Decimal,
    quote_budget_for_unrestored: Decimal,
) -> dict[str, Any]:
    """Resolve native restore target + USDT spend from ``--amount`` or ``--usdt``.

    Returns a dict with either ``ok=True`` and sizing fields, or ``ok=False`` + ``reason``.
    """
    has_amount = amount is not None and amount > 0
    has_quote = quote_usdt is not None and quote_usdt > 0
    if has_amount and has_quote:
        return {"ok": False, "reason": "amount_and_usdt_mutually_exclusive"}
    if unrestored <= 0:
        return {"ok": False, "reason": "nothing_to_restore", "unrestored": unrestored}

    if has_quote:
        assert quote_usdt is not None
        if trade_price <= 0:
            return {"ok": False, "reason": "spot_price_unavailable"}
        if quote_budget_for_unrestored <= 0:
            return {"ok": False, "reason": "nothing_to_restore", "unrestored": unrestored}
        quote_budget = min(quote_usdt, quote_budget_for_unrestored)
        estimated_native = quote_budget / trade_price
        target = min(unrestored, estimated_native)
        if target <= 0:
            return {"ok": False, "reason": "nothing_to_restore", "unrestored": unrestored}
        return {
            "ok": True,
            "target": target,
            "quote_budget": quote_budget,
            "size_mode": "usdt",
            "requested_usdt": quote_usdt,
            "usdt_capped_to_unrestored": quote_budget + Decimal("0.00005") < quote_usdt,
        }

    target = amount if has_amount else unrestored
    assert target is not None
    if target > unrestored + Decimal("1e-8"):
        return {
            "ok": False,
            "reason": "amount_exceeds_unrestored",
            "requested": target,
            "unrestored": unrestored,
        }
    if target <= 0:
        return {"ok": False, "reason": "nothing_to_restore", "unrestored": unrestored}
    # Caller supplies quote budget for the chosen native target.
    return {
        "ok": True,
        "target": target,
        "quote_budget": None,
        "size_mode": "native" if has_amount else "full_unrestored",
        "requested_usdt": None,
        "usdt_capped_to_unrestored": False,
    }


def _ensure_spot_exit_settlement_loss(bot: DeribitOptionTrialBot, group: TradeGroup) -> None:
    """Backfill persisted settlement debit when older journals omit it."""
    if group.spot_exit_settlement_loss > 0:
        return
    if covered_call_spot_exit_skips_settlement_for_group(group):
        group.spot_exit_settlement_loss = Decimal("0")
        if not group.spot_exit_settlement_loss_source:
            group.spot_exit_settlement_loss_source = "skipped_robust"
        return
    try:
        from .covered_call_settlement import resolve_covered_call_settlement_loss

        index_price = Decimal("0")
        getter = getattr(bot, "_spot_exit_index_price_usd", None)
        if callable(getter):
            index_price = to_decimal(getter(group, {}))
        if index_price <= 0:
            # Fall back to close/entry index on the group.
            if group.close_index_usd is not None and group.close_index_usd > 0:
                index_price = group.close_index_usd
            elif group.entry_index_usd > 0:
                index_price = group.entry_index_usd
        loss, source = resolve_covered_call_settlement_loss(
            group,
            index_price_usd=index_price,
            short_instrument=None,
            client=bot.client,
            reason=group.spot_exit_reason or "covered_call_settlement_exit",
            prefer_log=bool(getattr(bot.config, "has_private_credentials", False)),
        )
        if loss > 0 or source:
            group.spot_exit_settlement_loss = max(loss, Decimal("0"))
            group.spot_exit_settlement_loss_source = str(source or "")
    except Exception:  # noqa: BLE001
        LOGGER.debug("spot_restore: settlement backfill failed group=%s", group.group_id, exc_info=True)


def covered_call_spot_exit_skips_settlement_for_group(group: TradeGroup) -> bool:
    from .covered_call_settlement import covered_call_spot_exit_skips_settlement_loss

    return covered_call_spot_exit_skips_settlement_loss(
        reason=group.spot_exit_reason,
        spot_exit_reason=group.spot_exit_reason,
    )


def execute_spot_restore_for_group(
    bot: DeribitOptionTrialBot,
    group: TradeGroup,
    *,
    amount: Decimal | None = None,
    quote_usdt: Decimal | None = None,
    live: bool = False,
    order_type: str | None = None,
    wait_seconds: int | None = None,
    sleep_fn: SleepFn | None = None,
    restore_reason: str = "manual_spot_restore",
    park_resting: bool = False,
    instrument_name: str | None = None,
    groups: list[TradeGroup] | None = None,
) -> dict[str, Any]:
    """Buy back cover sold by ITM spot exit; records spot_restore_* for accounting.

    Default size restores original cover via ``swap + settle + fee``.
    Default execution is resting limit buy@bid for ``SPOT_RESTORE_WAIT_SECONDS``.
    """
    if not group.is_covered_call_group():
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "not_covered_call",
        }
    _ensure_spot_exit_settlement_loss(bot, group)
    plan = plan_spot_restore_to_cover(group, groups=groups)
    swap = to_decimal(plan["swap"])
    settle = to_decimal(plan["settle"])
    if swap <= 0 and settle <= 0:
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "no_spot_exit_fill",
            "spot_exit_status": str(group.spot_exit_status or "") or None,
        }
    unrestored = to_decimal(plan["unrestored"])
    currency = group.currency.upper()
    override = str(instrument_name or "").strip().upper()
    if override:
        allowed = {f"{currency}_USDC", f"{currency}_USDT"}
        if override not in allowed:
            return {
                "action": "spot_restore_skipped",
                "group_id": group.group_id,
                "reason": "invalid_spot_instrument",
                "instrument_name": override,
                "allowed": sorted(allowed),
            }
        instrument_name = override
    else:
        instrument_name = spot_restore_spot_instrument_name(group)
    label = spot_restore_order_label(group, bot.config.order_label_prefix)
    auto_restore = str(restore_reason or "").startswith("auto_spot_restore")
    if auto_restore:
        # Auto restore may only park a cap-priced GTC. Never market-buy.
        park_resting = True
    resolved_order_type = str(order_type or getattr(bot.config, "spot_restore_order_type", None) or "limit").lower()
    if park_resting:
        resolved_order_type = "limit"
    if resolved_order_type not in {"limit", "market"}:
        resolved_order_type = "limit"
    if auto_restore and resolved_order_type == "market":
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "auto_restore_market_forbidden",
        }
    resolved_wait = int(
        wait_seconds
        if wait_seconds is not None
        else getattr(bot.config, "spot_restore_wait_seconds", DEFAULT_SPOT_RESTORE_WAIT_SECONDS)
    )
    resolved_wait = max(1, resolved_wait)
    park_min_edge = to_decimal(
        getattr(bot.config, "covered_call_auto_spot_restore_min_edge_pct", None)
        or DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT
    )
    if park_resting:
        cap = auto_spot_restore_cap_price(group, min_edge_pct=park_min_edge, groups=groups)
        trade_price, price_source = cap, "auto_max_buy"
    else:
        trade_price, price_source = _spot_restore_buy_quote(
            bot.client,
            instrument_name=instrument_name,
            order_type=resolved_order_type,
        )
    auto_ask, auto_ask_source = trade_price, price_source
    if resolved_order_type != "market":
        try:
            auto_ask, auto_ask_source = _spot_restore_buy_quote(
                bot.client,
                instrument_name=instrument_name,
                order_type="market",
            )
        except Exception:  # noqa: BLE001
            pass
    auto_kwargs = {
        "buy_price": auto_ask,
        "min_edge_pct": to_decimal(
            getattr(bot.config, "covered_call_auto_spot_restore_min_edge_pct", None)
            or DEFAULT_AUTO_SPOT_RESTORE_MIN_EDGE_PCT
        ),
        "enabled": bool(getattr(bot.config, "covered_call_auto_spot_restore_enabled", False)),
        "price_source": auto_ask_source,
        "groups": groups,
    }
    quote_for_unrestored = (
        _quote_budget_for_base_buy(
            bot.client,
            instrument_name=instrument_name,
            base_amount=unrestored,
            order_type=resolved_order_type,
        )
        if unrestored > 0
        else Decimal("0")
    )
    sized = resolve_spot_restore_order_size(
        unrestored=unrestored,
        amount=amount,
        quote_usdt=quote_usdt,
        trade_price=trade_price,
        quote_budget_for_unrestored=quote_for_unrestored,
    )
    if not sized.get("ok"):
        payload = {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": sized.get("reason"),
            "unrestored_amount": format_decimal(unrestored, 8),
            "cover_amount": format_decimal(to_decimal(plan["cover"]), 8),
            "swap_amount": format_decimal(to_decimal(plan["swap"]), 8),
            "settlement_loss": format_decimal(to_decimal(plan["settle"]), 8),
            "fee_amount": format_decimal(to_decimal(plan["fee"]), 8),
        }
        if sized.get("requested") is not None:
            payload["requested"] = format_decimal(sized["requested"], 8)
        attach_auto_spot_restore_preview(payload, group, **auto_kwargs)
        return payload

    target = sized["target"]
    from .spot_exit_ops import spot_exit_realized_usdt
    from .wallet_ops import _lookup_spot_instrument

    spot_instrument = _lookup_spot_instrument(bot.client, instrument_name, currency)
    cover_cap = to_decimal(plan["cover"])
    option_lot = lookup_usdc_linear_option_lot(bot.client, currency)
    aligned_target = align_spot_restore_buy_amount(
        amount=target,
        spot_contract_size=spot_instrument.contract_size,
        spot_min_trade=spot_instrument.min_trade_amount,
        option_lot=option_lot,
        cap=cover_cap if cover_cap > 0 else None,
        size_mode=str(sized.get("size_mode") or ""),
    )
    if target > 0 and aligned_target <= 0:
        # Below exchange min/step: omit (never round up past cover).
        composition = build_spot_restore_buy_amount_composition(
            plan,
            buy_amount=target,
            size_mode=str(sized.get("size_mode") or ""),
        )
        payload = {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "dust_below_min",
            "dust_policy": "omit_not_round_up",
            "currency": currency,
            "instrument_name": instrument_name,
            "unrestored_amount": format_decimal(unrestored, 8),
            "buy_amount": format_decimal(target, 8),
            "min_trade_amount": format_decimal(spot_instrument.min_trade_amount, 8),
            "contract_size": format_decimal(spot_instrument.contract_size, 8),
            "cover_amount": format_decimal(to_decimal(plan["cover"]), 8),
            "swap_amount": format_decimal(to_decimal(plan["swap"]), 8),
            "settlement_loss": format_decimal(to_decimal(plan["settle"]), 8),
            "premium_native": format_decimal(to_decimal(plan["premium"]), 8),
            "restore_target": format_decimal(to_decimal(plan["target"]), 8),
            "buy_amount_composition": composition,
            "size_mode": sized.get("size_mode"),
            "live": live,
        }
        if live:
            mark_spot_restore_dust_complete(group, dust_amount=target)
            payload["marked_complete"] = True
            payload["spot_restore_status"] = group.spot_restore_status or None
            payload["spot_restore_reason"] = group.spot_restore_reason or None
        attach_auto_spot_restore_preview(payload, group, **auto_kwargs)
        return payload

    target = aligned_target
    quote_budget = sized.get("quote_budget")
    if quote_budget is None:
        quote_budget = _quote_budget_for_base_buy(
            bot.client,
            instrument_name=instrument_name,
            base_amount=target,
            order_type=resolved_order_type,
        )

    swap_native = to_decimal(plan["swap"])
    exit_proceeds = spot_exit_realized_usdt(group)
    payload: dict[str, Any] = {
        "action": "spot_restore" if live else "spot_restore_preview",
        "group_id": group.group_id,
        "currency": currency,
        "instrument_name": instrument_name,
        "unrestored_amount": format_decimal(unrestored, 8),
        "cover_amount": format_decimal(to_decimal(plan["cover"]), 8),
        "swap_amount": format_decimal(swap_native, 8),
        "spot_exit_filled_native": format_decimal(swap_native, 8),
        "spot_exit_quote_proceeds": format_decimal(exit_proceeds, 4) if exit_proceeds > 0 else None,
        "settlement_loss": format_decimal(to_decimal(plan["settle"]), 8),
        "fee_amount": format_decimal(to_decimal(plan["fee"]), 8),
        "fee_amount_meaning": "option_entry_fee_native_info_only_already_in_premium",
        "premium_native": format_decimal(to_decimal(plan["premium"]), 8),
        "premium_still_held_est": bool(plan.get("premium_still_held_est")),
        "spot_exit_status": str(group.spot_exit_status or "") or None,
        "restore_target": format_decimal(to_decimal(plan["target"]), 8),
        "option_lot": format_decimal(option_lot, 8),
        "size_mode": sized.get("size_mode"),
        "order_type": resolved_order_type,
        "label": label,
        "live": live,
        "restore_reason": restore_reason or "manual_spot_restore",
    }
    if resolved_order_type == "limit":
        payload["limit_price"] = format_decimal(trade_price, 4) if trade_price > 0 else None
        payload["time_in_force"] = "good_til_cancelled"
        payload["post_only"] = False if park_resting else True
        payload["wait_seconds"] = 0 if park_resting else resolved_wait
        if park_resting:
            payload["park_resting"] = True
            payload["limit_price_source"] = "auto_max_buy"
    attach_spot_restore_preview_quote(
        payload,
        buy_amount=target,
        buy_currency=currency,
        trade_price=trade_price,
        price_source=price_source,
        quote_budget=quote_budget,
        plan=plan,
        size_mode=str(sized.get("size_mode") or ""),
    )
    if plan.get("settlement_loss_source"):
        payload["settlement_loss_source"] = plan["settlement_loss_source"]
    if plan.get("premium_in_swap"):
        payload["premium_in_swap"] = True
    if sized.get("requested_usdt") is not None:
        payload["requested_usdt"] = format_decimal(sized["requested_usdt"], 4)
    if sized.get("usdt_capped_to_unrestored"):
        payload["usdt_capped_to_unrestored"] = True
    attach_auto_spot_restore_preview(payload, group, **auto_kwargs)
    if not live:
        return payload

    if park_resting:
        if _spot_restore_order_is_open(bot.client, group.spot_restore_order_id):
            payload["action"] = "spot_restore_skipped"
            payload["reason"] = "restore_in_flight"
            payload["spot_restore_order_id"] = group.spot_restore_order_id or None
            return payload
        if trade_price <= 0:
            payload["action"] = "spot_restore_skipped"
            payload["reason"] = "no_cap_price"
            return payload
        result = place_spot_restore_resting_limit_buy(
            bot.client,
            instrument_name=instrument_name,
            amount=target,
            price=trade_price,
            label=label,
        )
        payload.update(
            {
                k: v
                for k, v in result.items()
                if k not in {"trades", "response", "skipped", "reason", "filled_native", "order_id", "parked"}
            }
        )
        if result.get("skipped") and not result.get("parked"):
            payload["action"] = "spot_restore_skipped"
            payload["reason"] = result.get("reason")
            payload["filled_native"] = result.get("filled_native") or "0"
            if result.get("order_id"):
                payload["spot_restore_order_id"] = result.get("order_id")
            return payload
        order_id = str(result.get("order_id") or "").strip()
        buy_trades = list(result.get("trades") or [])
        filled_native = to_decimal(result.get("filled_native"))
        if filled_native <= 0 and buy_trades:
            filled_native = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))
        avg_fallback = trade_price
        if filled_native <= 0 and result.get("parked") and order_id:
            group.spot_restore_status = "submitted"
            group.spot_restore_instrument_name = instrument_name
            group.spot_restore_reason = restore_reason or "auto_spot_restore_park"
            group.spot_restore_order_id = order_id
            payload["action"] = "spot_restore_submitted"
            payload["reason"] = "parked_limit"
            payload["spot_restore_status"] = group.spot_restore_status
            payload["spot_restore_order_id"] = order_id
            payload["filled_native"] = "0"
            return payload
    elif resolved_order_type == "limit":
        result = place_spot_restore_limit_buy(
            bot.client,
            instrument_name=instrument_name,
            amount=target,
            label=label,
            wait_seconds=resolved_wait,
            poll_seconds=max(1, int(getattr(bot.config, "order_poll_seconds", 10) or 10)),
            sleep_fn=sleep_fn,
        )
        payload.update(
            {
                k: v
                for k, v in result.items()
                if k
                not in {
                    "responses",
                    "trades",
                    "response",
                    "skipped",
                    "reason",
                    "filled_native",
                    "order_id",
                }
            }
        )
        if result.get("skipped"):
            payload["action"] = "spot_restore_skipped"
            payload["reason"] = result.get("reason")
            payload["filled_native"] = result.get("filled_native") or "0"
            if result.get("order_id"):
                payload["spot_restore_order_id"] = result.get("order_id")
            return payload
        order_id = str(result.get("order_id") or "").strip()
        buy_trades = list(result.get("trades") or [])
        filled_native = to_decimal(result.get("filled_native"))
        if filled_native <= 0 and buy_trades:
            filled_native = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))
        avg_fallback = trade_price
    else:
        if auto_restore or park_resting:
            payload["action"] = "spot_restore_skipped"
            payload["reason"] = "auto_restore_market_forbidden"
            return payload
        result = place_spot_restore_market_buy(
            bot.client,
            instrument_name=instrument_name,
            amount=target,
            label=label,
        )
        payload.update(
            {
                k: v
                for k, v in result.items()
                if k not in {"trades", "response", "skipped", "reason", "filled_native", "order_id"}
            }
        )
        if result.get("skipped"):
            payload["action"] = "spot_restore_skipped"
            payload["reason"] = result.get("reason")
            payload["filled_native"] = result.get("filled_native") or "0"
            if result.get("order_id"):
                payload["spot_restore_order_id"] = result.get("order_id")
            return payload
        order_id = str(result.get("order_id") or "").strip()
        buy_trades = list(result.get("trades") or [])
        filled_native = to_decimal(result.get("filled_native"))
        if filled_native <= 0 and buy_trades:
            filled_native = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))
        avg_fallback = trade_price

    if filled_native <= 0:
        payload["action"] = "spot_restore_skipped"
        payload["reason"] = "unfilled"
        return payload

    prior = group.spot_restore_amount if str(group.spot_restore_status or "").lower() == "filled" else Decimal("0")
    prior_spent = group.spot_restore_quote_spent if prior > 0 else Decimal("0")

    group.spot_restore_status = "filled"
    group.spot_restore_instrument_name = instrument_name
    group.spot_restore_reason = restore_reason or "manual_spot_restore"
    if order_id:
        group.spot_restore_order_id = order_id
    group.spot_restore_amount = prior + filled_native
    spent = apply_spot_restore_quote_spent(group, buy_trades, cumulative=False)
    if spent > 0:
        group.spot_restore_quote_spent = prior_spent + spent
        record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
    elif filled_native > 0:
        estimate = filled_native * avg_fallback if avg_fallback > 0 else quote_budget
        group.spot_restore_quote_spent = prior_spent + estimate
        record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
        spent = estimate

    payload["action"] = "spot_restore"
    payload["spot_restore_status"] = group.spot_restore_status
    payload["spot_restore_order_id"] = group.spot_restore_order_id or None
    payload["spot_restore_amount"] = format_decimal(group.spot_restore_amount, 8)
    payload["spot_restore_quote_spent"] = format_decimal(group.spot_restore_quote_spent, 4)
    payload["filled_native"] = format_decimal(filled_native, 8)
    if spent > 0:
        payload["filled_quote_spent"] = format_decimal(spent, 4)
    return payload


def run_spot_restores(
    bot: DeribitOptionTrialBot,
    *,
    live: bool = False,
    group_id: str | None = None,
    amount: Decimal | None = None,
    quote_usdt: Decimal | None = None,
    reconcile_only: bool = False,
    order_type: str | None = None,
    wait_seconds: int | None = None,
    instrument_name: str | None = None,
) -> SpotRestoreRunSummary:
    """Reconcile and optionally buy back cover sold by ITM spot exits."""
    context = bot._load_runtime(live=live)
    summary = SpotRestoreRunSummary(live=live)
    prefix = bot.config.order_label_prefix

    for group in context.state.groups:
        if group_id and group.group_id != group_id:
            continue
        if reconcile_spot_restore_from_exchange(group, client=bot.client, order_label_prefix=prefix):
            summary.reconciled += 1

    summary.candidates = list_spot_restore_candidates(context.state.groups, group_id=group_id)

    if reconcile_only:
        bot.state_store.save(context.state)
        summary.saved = True
        summary.candidates = list_spot_restore_candidates(context.state.groups, group_id=group_id)
        return summary

    targets = [
        g
        for g in context.state.groups
        if (not group_id or g.group_id == group_id)
        and g.status == "closed"
        and g.is_covered_call_group()
        and unrestored_spot_exit_native(g, groups=context.state.groups) > 0
    ]
    sized_request = (amount is not None and amount > 0) or (quote_usdt is not None and quote_usdt > 0)
    if sized_request and len(targets) > 1:
        raise SystemExit("--amount / --usdt requires --group-id when multiple unrestored groups exist")
    if (amount is not None and amount > 0) and (quote_usdt is not None and quote_usdt > 0):
        raise SystemExit("spot-restore: use either --amount or --usdt, not both")

    for group in targets:
        if live and str(group.cash_secured_status or "").lower() in {"entered", "submitted"}:
            group.cash_secured_status = "skipped"
            group.cash_secured_reason = "operator_spot_restore"
            bot.state_store.save(context.state)
        action = execute_spot_restore_for_group(
            bot,
            group,
            amount=amount if group_id else None,
            quote_usdt=quote_usdt if group_id else None,
            live=live,
            order_type=order_type,
            wait_seconds=wait_seconds,
            instrument_name=instrument_name,
            groups=context.state.groups,
        )
        summary.actions.append(action)
        if str(action.get("action") or "").startswith("spot_restore") and "skipped" not in str(
            action.get("action") or ""
        ):
            summary.scheduled += 1

    if live:
        if summary.actions:
            bot._persist_trade_journal_actions(summary.actions)
        bot.state_store.save(context.state)
        summary.saved = True
    summary.candidates = list_spot_restore_candidates(context.state.groups, group_id=group_id)
    return summary
