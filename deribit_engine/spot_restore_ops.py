"""CLI-driven cover restore after ITM / settlement spot exit (separate from Profit swap)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .models import TradeGroup
from .utils import format_decimal, to_decimal
from .wallet_ops import spot_buy_quote_spent_from_trades

if TYPE_CHECKING:
    from .client import DeribitClient
    from .engine import DeribitOptionTrialBot

LOGGER = logging.getLogger(__name__)


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


def unrestored_spot_exit_native(group: TradeGroup) -> Decimal:
    """Cover still missing after ITM spot exit (exit amount − restored)."""
    if str(group.spot_exit_status or "").lower() != "filled":
        return Decimal("0")
    exited = group.spot_exit_amount
    if exited <= 0:
        return Decimal("0")
    restored = group.spot_restore_amount if str(group.spot_restore_status or "").lower() == "filled" else Decimal("0")
    if str(group.spot_restore_status or "").lower() in {"pending", "submitted"} and group.spot_restore_amount > 0:
        restored = max(restored, group.spot_restore_amount)
    return max(exited - restored, Decimal("0"))


def apply_spot_restore_quote_spent(
    group: TradeGroup,
    trades: list[dict[str, Any]],
    *,
    cumulative: bool = False,
) -> Decimal:
    spent = spot_buy_quote_spent_from_trades(trades, quote_currency="USDT")
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
    spot_exit_amount: Decimal
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
            "spot_exit_amount": format_decimal(self.spot_exit_amount, 8),
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
        }


def list_spot_restore_candidates(
    groups: list[TradeGroup],
    *,
    group_id: str | None = None,
) -> list[SpotRestoreCandidate]:
    rows: list[SpotRestoreCandidate] = []
    for group in groups:
        if group_id and group.group_id != group_id:
            continue
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if str(group.spot_exit_status or "").lower() != "filled":
            continue
        unrestored = unrestored_spot_exit_native(group)
        restored_amt = group.spot_restore_amount if group.spot_restore_amount > 0 else Decimal("0")
        if unrestored <= 0 and restored_amt <= 0:
            continue
        from .spot_exit_ops import spot_exit_realized_usdt

        rows.append(
            SpotRestoreCandidate(
                group_id=group.group_id,
                currency=group.currency.upper(),
                spot_exit_amount=group.spot_exit_amount,
                spot_exit_quote_proceeds=spot_exit_realized_usdt(group),
                restored_amount=restored_amt,
                restored_quote_spent=spot_restore_realized_usdt(group),
                unrestored_amount=unrestored,
                spot_restore_status=str(group.spot_restore_status or ""),
                instrument_name=group.spot_restore_instrument_name
                or group.spot_exit_instrument_name
                or f"{group.currency.upper()}_USDT",
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


def _quote_budget_for_base_buy(
    client: DeribitClient,
    *,
    instrument_name: str,
    base_amount: Decimal,
) -> Decimal:
    from .profit_sweep_repair import _quote_spend_for_base_buy

    return _quote_spend_for_base_buy(
        client,
        instrument_name=instrument_name,
        base_amount=base_amount,
        proceeds_budget=Decimal("0"),
    )


def execute_spot_restore_for_group(
    bot: DeribitOptionTrialBot,
    group: TradeGroup,
    *,
    amount: Decimal | None = None,
    live: bool = False,
) -> dict[str, Any]:
    """Buy back cover sold by ITM spot exit; records spot_restore_* for accounting."""
    if not group.is_covered_call_group():
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "not_covered_call",
        }
    if str(group.spot_exit_status or "").lower() != "filled":
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "spot_exit_not_filled",
        }
    unrestored = unrestored_spot_exit_native(group)
    target = amount if amount is not None and amount > 0 else unrestored
    if target <= 0:
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "nothing_to_restore",
            "unrestored_amount": format_decimal(unrestored, 8),
        }
    if target > unrestored + Decimal("1e-8"):
        return {
            "action": "spot_restore_skipped",
            "group_id": group.group_id,
            "reason": "amount_exceeds_unrestored",
            "requested": format_decimal(target, 8),
            "unrestored_amount": format_decimal(unrestored, 8),
        }

    currency = group.currency.upper()
    instrument_name = f"{currency}_USDT"
    label = spot_restore_order_label(group, bot.config.order_label_prefix)
    quote_budget = _quote_budget_for_base_buy(
        bot.client,
        instrument_name=instrument_name,
        base_amount=target,
    )
    payload: dict[str, Any] = {
        "action": "spot_restore" if live else "spot_restore_preview",
        "group_id": group.group_id,
        "currency": currency,
        "instrument_name": instrument_name,
        "restore_amount": format_decimal(target, 8),
        "unrestored_amount": format_decimal(unrestored, 8),
        "quote_budget_usdt": format_decimal(quote_budget, 4),
        "label": label,
        "live": live,
    }
    if not live:
        return payload

    from .wallet_ops import trade_spot

    result = trade_spot(
        bot.config,
        bot.client,
        from_currency="USDT",
        to_currency=currency,
        amount=format_decimal(quote_budget, 4),
        instrument_name=instrument_name,
        order_type=bot.config.covered_call_spot_order_type,
        live=True,
        label=label,
    )
    payload.update({k: v for k, v in result.items() if k not in {"action", "live"}})
    if result.get("action") == "trade_spot_skipped":
        payload["action"] = "spot_restore_skipped"
        payload["reason"] = result.get("reason")
        return payload

    order_id = str(result.get("order_id") or "").strip()
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    trades = list(response.get("trades") or [])
    if not trades and order_id:
        try:
            trades = list(bot.client.get_user_trades_by_order(order_id) or [])
        except Exception:  # noqa: BLE001
            LOGGER.debug("spot_restore: trades lookup failed order=%s", order_id, exc_info=True)
            trades = []
    buy_trades = [t for t in trades if str(t.get("direction") or "").lower() == "buy"]
    filled_native = sum((to_decimal(t.get("amount")) for t in buy_trades), Decimal("0"))
    if filled_native <= 0:
        filled_native = to_decimal(result.get("amount"))
    prior = group.spot_restore_amount if str(group.spot_restore_status or "").lower() == "filled" else Decimal("0")
    prior_spent = group.spot_restore_quote_spent if prior > 0 else Decimal("0")

    group.spot_restore_status = "filled"
    group.spot_restore_instrument_name = instrument_name
    group.spot_restore_reason = "manual_spot_restore"
    if order_id:
        group.spot_restore_order_id = order_id
    group.spot_restore_amount = prior + filled_native
    spent = apply_spot_restore_quote_spent(group, buy_trades, cumulative=False)
    if spent > 0:
        group.spot_restore_quote_spent = prior_spent + spent
        record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
    elif filled_native > 0:
        # Fallback: estimate from quote budget / fill price on order.
        avg = to_decimal(result.get("average_price"))
        estimate = filled_native * avg if avg > 0 else quote_budget
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
    reconcile_only: bool = False,
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
        and unrestored_spot_exit_native(g) > 0
    ]
    if amount is not None and amount > 0 and len(targets) > 1:
        raise SystemExit("--amount requires --group-id when multiple unrestored groups exist")

    for group in targets:
        action = execute_spot_restore_for_group(
            bot,
            group,
            amount=amount if group_id else None,
            live=live,
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
