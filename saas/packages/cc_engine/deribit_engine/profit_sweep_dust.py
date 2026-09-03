"""Pool sub-minimum profit-sweep remainders and swap when the batch clears exchange minimums."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .models import TradeGroup
from .profit_sweep_ops import (
    ProfitSweepTradeCache,
    exchange_swept_native_for_group,
    guard_profit_sweep_against_oversell,
    native_profit_for_group,
    record_profit_sweep_lifetime_proceeds,
    remaining_spot_profit_native,
)
from .utils import align_option_order_amount, format_decimal, to_decimal

if TYPE_CHECKING:
    from .engine import DeribitOptionTrialBot
    from .engine.context import RuntimeContext


@dataclass
class DustRemainderRow:
    group_id: str
    currency: str
    remainder_native: Decimal
    closed_timestamp_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "currency": self.currency,
            "remainder_native": format_decimal(self.remainder_native, 8),
            "closed_timestamp_ms": self.closed_timestamp_ms,
        }


def dust_sweep_order_label(order_label_prefix: str, currency: str) -> str:
    return f"{order_label_prefix}-profit-sweep-dust-{currency.lower()}"


def dust_sweep_trades_for_currency(
    client,
    order_label_prefix: str,
    currency: str,
) -> list[dict[str, Any]]:
    """Spot sells tagged with the dust-pool order label."""
    label = dust_sweep_order_label(order_label_prefix, currency)
    trades: list[dict[str, Any]] = []
    fetch = getattr(client, "get_user_trades_by_currency", None)
    if not callable(fetch):
        return trades
    cursor_ts = 0
    seen: set = set()
    try:
        while True:
            payload = fetch(
                currency,
                kind="spot",
                count=1000,
                sorting="asc",
                historical=True,
                start_timestamp=cursor_ts if cursor_ts > 0 else None,
            )
            batch = list(payload.get("trades") or [])
            if not batch:
                break
            for trade in batch:
                if str(trade.get("label") or "") != label:
                    continue
                if str(trade.get("direction") or "").lower() != "sell":
                    continue
                trade_id = trade.get("trade_id")
                if trade_id in seen:
                    continue
                if trade_id is not None:
                    seen.add(trade_id)
                trades.append(trade)
            if not payload.get("has_more"):
                break
            last_ts = int(batch[-1].get("timestamp") or 0)
            if last_ts <= 0 or last_ts <= cursor_ts:
                break
            cursor_ts = last_ts + 1
    except Exception:
        return trades
    trades.sort(key=lambda row: int(row.get("timestamp") or 0))
    return trades


def exchange_dust_swept_native(
    client,
    order_label_prefix: str,
    currency: str,
) -> Decimal:
    """Native already sold via dust-pool label (prevents repeat dust oversells)."""
    return sum(
        (
            to_decimal(trade.get("amount"))
            for trade in dust_sweep_trades_for_currency(client, order_label_prefix, currency)
        ),
        Decimal("0"),
    )


def dust_allocated_native_in_state(
    groups: list[TradeGroup],
    client,
    order_label_prefix: str,
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> Decimal:
    """Native profit-sweep amount already attributed to dust-pool fills in state."""
    total = Decimal("0")
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if "dust_pool_sweep" not in str(group.profit_sweep_reason or ""):
            continue
        per_group = exchange_swept_native_for_group(
            client,
            group,
            order_label_prefix,
            trade_cache=trade_cache,
        )
        total += max(Decimal("0"), group.profit_sweep_amount - per_group)
    return total


def reconcile_dust_sweep_from_exchange(
    bot: DeribitOptionTrialBot,
    groups: list[TradeGroup],
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> int:
    """Backfill group profit_sweep_* from exchange dust-pool sells not yet allocated."""
    prefix = bot.config.order_label_prefix
    cache = trade_cache or ProfitSweepTradeCache(bot.client)
    updated = 0
    for currency in ("BTC", "ETH"):
        dust_trades = dust_sweep_trades_for_currency(bot.client, prefix, currency)
        if not dust_trades:
            continue
        dust_sold = sum((to_decimal(t.get("amount")) for t in dust_trades), Decimal("0"))
        if dust_sold <= 0:
            continue
        from .wallet_ops import spot_sell_quote_proceeds_from_trades

        dust_proceeds = spot_sell_quote_proceeds_from_trades(dust_trades, quote_currency="USDT")
        allocated = dust_allocated_native_in_state(groups, bot.client, prefix, trade_cache=cache)
        unallocated = dust_sold - allocated
        if unallocated <= 0:
            continue
        pool_rows = [
            row
            for row in collect_dust_remainder_rows(bot, groups, trade_cache=cache)
            if row.currency == currency.upper()
        ]
        if not pool_rows:
            continue
        proceeds_share = dust_proceeds * (unallocated / dust_sold) if dust_sold > 0 else Decimal("0")
        allocated_groups = apply_dust_sweep_allocation(
            groups,
            pool_rows,
            sold_native=unallocated,
            proceeds_usdt=proceeds_share,
        )
        if allocated_groups:
            updated += len(allocated_groups)
    return updated


def _is_dust_remainder(remainder: Decimal, *, contract_size: Decimal, min_trade_amount: Decimal) -> bool:
    if remainder <= 0:
        return False
    return align_option_order_amount(remainder, contract_size, min_trade_amount) <= 0


def collect_dust_remainder_rows(
    bot: DeribitOptionTrialBot,
    groups: list[TradeGroup],
    *,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> list[DustRemainderRow]:
    rows: list[DustRemainderRow] = []
    prefix = bot.config.order_label_prefix
    cache = trade_cache or ProfitSweepTradeCache(bot.client)
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group() or not group.is_coin_collateral():
            continue
        guard_profit_sweep_against_oversell(group, bot.client, prefix, trade_cache=cache)
        remainder = remaining_spot_profit_native(group)
        if remainder <= 0:
            continue
        native = native_profit_for_group(group)
        if native is None or native <= 0:
            continue
        exchange_swept = exchange_swept_native_for_group(
            bot.client,
            group,
            prefix,
            trade_cache=cache,
        )
        if exchange_swept >= native:
            continue
        instrument = bot._covered_call_profit_sweep_instrument(group.currency)
        contract_size, min_trade_amount = bot._spot_min_trade_amount(instrument, group.currency)
        if not _is_dust_remainder(remainder, contract_size=contract_size, min_trade_amount=min_trade_amount):
            continue
        rows.append(
            DustRemainderRow(
                group_id=group.group_id,
                currency=group.currency.upper(),
                remainder_native=remainder,
                closed_timestamp_ms=int(group.closed_timestamp_ms or 0),
            )
        )
    rows.sort(key=lambda row: (row.currency, row.closed_timestamp_ms, int(row.group_id)))
    return rows


def _pool_rows(rows: list[DustRemainderRow], currency: str) -> tuple[list[DustRemainderRow], Decimal]:
    picked = [row for row in rows if row.currency == currency.upper()]
    total = sum((row.remainder_native for row in picked), Decimal(0))
    return picked, total


def dust_pool_sell_budget(pool_total: Decimal, exchange_deficit_native: Decimal) -> Decimal:
    """Native dust pool may sell: capped by state remainder pool and exchange deficit."""
    if pool_total <= 0 or exchange_deficit_native <= 0:
        return Decimal("0")
    return min(pool_total, exchange_deficit_native)


def _exchange_premium_deficit_native(bot, groups: list[TradeGroup], currency: str) -> Decimal:
    from .profit_sweep_repair import build_premium_alignment_plan

    plan = build_premium_alignment_plan(bot.client, groups)
    return plan.sell_native.get(currency.upper(), Decimal("0"))


def _available_native(
    bot: DeribitOptionTrialBot,
    context: RuntimeContext,
    currency: str,
    *,
    live: bool,
) -> Decimal:
    """Free native after open covered-call collateral (same cap as per-group sweeps)."""
    return bot._profit_sweep_sellable_native_cap(context, currency, live=live)


def apply_dust_sweep_allocation(
    groups: list[TradeGroup],
    pool_rows: list[DustRemainderRow],
    *,
    sold_native: Decimal,
    proceeds_usdt: Decimal,
) -> list[str]:
    if sold_native <= 0:
        return []
    by_id = {group.group_id: group for group in groups}
    allocated: list[str] = []
    left = sold_native
    for row in pool_rows:
        if left <= 0:
            break
        group = by_id.get(row.group_id)
        if group is None:
            continue
        native = native_profit_for_group(group)
        if native is None or native <= 0:
            continue
        rem = remaining_spot_profit_native(group)
        chunk = min(rem, left, row.remainder_native)
        if chunk <= 0:
            continue
        share = chunk / sold_native
        quote = proceeds_usdt * share
        prior = group.profit_sweep_amount if group.profit_sweep_amount > 0 else Decimal(0)
        group.profit_sweep_amount = min(native, prior + chunk)
        group.profit_sweep_quote_proceeds += quote
        record_profit_sweep_lifetime_proceeds(group, group.profit_sweep_quote_proceeds)
        group.profit_sweep_instrument_name = f"{group.currency.upper()}_USDT"
        if remaining_spot_profit_native(group) <= 0:
            group.profit_sweep_status = "filled"
        reason = str(group.profit_sweep_reason or "")
        if "dust_pool_sweep" not in reason:
            group.profit_sweep_reason = (reason + "; dust_pool_sweep").strip("; ")
        allocated.append(group.group_id)
        left -= chunk
    return allocated


def run_dust_pool_profit_sweeps(
    bot: DeribitOptionTrialBot,
    context: RuntimeContext,
    *,
    live: bool = False,
    trade_cache: ProfitSweepTradeCache | None = None,
) -> list[dict[str, Any]]:
    if not bot.config.covered_call_profit_sweep_enabled:
        return []
    if not bot.config.covered_call_profit_sweep_dust_pool_enabled:
        return []

    cache = trade_cache or ProfitSweepTradeCache(bot.client)
    if live:
        reconcile_dust_sweep_from_exchange(bot, context.state.groups, trade_cache=cache)
    rows = collect_dust_remainder_rows(bot, context.state.groups, trade_cache=cache)
    if not rows:
        return []

    actions: list[dict[str, Any]] = []
    prefix = bot.config.order_label_prefix
    for currency in ("BTC", "ETH"):
        pool_rows, pool_total = _pool_rows(rows, currency)
        if not pool_rows or pool_total <= 0:
            continue

        instrument_name = bot._covered_call_profit_sweep_instrument(currency)
        contract_size, min_trade_amount = bot._spot_min_trade_amount(instrument_name, currency)
        exchange_deficit = _exchange_premium_deficit_native(bot, context.state.groups, currency) if live else pool_total
        remaining_pool = dust_pool_sell_budget(pool_total, exchange_deficit)
        if live and remaining_pool <= 0 and pool_total > 0:
            from .profit_sweep_ops import sync_filled_profit_sweep_amounts_to_premium

            reconciled = reconcile_dust_sweep_from_exchange(
                bot,
                context.state.groups,
                trade_cache=cache,
            )
            synced = sync_filled_profit_sweep_amounts_to_premium(
                context.state.groups,
                currency=currency,
            )
            actions.append(
                {
                    "action": "covered_call_profit_dust_sweep_state_sync",
                    "currency": currency,
                    "pool_native": format_decimal(pool_total, 8),
                    "exchange_deficit_native": format_decimal(exchange_deficit, 8),
                    "reconciled_groups": reconciled,
                    "synced_groups": synced,
                    "live": live,
                }
            )
            continue
        available = _available_native(bot, context, currency, live=live) if live else remaining_pool
        target = min(remaining_pool, available)
        order_amount = align_option_order_amount(target, contract_size, min_trade_amount)
        if order_amount > remaining_pool:
            order_amount = align_option_order_amount(remaining_pool, contract_size, min_trade_amount)
        if order_amount <= 0 or order_amount > remaining_pool:
            actions.append(
                {
                    "action": "covered_call_profit_dust_sweep_skipped",
                    "currency": currency,
                    "reason": "pool_below_min_trade",
                    "pool_native": format_decimal(pool_total, 8),
                    "min_trade_amount": format_decimal(min_trade_amount, 8),
                    "group_count": len(pool_rows),
                    "live": live,
                }
            )
            continue

        payload: dict[str, Any] = {
            "action": "covered_call_profit_dust_sweep" if live else "covered_call_profit_dust_sweep_preview",
            "currency": currency,
            "instrument_name": instrument_name,
            "pool_native": format_decimal(pool_total, 8),
            "amount": format_decimal(order_amount, 8),
            "group_count": len(pool_rows),
            "groups": [row.group_id for row in pool_rows],
            "live": live,
        }
        if not live:
            actions.append(payload)
            continue

        label = dust_sweep_order_label(prefix, currency)
        try:
            from .wallet_ops import trade_spot

            result = trade_spot(
                bot.config,
                bot.client,
                from_currency=currency,
                to_currency="USDT",
                amount=format_decimal(order_amount, 8),
                instrument_name=instrument_name,
                order_type=bot.config.covered_call_spot_order_type,
                live=True,
                label=label,
            )
        except Exception as exc:
            payload["action"] = "covered_call_profit_dust_sweep_failed"
            payload["error"] = str(exc)
            actions.append(payload)
            continue

        if result.get("action") == "trade_spot_skipped":
            payload["action"] = "covered_call_profit_dust_sweep_skipped"
            payload["reason"] = str(result.get("reason") or "skipped")
            actions.append(payload)
            continue

        from .wallet_ops import spot_sell_quote_proceeds_from_trades

        proceeds = spot_sell_quote_proceeds_from_trades(
            bot._order_trades(result.get("response")),
            quote_currency="USDT",
        )
        filled_native = to_decimal(result.get("amount") or order_amount)
        if filled_native <= 0:
            filled_native = order_amount
        allocated = apply_dust_sweep_allocation(
            context.state.groups,
            pool_rows,
            sold_native=min(filled_native, remaining_pool, pool_total),
            proceeds_usdt=proceeds,
        )
        order_id = result.get("order_id")
        payload["order_id"] = order_id
        payload["allocated_groups"] = allocated
        if proceeds > 0:
            payload["profit_sweep_quote_proceeds"] = format_decimal(proceeds, 4)
        payload["response"] = result.get("response")
        actions.append(payload)
    return actions
