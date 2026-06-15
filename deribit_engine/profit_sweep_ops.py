"""Manual profit-sweep helpers: sell remaining covered-call spot profit to USDT."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .models import TradeGroup
from .trade_journal_backfill import profit_sweep_order_label, reconcile_profit_sweep_from_exchange
from .utils import format_decimal, to_decimal

if TYPE_CHECKING:
    from .client import DeribitClient
    from .engine import DeribitOptionTrialBot
    from .engine.context import RuntimeContext


def record_profit_sweep_lifetime_proceeds(group: TradeGroup, proceeds: Decimal) -> None:
    """Immutable lifetime USDT from premium swap fills (not reduced by wallet reconcile / withdrawal)."""
    if proceeds <= 0:
        return
    if proceeds > group.profit_sweep_quote_proceeds_lifetime:
        group.profit_sweep_quote_proceeds_lifetime = proceeds


@dataclass
class ProfitSweepTradeCache:
    """Fetch each currency's profit-sweep spot sells once per sweep run."""

    client: DeribitClient
    _by_key: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    _loaded: set[str] = field(default_factory=set)

    def trades_for_group(self, group: TradeGroup, order_label_prefix: str) -> list[dict[str, Any]]:
        currency = group.currency.upper()
        label = profit_sweep_order_label(order_label_prefix, group)
        self._ensure_currency(currency)
        return list(self._by_key.get((currency, label), []))

    def _ensure_currency(self, currency: str) -> None:
        currency = currency.upper()
        if currency in self._loaded:
            return
        fetch = getattr(self.client, "get_user_trades_by_currency", None)
        if not callable(fetch):
            self._loaded.add(currency)
            return
        try:
            payload = fetch(currency, kind="spot", count=100, historical=True)
        except Exception:
            self._loaded.add(currency)
            return
        seen: set[Any] = set()
        for trade in payload.get("trades", []):
            label = str(trade.get("label") or "")
            if "profit-sweep" not in label:
                continue
            if str(trade.get("direction") or "").lower() != "sell":
                continue
            trade_id = trade.get("trade_id")
            if trade_id in seen:
                continue
            seen.add(trade_id)
            key = (currency, label)
            self._by_key.setdefault(key, []).append(trade)
        for key, rows in self._by_key.items():
            if key[0] == currency:
                rows.sort(key=lambda row: int(row.get("timestamp") or 0))
        self._loaded.add(currency)


@dataclass
class RemainingProfitSweepRow:
    group_id: str
    currency: str
    short_instrument_name: str
    native_total: Decimal
    remaining_native: Decimal
    to_sweep_native: Decimal
    profit_sweep_status: str
    profit_sweep_amount: Decimal
    profit_sweep_quote_proceeds: Decimal
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "currency": self.currency,
            "short_instrument_name": self.short_instrument_name,
            "native_total": format_decimal(self.native_total, 8),
            "remaining_native": format_decimal(self.remaining_native, 8),
            "to_sweep_native": format_decimal(self.to_sweep_native, 8),
            "profit_sweep_status": self.profit_sweep_status or None,
            "profit_sweep_amount": format_decimal(self.profit_sweep_amount, 8)
            if self.profit_sweep_amount > 0
            else None,
            "profit_sweep_quote_proceeds": format_decimal(self.profit_sweep_quote_proceeds, 4)
            if self.profit_sweep_quote_proceeds > 0
            else None,
            "kind": self.kind,
        }


@dataclass
class ProfitSweepRunSummary:
    live: bool
    candidates: list[RemainingProfitSweepRow] = field(default_factory=list)
    reconciled: int = 0
    blocked_oversweep: int = 0
    scheduled: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)
    saved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": "profit_sweep",
            "live": self.live,
            "candidate_count": len(self.candidates),
            "candidates": [row.to_dict() for row in self.candidates],
            "reconciled": self.reconciled,
            "blocked_oversweep": self.blocked_oversweep,
            "scheduled": self.scheduled,
            "actions": self.actions,
            "saved": self.saved,
        }


def realized_spot_profit_native_for_group(group: TradeGroup) -> Decimal | None:
    """Fee-aware realized option premium profit (collateral native).

    Profit sweep may only sell this amount — never open-position collateral or
    principal. Premium ledger (fill prices) is authoritative; stored
    ``realized_pnl_collateral_native`` is a fallback only when the ledger cannot
    be computed (``None``), not when it shows a loss or zero.
    """
    if group.status != "closed" or not group.is_covered_call_group() or not group.is_coin_collateral():
        return None
    native = group.compute_coin_profit_native(allow_ledger_spot_infer=False)
    close_idx = group.close_index_usd
    if native is None and close_idx is not None and close_idx > 0:
        native = group.compute_coin_profit_native(allow_ledger_spot_infer=True)
    if native is None and group.realized_pnl_collateral_native is not None:
        if group.realized_pnl_collateral_native > 0:
            native = group.realized_pnl_collateral_native
    if native is None or native <= 0:
        return None
    return native


def native_profit_for_group(group: TradeGroup) -> Decimal | None:
    """Alias for :func:`realized_spot_profit_native_for_group`."""
    return realized_spot_profit_native_for_group(group)


def profit_sweep_sell_trades_for_group(
    client: DeribitClient,
    group: TradeGroup,
    order_label_prefix: str,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> list[dict[str, Any]]:
    """All spot sell fills for this group's profit-sweep order label."""
    if trade_cache is not None:
        return trade_cache.trades_for_group(group, order_label_prefix)
    label = profit_sweep_order_label(order_label_prefix, group)
    fetch = getattr(client, "get_user_trades_by_currency", None)
    if not callable(fetch):
        return []
    try:
        payload = fetch(group.currency, kind="spot", count=100, historical=True)
    except Exception:
        return []
    seen: set[Any] = set()
    trades: list[dict[str, Any]] = []
    for trade in payload.get("trades", []):
        if str(trade.get("label") or "") != label:
            continue
        if str(trade.get("direction") or "").lower() != "sell":
            continue
        trade_id = trade.get("trade_id")
        if trade_id in seen:
            continue
        seen.add(trade_id)
        trades.append(trade)
    trades.sort(key=lambda row: int(row.get("timestamp") or 0))
    return trades


def exchange_swept_native_for_group(
    client: DeribitClient,
    group: TradeGroup,
    order_label_prefix: str,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> Decimal:
    return sum(
        to_decimal(trade.get("amount"))
        for trade in profit_sweep_sell_trades_for_group(
            client,
            group,
            order_label_prefix,
            trade_cache=trade_cache,
        )
    )


def _first_day_profit_sweep_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    from datetime import UTC, datetime

    ordered = sorted(trades, key=lambda row: int(row.get("timestamp") or 0))
    days = sorted(
        {datetime.fromtimestamp(int(row.get("timestamp") or 0) / 1000, tz=UTC).strftime("%Y-%m-%d") for row in ordered}
    )
    if len(days) <= 1:
        return ordered
    first_day = days[0]
    return [
        row
        for row in ordered
        if datetime.fromtimestamp(int(row.get("timestamp") or 0) / 1000, tz=UTC).strftime("%Y-%m-%d") == first_day
    ]


def _profit_sweep_state_locked(group: TradeGroup) -> bool:
    """Groups reconciled/repaired manually — do not overwrite amount/proceeds from per-label fills."""
    reason = str(group.profit_sweep_reason or "")
    return any(
        token in reason
        for token in (
            "duplicate_sweep_repaired",
            "proceeds_reconciled",
            "premium_amount_synced",
        )
    )


def guard_profit_sweep_against_oversell(
    group: TradeGroup,
    client: DeribitClient,
    order_label_prefix: str,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> bool:
    """Sync state from exchange fills; return True when nothing remains to sweep."""
    if group.status != "closed" or not group.is_covered_call_group():
        return False
    if _profit_sweep_state_locked(group):
        return True
    native = native_profit_for_group(group)
    if native is None or native <= 0:
        return True

    trades = profit_sweep_sell_trades_for_group(
        client,
        group,
        order_label_prefix,
        trade_cache=trade_cache,
    )
    if not trades:
        return False

    attributed_trades = _first_day_profit_sweep_trades(trades)
    exchange_swept = sum(to_decimal(trade.get("amount")) for trade in attributed_trades)
    remaining = max(Decimal("0"), native - exchange_swept)
    last = attributed_trades[-1]
    order_id = str(last.get("order_id") or "").strip()
    instrument_name = str(last.get("instrument_name") or f"{group.currency.upper()}_USDT")

    from .wallet_ops import spot_sell_quote_proceeds_from_trades

    if exchange_swept >= native:
        group.profit_sweep_status = "filled"
        group.profit_sweep_amount = min(exchange_swept, native)
        group.profit_sweep_instrument_name = instrument_name
        if order_id:
            group.profit_sweep_order_id = order_id
        proceeds = spot_sell_quote_proceeds_from_trades(attributed_trades, quote_currency="USDT")
        if proceeds > 0:
            group.profit_sweep_quote_proceeds = proceeds
            record_profit_sweep_lifetime_proceeds(group, proceeds)
        reason = str(group.profit_sweep_reason or "")
        if "exchange_fully_swept" not in reason:
            group.profit_sweep_reason = (reason + "; exchange_fully_swept").strip("; ")
        return True

    if exchange_swept > group.profit_sweep_amount:
        group.profit_sweep_amount = exchange_swept
        group.profit_sweep_instrument_name = instrument_name
        if order_id:
            group.profit_sweep_order_id = order_id
        if remaining > 0:
            group.profit_sweep_status = "filled"
        proceeds = spot_sell_quote_proceeds_from_trades(attributed_trades, quote_currency="USDT")
        if proceeds > 0:
            group.profit_sweep_quote_proceeds = proceeds
            record_profit_sweep_lifetime_proceeds(group, proceeds)

    if remaining <= 0:
        group.profit_sweep_status = "filled"
        return True
    return False


def _exchange_remaining_native(
    client: DeribitClient,
    group: TradeGroup,
    order_label_prefix: str,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> Decimal | None:
    native = native_profit_for_group(group)
    if native is None:
        return None
    exchange_swept = exchange_swept_native_for_group(
        client,
        group,
        order_label_prefix,
        trade_cache=trade_cache,
    )
    if exchange_swept <= 0:
        status = str(group.profit_sweep_status or "").lower()
        if status in {"pending", "submitted"}:
            queued = to_sweep_native(group)
            if queued > 0:
                return queued
        return remaining_spot_profit_native(group)
    return max(Decimal("0"), native - exchange_swept)


def heal_reconciled_proceeds_drift(
    bot: DeribitOptionTrialBot,
    groups: list[TradeGroup],
    *,
    drift_threshold_usdt: Decimal = Decimal("0.50"),
) -> bool:
    """Re-allocate labeled premium-sweep USDT when journal drifts from exchange net.

    Unlabeled pre-label premium sells are repaired first and excluded from the
    labeled pool so wallet USDT can exceed exchange net without being reset.
    """
    from .profit_sweep_repair import actual_premium_sweep_usdt_net, reconcile_premium_proceeds_to_groups
    from .trade_journal_backfill import (
        repair_manual_swap_proceeds_in_groups,
        repair_unlabeled_profit_sweeps_in_groups,
        unlabeled_premium_usdt_total,
    )

    repair_manual_swap_proceeds_in_groups(groups)
    repair_unlabeled_profit_sweeps_in_groups(groups, bot.client, bot.config.order_label_prefix)

    swept_groups = [
        g
        for g in groups
        if g.status == "closed"
        and g.is_covered_call_group()
        and (g.profit_sweep_quote_proceeds_lifetime or g.profit_sweep_quote_proceeds) > 0
    ]
    if not swept_groups:
        return False
    state_usdt = sum(
        ((g.profit_sweep_quote_proceeds_lifetime or g.profit_sweep_quote_proceeds) for g in swept_groups),
        Decimal("0"),
    )
    try:
        exchange_net = actual_premium_sweep_usdt_net(bot.client, bot.config.order_label_prefix)
    except Exception:
        return False
    unlabeled_usdt = unlabeled_premium_usdt_total(groups)
    expected_usdt = exchange_net + unlabeled_usdt
    if expected_usdt <= 0 or abs(state_usdt - expected_usdt) <= drift_threshold_usdt:
        return False
    reconcile_premium_proceeds_to_groups(
        groups,
        bot.client,
        bot.config.order_label_prefix,
        apply=True,
        target_total_usdt=exchange_net,
    )
    sync_filled_profit_sweep_amounts_to_premium(groups)
    return True


def sync_filled_profit_sweep_amounts_to_premium(
    groups: list[TradeGroup],
    *,
    currency: str | None = None,
) -> int:
    """Mark filled sweeps at full premium native when state amount lags (clears UI remainder)."""
    updated = 0
    target = str(currency or "").upper()
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if target and group.currency.upper() != target:
            continue
        if str(group.profit_sweep_status or "").lower() != "filled":
            continue
        native = realized_spot_profit_native_for_group(group)
        if native is None or native <= 0:
            continue
        if group.profit_sweep_amount >= native:
            continue
        group.profit_sweep_amount = native
        reason = str(group.profit_sweep_reason or "")
        if "premium_amount_synced" not in reason:
            group.profit_sweep_reason = (reason + "; premium_amount_synced").strip("; ")
        updated += 1
    return updated


def remaining_spot_profit_native(group: TradeGroup) -> Decimal:
    """Held spot profit after prior sweeps (dashboard Remaining column)."""
    native = native_profit_for_group(group)
    if native is None or native <= 0:
        return Decimal("0")
    sweep = str(group.profit_sweep_status or "").lower()
    sweep_amt = group.profit_sweep_amount if group.profit_sweep_amount > 0 else native
    if sweep == "filled":
        return max(Decimal("0"), native - min(sweep_amt, native))
    if sweep in {"pending", "submitted"}:
        return max(Decimal("0"), native - sweep_amt)
    return native


def to_sweep_native(group: TradeGroup) -> Decimal:
    """Native amount this run would sell (queued pending or unswept remainder)."""
    native = native_profit_for_group(group)
    if native is None or native <= 0:
        return Decimal("0")
    status = str(group.profit_sweep_status or "").lower()
    if status in {"pending", "submitted"}:
        swept = group.profit_sweep_amount if group.profit_sweep_amount > 0 else native
        if swept > 0 and swept < native:
            return max(native - swept, Decimal("0"))
        return swept if swept > 0 else native
    if status == "filled":
        return remaining_spot_profit_native(group)
    return native


def _row_kind(group: TradeGroup) -> str:
    status = str(group.profit_sweep_status or "").lower()
    if status in {"pending", "submitted"}:
        return "queued"
    if status == "filled" and remaining_spot_profit_native(group) > 0:
        return "remainder"
    return "unswept"


def list_remaining_profit_sweeps(
    groups: list[TradeGroup],
    *,
    group_id: str | None = None,
    min_to_sweep: Decimal = Decimal("0"),
) -> list[RemainingProfitSweepRow]:
    rows: list[RemainingProfitSweepRow] = []
    target = str(group_id or "").strip()
    for group in groups:
        if target and group.group_id != target:
            continue
        native = native_profit_for_group(group)
        if native is None:
            continue
        sweep_amt = to_sweep_native(group)
        if sweep_amt <= min_to_sweep:
            continue
        rows.append(
            RemainingProfitSweepRow(
                group_id=group.group_id,
                currency=group.currency,
                short_instrument_name=group.short_instrument_name,
                native_total=native,
                remaining_native=remaining_spot_profit_native(group),
                to_sweep_native=sweep_amt,
                profit_sweep_status=str(group.profit_sweep_status or ""),
                profit_sweep_amount=group.profit_sweep_amount,
                profit_sweep_quote_proceeds=group.profit_sweep_quote_proceeds,
                kind=_row_kind(group),
            )
        )
    return rows


def _would_schedule_for_manual_sweep(
    bot: DeribitOptionTrialBot,
    group: TradeGroup,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> bool:
    prefix = bot.config.order_label_prefix
    if guard_profit_sweep_against_oversell(
        group,
        bot.client,
        prefix,
        trade_cache=trade_cache,
    ):
        return False
    remaining = _exchange_remaining_native(
        bot.client,
        group,
        prefix,
        trade_cache=trade_cache,
    )
    if remaining is None or remaining <= 0:
        return False

    status = str(group.profit_sweep_status or "").lower()
    if status == "submitted":
        return False
    if status == "filled":
        return remaining > 0
    native = bot._coin_profit_native_for_sweep(group)
    if native is None or native <= 0:
        return False
    if status in {"", "failed", "skipped"}:
        return True
    if status == "pending":
        return to_sweep_native(group) > 0
    return False


def _ensure_pending_for_manual_sweep(
    bot: DeribitOptionTrialBot,
    group: TradeGroup,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> bool:
    if _profit_sweep_state_locked(group):
        return False
    prefix = bot.config.order_label_prefix
    if guard_profit_sweep_against_oversell(
        group,
        bot.client,
        prefix,
        trade_cache=trade_cache,
    ):
        return False
    if not _would_schedule_for_manual_sweep(bot, group, trade_cache=trade_cache):
        return False

    native = bot._coin_profit_native_for_sweep(group)
    if native is None or native <= 0:
        return False
    exchange_swept = exchange_swept_native_for_group(
        bot.client,
        group,
        prefix,
        trade_cache=trade_cache,
    )
    remaining = (
        max(Decimal("0"), native - exchange_swept) if exchange_swept > 0 else remaining_spot_profit_native(group)
    )
    if remaining <= 0:
        return False

    status = str(group.profit_sweep_status or "").lower()
    if status == "filled":
        group.profit_sweep_status = "pending"
        group.profit_sweep_amount = exchange_swept if exchange_swept > 0 else group.profit_sweep_amount
        if not group.profit_sweep_reason:
            group.profit_sweep_reason = "manual_sweep"
        elif "resweep_remainder" not in group.profit_sweep_reason:
            group.profit_sweep_reason = f"{group.profit_sweep_reason}; resweep_remainder"
        return True

    if status in {"", "failed", "skipped"}:
        group.profit_sweep_status = "pending"
        group.profit_sweep_amount = exchange_swept if exchange_swept > 0 else native
        if not group.profit_sweep_reason:
            group.profit_sweep_reason = "manual_sweep"
        return True
    if status == "pending":
        swept = group.profit_sweep_amount if group.profit_sweep_amount > 0 else Decimal("0")
        if exchange_swept > 0 and exchange_swept < native:
            if swept != exchange_swept:
                group.profit_sweep_amount = exchange_swept
            return False
        if swept > 0 and swept < native:
            return False
        group.profit_sweep_amount = native
        return False
    return False


def reschedule_failed_profit_sweeps(
    bot: DeribitOptionTrialBot,
    groups: list[TradeGroup],
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> int:
    """Re-queue failed profit sweeps so the live manage cycle retries them."""
    rescheduled = 0
    cache = trade_cache or ProfitSweepTradeCache(bot.client)
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if str(group.profit_sweep_status or "").lower() != "failed":
            continue
        if _ensure_pending_for_manual_sweep(bot, group, trade_cache=cache):
            rescheduled += 1
    return rescheduled


def _preview_context(context: RuntimeContext) -> RuntimeContext:
    preview_state = copy.deepcopy(context.state)
    return replace(context, state=preview_state)


def _run_profit_sweep_pass(
    bot: DeribitOptionTrialBot,
    context: RuntimeContext,
    *,
    live: bool,
    group_id: str | None,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    prefix = bot.config.order_label_prefix
    scheduled = 0
    actions: list[dict[str, Any]] = []
    for group in context.state.groups:
        if group_id and group.group_id != group_id:
            continue
        if guard_profit_sweep_against_oversell(
            group,
            bot.client,
            prefix,
            trade_cache=trade_cache,
        ):
            continue
        remaining = _exchange_remaining_native(
            bot.client,
            group,
            prefix,
            trade_cache=trade_cache,
        )
        if remaining is None or remaining <= 0:
            continue
        if to_sweep_native(group) <= 0 and remaining <= 0:
            continue
        if _ensure_pending_for_manual_sweep(bot, group, trade_cache=trade_cache):
            scheduled += 1

    for group in context.state.groups:
        if group_id and group.group_id != group_id:
            continue
        if str(group.profit_sweep_status or "").lower() != "pending":
            continue
        remaining = _exchange_remaining_native(
            bot.client,
            group,
            prefix,
            trade_cache=trade_cache,
        )
        if remaining is None or remaining <= 0:
            continue
        if to_sweep_native(group) <= 0:
            continue
        actions.append(bot._execute_covered_call_profit_sweep(context, group, live=live))
    return scheduled, actions


def _apply_exchange_guards(
    bot: DeribitOptionTrialBot,
    groups: list[TradeGroup],
    *,
    group_id: str | None = None,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> int:
    blocked = 0
    target = str(group_id or "").strip()
    prefix = bot.config.order_label_prefix
    for group in groups:
        if target and group.group_id != target:
            continue
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if guard_profit_sweep_against_oversell(
            group,
            bot.client,
            prefix,
            trade_cache=trade_cache,
        ):
            blocked += 1
    return blocked


def run_remaining_profit_sweeps(
    bot: DeribitOptionTrialBot,
    *,
    live: bool = False,
    group_id: str | None = None,
    reconcile_only: bool = False,
) -> ProfitSweepRunSummary:
    """Reconcile, schedule, and execute remaining covered-call profit sweeps to USDT."""
    context = bot._load_runtime(live=live)
    summary = ProfitSweepRunSummary(live=live)
    trade_cache = ProfitSweepTradeCache(bot.client)

    if live and heal_reconciled_proceeds_drift(bot, context.state.groups):
        summary.reconciled += 1

    for group in context.state.groups:
        if group_id and group.group_id != group_id:
            continue
        if reconcile_profit_sweep_from_exchange(
            group,
            client=bot.client,
            order_label_prefix=bot.config.order_label_prefix,
            trade_cache=trade_cache,
        ):
            summary.reconciled += 1

    summary.blocked_oversweep = _apply_exchange_guards(
        bot,
        context.state.groups,
        group_id=group_id,
        trade_cache=trade_cache,
    )

    from .profit_sweep_dust import reconcile_dust_sweep_from_exchange

    dust_reconciled = reconcile_dust_sweep_from_exchange(
        bot,
        context.state.groups,
        trade_cache=trade_cache,
    )
    if dust_reconciled:
        summary.reconciled += dust_reconciled

    if live:
        bot._reconcile_profit_sweep_quote_proceeds(context)

    summary.candidates = list_remaining_profit_sweeps(context.state.groups, group_id=group_id)

    if reconcile_only:
        bot.state_store.save(context.state)
        summary.saved = True
        summary.candidates = list_remaining_profit_sweeps(context.state.groups, group_id=group_id)
        return summary

    if not live:
        preview = _preview_context(context)
        summary.scheduled, summary.actions = _run_profit_sweep_pass(
            bot,
            preview,
            live=False,
            group_id=group_id,
            trade_cache=trade_cache,
        )
        from .profit_sweep_dust import run_dust_pool_profit_sweeps

        summary.actions.extend(run_dust_pool_profit_sweeps(bot, preview, live=False, trade_cache=trade_cache))
        summary.saved = False
        return summary

    summary.scheduled, summary.actions = _run_profit_sweep_pass(
        bot,
        context,
        live=True,
        group_id=group_id,
        trade_cache=trade_cache,
    )

    if summary.actions:
        bot._persist_trade_journal_actions(summary.actions)

    from .profit_sweep_dust import run_dust_pool_profit_sweeps

    dust_actions = run_dust_pool_profit_sweeps(
        bot,
        context,
        live=live,
        trade_cache=trade_cache,
    )
    summary.actions.extend(dust_actions)
    if dust_actions and live:
        bot._persist_trade_journal_actions(dust_actions)

    bot.state_store.save(context.state)
    summary.saved = True
    summary.candidates = list_remaining_profit_sweeps(context.state.groups, group_id=group_id)
    return summary
