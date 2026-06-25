"""Detect duplicate profit sweeps, repair state, and buy back over-sold native."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .models import TradeGroup
from .profit_sweep_dust import dust_sweep_order_label
from .profit_sweep_ops import (
    _first_day_profit_sweep_trades,
    native_profit_for_group,
    profit_sweep_has_exchange_fill,
    realized_spot_profit_native_for_group,
    record_profit_sweep_lifetime_proceeds,
)
from .utils import format_decimal, to_decimal
from .wallet_ops import spot_sell_quote_proceeds_from_trades

if TYPE_CHECKING:
    from .client import DeribitClient
    from .config import BotConfig


def _iter_spot_trades(client: DeribitClient, currency: str):
    """Paginate spot fills so profit-sweep / buyback totals are complete."""
    seen: set[Any] = set()

    def _yield_trades(trades: list[dict[str, Any]]):
        for trade in trades:
            trade_id = trade.get("trade_id")
            if trade_id in seen:
                continue
            if trade_id is not None:
                seen.add(trade_id)
            yield trade

    recent = client.get_user_trades_by_currency(
        currency,
        kind="spot",
        count=1000,
        historical=False,
    )
    yield from _yield_trades(list(recent.get("trades") or []))

    cursor_ts = 0
    while True:
        batch = client.get_user_trades_by_currency(
            currency,
            kind="spot",
            count=1000,
            sorting="asc",
            historical=True,
            start_timestamp=cursor_ts if cursor_ts > 0 else None,
        )
        trades = list(batch.get("trades") or [])
        if not trades:
            break
        yield from _yield_trades(trades)
        if not batch.get("has_more"):
            break
        last_ts = int(trades[-1].get("timestamp") or 0)
        if last_ts <= 0 or last_ts <= cursor_ts:
            break
        cursor_ts = last_ts + 1


def _group_id_from_label(label: str) -> str | None:
    text = str(label or "")
    if "profit-sweep" not in text:
        return None
    if "profit-sweep-dust-" in text or "profit-sweep-buyback-" in text:
        return None
    gid = text.split("-")[-1]
    if not gid.isdigit():
        return None
    return gid


def _unique_profit_sweep_sells(client: DeribitClient, currency: str) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for trade in _iter_spot_trades(client, currency):
        if "USDT" not in str(trade.get("instrument_name") or ""):
            continue
        if str(trade.get("direction") or "").lower() != "sell":
            continue
        if "profit-sweep" not in str(trade.get("label") or ""):
            continue
        trade_id = trade.get("trade_id")
        if trade_id in seen:
            continue
        seen.add(trade_id)
        out.append(trade)
    return out


def _trade_day(trade: dict[str, Any]) -> str:
    ts = int(trade.get("timestamp") or 0)
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d")


@dataclass
class ProfitSweepTradeLedger:
    group_id: str
    currency: str
    days: list[str]
    first_day_trades: list[dict[str, Any]] = field(default_factory=list)
    duplicate_trades: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_duplicate(self) -> bool:
        return bool(self.duplicate_trades)

    @property
    def first_native(self) -> Decimal:
        return sum(to_decimal(t.get("amount")) for t in self.first_day_trades)

    @property
    def duplicate_native(self) -> Decimal:
        return sum(to_decimal(t.get("amount")) for t in self.duplicate_trades)

    @property
    def first_proceeds(self) -> Decimal:
        return spot_sell_quote_proceeds_from_trades(self.first_day_trades, quote_currency="USDT")

    @property
    def duplicate_proceeds(self) -> Decimal:
        return spot_sell_quote_proceeds_from_trades(self.duplicate_trades, quote_currency="USDT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "currency": self.currency,
            "days": self.days,
            "first_native": format_decimal(self.first_native, 8),
            "duplicate_native": format_decimal(self.duplicate_native, 8),
            "first_proceeds_usdt": format_decimal(self.first_proceeds, 4),
            "duplicate_proceeds_usdt": format_decimal(self.duplicate_proceeds, 4),
        }


@dataclass
class ProfitSweepRepairPlan:
    ledgers: list[ProfitSweepTradeLedger] = field(default_factory=list)
    buyback_native: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(0), "ETH": Decimal(0)})
    buyback_usdt: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(0), "ETH": Decimal(0)})
    state_proceeds_before: Decimal = Decimal(0)
    state_proceeds_after: Decimal = Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duplicate_groups": len([row for row in self.ledgers if row.has_duplicate]),
            "ledgers": [row.to_dict() for row in self.ledgers if row.has_duplicate],
            "buyback_native": {k: format_decimal(v, 8) for k, v in self.buyback_native.items() if v > 0},
            "buyback_usdt_estimate": {k: format_decimal(v, 4) for k, v in self.buyback_usdt.items() if v > 0},
            "state_proceeds_before": format_decimal(self.state_proceeds_before, 4),
            "state_proceeds_after": format_decimal(self.state_proceeds_after, 4),
        }


def _already_repaired_duplicate_sweep(group: TradeGroup | None) -> bool:
    if group is None:
        return False
    return "duplicate_sweep_repaired" in str(group.profit_sweep_reason or "")


def build_profit_sweep_repair_plan(
    client: DeribitClient,
    groups: list[TradeGroup],
) -> ProfitSweepRepairPlan:
    group_by_id = {g.group_id: g for g in groups}
    by_gid: dict[str, list[dict[str, Any]]] = {}
    for currency in ("BTC", "ETH"):
        for trade in _unique_profit_sweep_sells(client, currency):
            gid = _group_id_from_label(str(trade.get("label") or ""))
            if gid:
                by_gid.setdefault(gid, []).append(trade)

    plan = ProfitSweepRepairPlan()

    for gid in sorted(by_gid, key=lambda value: int(value)):
        trades = sorted(by_gid[gid], key=lambda row: int(row.get("timestamp") or 0))
        days = sorted({_trade_day(t) for t in trades})
        ledger = ProfitSweepTradeLedger(
            group_id=gid,
            currency=str(trades[0].get("instrument_name") or "").split("_")[0] or "?",
            days=days,
        )
        if len(days) <= 1:
            plan.ledgers.append(ledger)
            continue
        first_day = days[0]
        ledger.first_day_trades = [t for t in trades if _trade_day(t) == first_day]
        ledger.duplicate_trades = [t for t in trades if _trade_day(t) != first_day]
        plan.ledgers.append(ledger)
        if ledger.duplicate_native > 0 and not _already_repaired_duplicate_sweep(group_by_id.get(gid)):
            plan.buyback_native[ledger.currency] = (
                plan.buyback_native.get(ledger.currency, Decimal(0)) + ledger.duplicate_native
            )
            plan.buyback_usdt[ledger.currency] = (
                plan.buyback_usdt.get(ledger.currency, Decimal(0)) + ledger.duplicate_proceeds
            )

    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        plan.state_proceeds_before += group.profit_sweep_quote_proceeds

    repaired_ids = {ledger.group_id for ledger in plan.ledgers if ledger.has_duplicate}
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if group.group_id in repaired_ids:
            ledger = next(row for row in plan.ledgers if row.group_id == group.group_id)
            plan.state_proceeds_after += ledger.first_proceeds
        else:
            plan.state_proceeds_after += group.profit_sweep_quote_proceeds

    return plan


def apply_profit_sweep_state_repairs(groups: list[TradeGroup], plan: ProfitSweepRepairPlan) -> int:
    group_by_id = {g.group_id: g for g in groups}
    repaired = 0
    for ledger in plan.ledgers:
        if not ledger.has_duplicate:
            continue
        group = group_by_id.get(ledger.group_id)
        if group is None:
            continue
        native_cap = native_profit_for_group(group)
        group.profit_sweep_status = "filled"
        group.profit_sweep_amount = min(ledger.first_native, native_cap) if native_cap else ledger.first_native
        group.profit_sweep_quote_proceeds = ledger.first_proceeds
        record_profit_sweep_lifetime_proceeds(group, ledger.first_proceeds)
        group.profit_sweep_instrument_name = f"{group.currency.upper()}_USDT"
        last = ledger.first_day_trades[-1]
        order_id = str(last.get("order_id") or "").strip()
        if order_id:
            group.profit_sweep_order_id = order_id
        reason = str(group.profit_sweep_reason or "")
        if "duplicate_sweep_repaired" not in reason:
            group.profit_sweep_reason = (reason + "; duplicate_sweep_repaired").strip("; ")
        repaired += 1
    return repaired


def _quote_spend_for_base_buy(
    client: DeribitClient,
    *,
    instrument_name: str,
    base_amount: Decimal,
    proceeds_budget: Decimal,
) -> Decimal:
    from .wallet_ops import _lookup_spot_instrument, _spot_trade_price_quote

    instrument = _lookup_spot_instrument(client, instrument_name, instrument_name.split("_")[0])
    trade_price, _, _ = _spot_trade_price_quote(
        client,
        instrument_name,
        direction="buy",
        order_type="market",
        instrument=instrument,
        limit_price=None,
    )
    if trade_price <= 0:
        return proceeds_budget
    notional = base_amount * trade_price * Decimal("1.02")
    return max(notional, proceeds_budget)


def _profit_swap_sell_native(client: DeribitClient, currency: str) -> Decimal:
    total = Decimal("0")
    for trade in _iter_spot_trades(client, currency):
        label = str(trade.get("label") or "")
        if str(trade.get("direction") or "").lower() != "sell":
            continue
        if "profit-sweep" not in label and "spot-exit" not in label and "profit-sweep-align" not in label:
            continue
        total += to_decimal(trade.get("amount"))
    return total


def _profit_swap_buyback_native(client: DeribitClient, currency: str) -> Decimal:
    total = Decimal("0")
    for trade in _iter_spot_trades(client, currency):
        label = str(trade.get("label") or "")
        if str(trade.get("direction") or "").lower() != "buy":
            continue
        if "profit-sweep-buyback" not in label:
            continue
        total += to_decimal(trade.get("amount"))
    return total


@dataclass
class PremiumAlignmentPlan:
    premium_native: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(0), "ETH": Decimal(0)})
    net_sold_native: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(0), "ETH": Decimal(0)})
    buyback_native: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(0), "ETH": Decimal(0)})
    sell_native: dict[str, Decimal] = field(default_factory=lambda: {"BTC": Decimal(0), "ETH": Decimal(0)})

    def to_dict(self) -> dict[str, Any]:
        return {
            "premium_native": {k: format_decimal(v, 8) for k, v in self.premium_native.items()},
            "net_sold_native": {k: format_decimal(v, 8) for k, v in self.net_sold_native.items()},
            "buyback_native": {k: format_decimal(v, 8) for k, v in self.buyback_native.items() if v > 0},
            "sell_native": {k: format_decimal(v, 8) for k, v in self.sell_native.items() if v > 0},
        }


def build_premium_alignment_plan(
    client: DeribitClient,
    groups: list[TradeGroup],
) -> PremiumAlignmentPlan:
    plan = PremiumAlignmentPlan()
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        native = realized_spot_profit_native_for_group(group)
        if native is None or native <= 0:
            continue
        plan.premium_native[group.currency.upper()] += native
    for currency in ("BTC", "ETH"):
        sold = _profit_swap_sell_native(client, currency)
        bought = _profit_swap_buyback_native(client, currency)
        net = sold - bought
        plan.net_sold_native[currency] = net
        plan.buyback_native[currency] = max(Decimal("0"), net - plan.premium_native[currency])
        plan.sell_native[currency] = max(Decimal("0"), plan.premium_native[currency] - net)
    return plan


def _premium_align_sell_label(order_label_prefix: str, currency: str) -> str:
    return f"{order_label_prefix}-profit-sweep-align-{currency.lower()}"


def execute_premium_deficit_sell(
    bot,
    sell_native: dict[str, Decimal],
    *,
    live: bool = False,
) -> list[dict[str, Any]]:
    from .utils import align_option_order_amount
    from .wallet_ops import trade_spot

    actions: list[dict[str, Any]] = []
    for currency in ("BTC", "ETH"):
        deficit = sell_native.get(currency, Decimal(0))
        if deficit <= 0:
            continue
        instrument_name = bot._covered_call_profit_sweep_instrument(currency)
        contract_size, min_trade_amount = bot._spot_min_trade_amount(instrument_name, currency)
        order_amount = align_option_order_amount(deficit, contract_size, min_trade_amount)
        if order_amount <= 0 or order_amount > deficit:
            actions.append(
                {
                    "action": "premium_align_sell_skipped",
                    "currency": currency,
                    "deficit_native": format_decimal(deficit, 8),
                    "reason": "below_min_trade_or_misaligned",
                    "live": live,
                }
            )
            continue
        label = _premium_align_sell_label(bot.config.order_label_prefix, currency)
        result = trade_spot(
            bot.config,
            bot.client,
            from_currency=currency,
            to_currency="USDT",
            amount=format_decimal(order_amount, 8),
            instrument_name=instrument_name,
            order_type=bot.config.covered_call_spot_order_type,
            live=live,
            label=label,
        )
        result["premium_align_sell_target"] = format_decimal(deficit, 8)
        result["premium_align_sell_amount"] = format_decimal(order_amount, 8)
        actions.append(result)
    return actions


def execute_buyback_native(
    config: BotConfig,
    client: DeribitClient,
    buyback_native: dict[str, Decimal],
    *,
    live: bool = False,
) -> list[dict[str, Any]]:
    from .wallet_ops import trade_spot

    actions: list[dict[str, Any]] = []
    for currency in ("BTC", "ETH"):
        base_amount = buyback_native.get(currency, Decimal(0))
        if base_amount <= 0:
            continue
        instrument = f"{currency}_USDT"
        quote_spend = _quote_spend_for_base_buy(
            client,
            instrument_name=instrument,
            base_amount=base_amount,
            proceeds_budget=Decimal("0"),
        )
        result = trade_spot(
            config,
            client,
            from_currency="USDT",
            to_currency=currency,
            amount=format_decimal(quote_spend, 4),
            instrument_name=instrument,
            order_type=config.covered_call_spot_order_type,
            live=live,
            label=f"{config.order_label_prefix}-profit-sweep-buyback-{currency.lower()}",
        )
        result["buyback_base_target"] = format_decimal(base_amount, 8)
        result["buyback_quote_budget"] = format_decimal(quote_spend, 4)
        actions.append(result)
    return actions


def execute_profit_sweep_buyback(
    config: BotConfig,
    client: DeribitClient,
    plan: ProfitSweepRepairPlan,
    *,
    live: bool = False,
) -> list[dict[str, Any]]:
    return execute_buyback_native(config, client, plan.buyback_native, live=live)


def _buyback_usdt_for_currency(client: DeribitClient, currency: str) -> Decimal:
    total = Decimal("0")
    for trade in _iter_spot_trades(client, currency):
        label = str(trade.get("label") or "")
        if str(trade.get("direction") or "").lower() != "buy":
            continue
        if "profit-sweep-buyback" not in label:
            continue
        amount = to_decimal(trade.get("amount"))
        price = to_decimal(trade.get("price"))
        if amount > 0 and price > 0:
            total += amount * price
    return total


def _classify_profit_sweep_sell_trades(
    client: DeribitClient,
    order_label_prefix: str,
    currency: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-group (by gid), dust-pool, and align sells — excludes buyback."""
    dust_label = dust_sweep_order_label(order_label_prefix, currency)
    align_label = _premium_align_sell_label(order_label_prefix, currency)
    by_gid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dust_trades: list[dict[str, Any]] = []
    align_trades: list[dict[str, Any]] = []
    for trade in _iter_spot_trades(client, currency):
        label = str(trade.get("label") or "")
        if str(trade.get("direction") or "").lower() != "sell":
            continue
        if "profit-sweep" not in label and "spot-exit" not in label:
            continue
        if "profit-sweep-buyback" in label:
            continue
        if label == dust_label:
            dust_trades.append(trade)
            continue
        if label == align_label:
            align_trades.append(trade)
            continue
        gid = _group_id_from_label(label)
        if gid:
            by_gid[gid].append(trade)
    return by_gid, dust_trades, align_trades


def _wallet_usdt_balance(client: DeribitClient) -> Decimal:
    fetch = getattr(client, "get_account_summaries", None)
    if not callable(fetch):
        return Decimal("0")
    try:
        rows = fetch(extended=False) or []
    except TypeError:
        rows = fetch() or []
    except Exception:
        return Decimal("0")
    for row in rows:
        if str(row.get("currency") or "").upper() != "USDT":
            continue
        for key in ("balance", "available_funds", "equity"):
            value = to_decimal(row.get(key))
            if value > 0:
                return value
    return Decimal("0")


def _premium_usdt_weight(group: TradeGroup) -> Decimal:
    native = realized_spot_profit_native_for_group(group)
    if native is None or native <= 0:
        return Decimal("0")
    idx = group.close_index_usd or group.entry_index_usd
    if idx is None or idx <= 0:
        return native
    return native * idx


def labeled_fill_proceeds_by_group(
    client: DeribitClient,
    order_label_prefix: str,
) -> dict[str, Decimal]:
    """Per-group USDT from labeled profit-sweep spot sells (first fill day only)."""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for currency in ("BTC", "ETH"):
        by_gid, _, _ = _classify_profit_sweep_sell_trades(client, order_label_prefix, currency)
        for gid, trades in by_gid.items():
            first_day = _first_day_profit_sweep_trades(trades)
            out[gid] += spot_sell_quote_proceeds_from_trades(first_day, quote_currency="USDT")
    return dict(out)


def repair_labeled_profit_sweep_proceeds_in_groups(
    groups: list[TradeGroup],
    client: DeribitClient,
    order_label_prefix: str,
) -> int:
    """Restore profit_sweep_quote_proceeds from per-group labeled exchange fills."""
    from .trade_journal_backfill import group_excluded_from_premium_proceeds_pool

    direct_by_gid = labeled_fill_proceeds_by_group(client, order_label_prefix)
    if not direct_by_gid:
        return 0
    repaired = 0
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if group_excluded_from_premium_proceeds_pool(group):
            continue
        proceeds = direct_by_gid.get(group.group_id, Decimal("0"))
        if proceeds <= 0:
            continue
        tol = max(Decimal("0.01"), proceeds * Decimal("0.005"))
        if abs(group.profit_sweep_quote_proceeds - proceeds) <= tol:
            continue
        group.profit_sweep_status = "filled"
        group.profit_sweep_quote_proceeds = proceeds
        record_profit_sweep_lifetime_proceeds(group, proceeds)
        repaired += 1
    return repaired


def gross_premium_sweep_usdt_total(
    client: DeribitClient,
    order_label_prefix: str,
) -> Decimal:
    """Sum of all premium-sweep spot sell USDT (every fill day, incl. duplicates)."""
    total = Decimal("0")
    for currency in ("BTC", "ETH"):
        by_gid, dust_trades, align_trades = _classify_profit_sweep_sell_trades(
            client,
            order_label_prefix,
            currency,
        )
        for trades in by_gid.values():
            total += spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
        total += spot_sell_quote_proceeds_from_trades(dust_trades, quote_currency="USDT")
        total += spot_sell_quote_proceeds_from_trades(align_trades, quote_currency="USDT")
    return total


def actual_premium_sweep_usdt_net(
    client: DeribitClient,
    order_label_prefix: str,
) -> Decimal:
    """Net USDT from premium sweeps: all sell proceeds minus buybacks (matches trade log)."""
    gross = gross_premium_sweep_usdt_total(client, order_label_prefix)
    buyback = sum(
        (_buyback_usdt_for_currency(client, currency) for currency in ("BTC", "ETH")),
        Decimal("0"),
    )
    return max(gross - buyback, Decimal("0"))


def _buyback_native_for_currency(client: DeribitClient, currency: str) -> Decimal:
    total = Decimal("0")
    for trade in _iter_spot_trades(client, currency):
        label = str(trade.get("label") or "")
        if str(trade.get("direction") or "").lower() != "buy":
            continue
        if "profit-sweep-buyback" not in label:
            continue
        total += to_decimal(trade.get("amount"))
    return total


def premium_sweep_fill_stats_for_currency(
    client: DeribitClient,
    order_label_prefix: str,
    currency: str,
) -> dict[str, str]:
    """Exchange VWAP stats for premium-sweep spot sells (gross fills and net after buyback)."""
    by_gid, dust_trades, align_trades = _classify_profit_sweep_sell_trades(
        client,
        order_label_prefix,
        currency,
    )
    sell_trades: list[dict[str, Any]] = []
    for trades in by_gid.values():
        sell_trades.extend(trades)
    sell_trades.extend(dust_trades)
    sell_trades.extend(align_trades)
    gross_native = sum((to_decimal(t.get("amount")) for t in sell_trades), Decimal("0"))
    gross_usdt = spot_sell_quote_proceeds_from_trades(sell_trades, quote_currency="USDT")
    buyback_native = _buyback_native_for_currency(client, currency)
    buyback_usdt = _buyback_usdt_for_currency(client, currency)
    net_native = max(gross_native - buyback_native, Decimal("0"))
    net_usdt = max(gross_usdt - buyback_usdt, Decimal("0"))

    from .trade_journal_backfill import _collect_unlabeled_premium_sell_trades

    unlabeled_trades = _collect_unlabeled_premium_sell_trades(client, currency)
    unlabeled_native = sum((to_decimal(t.get("amount")) for t in unlabeled_trades), Decimal("0"))
    unlabeled_usdt = spot_sell_quote_proceeds_from_trades(unlabeled_trades, quote_currency="USDT")
    display_native = net_native + unlabeled_native
    display_usdt = net_usdt + unlabeled_usdt

    def _avg(usdt: Decimal, native: Decimal) -> str:
        if native <= 0:
            return "0"
        return format_decimal(usdt / native, 2)

    return {
        "gross_native_sold": format_decimal(gross_native, 8),
        "gross_usdt": format_decimal(gross_usdt, 4),
        "gross_avg_price_usd": _avg(gross_usdt, gross_native),
        "buyback_native": format_decimal(buyback_native, 8),
        "buyback_usdt": format_decimal(buyback_usdt, 4),
        "net_native_sold": format_decimal(net_native, 8),
        "net_usdt": format_decimal(net_usdt, 4),
        "net_avg_price_usd": _avg(net_usdt, net_native),
        "unlabeled_native_sold": format_decimal(unlabeled_native, 8),
        "unlabeled_usdt": format_decimal(unlabeled_usdt, 4),
        "display_native_sold": format_decimal(display_native, 8),
        "display_usdt": format_decimal(display_usdt, 4),
        "display_avg_price_usd": _avg(display_usdt, display_native),
    }


def premium_sweep_fill_stats_by_book(
    client: DeribitClient,
    order_label_prefix: str,
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for currency in ("BTC", "ETH"):
        stats = premium_sweep_fill_stats_for_currency(client, order_label_prefix, currency)
        if to_decimal(stats["gross_native_sold"]) > 0 or to_decimal(stats["net_usdt"]) > 0:
            out[currency] = stats
    return out


def net_premium_sweep_usdt_total(
    client: DeribitClient,
    order_label_prefix: str,
) -> Decimal:
    """Sum of exchange-attributed premium sweep USDT across BTC/ETH books."""
    return sum(
        (net_premium_sweep_usdt_for_currency(client, order_label_prefix, currency) for currency in ("BTC", "ETH")),
        Decimal("0"),
    )


def net_premium_sweep_usdt_for_currency(
    client: DeribitClient,
    order_label_prefix: str,
    currency: str,
) -> Decimal:
    """Net USDT from premium sweeps: first-day per-group + dust + align sells, minus buyback."""
    by_gid, dust_trades, align_trades = _classify_profit_sweep_sell_trades(
        client,
        order_label_prefix,
        currency,
    )
    total = Decimal("0")
    for trades in by_gid.values():
        first_day = _first_day_profit_sweep_trades(trades)
        total += spot_sell_quote_proceeds_from_trades(first_day, quote_currency="USDT")
    total += spot_sell_quote_proceeds_from_trades(dust_trades, quote_currency="USDT")
    total += spot_sell_quote_proceeds_from_trades(align_trades, quote_currency="USDT")
    total -= _buyback_usdt_for_currency(client, currency)
    return max(total, Decimal("0"))


def _exchange_gross_usdt_weights(
    client: DeribitClient,
    order_label_prefix: str,
    books: dict[str, list[tuple[TradeGroup, Decimal]]],
) -> dict[str, Decimal]:
    """Per-group gross USDT weight from exchange fills + dust/align pool share."""
    gross_by_gid: dict[str, Decimal] = defaultdict(Decimal)
    pool_by_currency: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    for currency in ("BTC", "ETH"):
        by_gid, dust_trades, align_trades = _classify_profit_sweep_sell_trades(
            client,
            order_label_prefix,
            currency,
        )
        for gid, trades in by_gid.items():
            gross_by_gid[gid] += spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
        pool_by_currency[currency] += spot_sell_quote_proceeds_from_trades(
            dust_trades,
            quote_currency="USDT",
        )
        pool_by_currency[currency] += spot_sell_quote_proceeds_from_trades(
            align_trades,
            quote_currency="USDT",
        )

    weights: dict[str, Decimal] = {}
    for currency in ("BTC", "ETH"):
        premiums = books.get(currency) or []
        if not premiums:
            continue
        total_premium = sum((native for _, native in premiums), Decimal("0"))
        pool = pool_by_currency.get(currency, Decimal("0"))
        for group, premium in premiums:
            gid = group.group_id
            direct = gross_by_gid.get(gid, Decimal("0"))
            pool_share = Decimal("0")
            if pool > 0 and total_premium > 0:
                pool_share = pool * premium / total_premium
            exchange_weight = direct + pool_share
            if exchange_weight > 0:
                weights[gid] = exchange_weight
            else:
                weights[gid] = _premium_usdt_weight(group)
    return weights


def _should_skip_proceeds_reconcile_apply(group: TradeGroup) -> bool:
    """Do not ledger-mark groups that still need a live premium spot sell."""
    status = str(group.profit_sweep_status or "").lower()
    if status in {"pending", "submitted"}:
        return True
    if status == "filled" and not profit_sweep_has_exchange_fill(group):
        return True
    return False


def reconcile_premium_proceeds_to_groups(
    groups: list[TradeGroup],
    client: DeribitClient,
    order_label_prefix: str,
    *,
    apply: bool = False,
    target_total_usdt: Decimal | None = None,
) -> dict[str, Any]:
    """Allocate net premium-sweep USDT to groups from exchange fill weights.

    Uses all profit-sweep spot sell proceeds (per group + dust/align pool), scaled to
    net USDT after buybacks. Does not use live wallet balance (withdrawals do not
    reduce lifetime proceeds).
    """
    summary: dict[str, Any] = {
        "net_usdt_by_book": {},
        "updated_groups": 0,
        "groups": [],
    }
    from .trade_journal_backfill import (
        group_excluded_from_premium_proceeds_pool,
        repair_manual_swap_proceeds_in_groups,
        repair_unlabeled_profit_sweeps_in_groups,
    )

    repair_manual_swap_proceeds_in_groups(groups)
    repair_unlabeled_profit_sweeps_in_groups(groups, client, order_label_prefix)
    labeled_repaired = repair_labeled_profit_sweep_proceeds_in_groups(groups, client, order_label_prefix)
    summary["labeled_fill_repaired_groups"] = labeled_repaired
    exchange_net = actual_premium_sweep_usdt_net(client, order_label_prefix)
    if target_total_usdt is None:
        target_total_usdt = exchange_net
    summary["exchange_net_usdt"] = format_decimal(exchange_net, 4)
    summary["target_total_usdt"] = format_decimal(target_total_usdt, 4)

    books: dict[str, list[tuple[TradeGroup, Decimal]]] = {"BTC": [], "ETH": []}
    for group in groups:
        if group.status != "closed" or not group.is_covered_call_group():
            continue
        if group_excluded_from_premium_proceeds_pool(group):
            continue
        currency = group.currency.upper()
        if currency not in books:
            continue
        native = realized_spot_profit_native_for_group(group)
        if native is None or native <= 0:
            continue
        books[currency].append((group, native))

    if target_total_usdt <= 0:
        summary["total_usdt"] = format_decimal(Decimal("0"), 4)
        return summary

    direct_by_gid = labeled_fill_proceeds_by_group(client, order_label_prefix)
    fixed_direct = sum(
        (
            direct_by_gid.get(group.group_id, Decimal("0"))
            for currency in ("BTC", "ETH")
            for group, _ in books.get(currency) or []
        ),
        Decimal("0"),
    )
    remainder = max(Decimal("0"), target_total_usdt - fixed_direct)

    weights = _exchange_gross_usdt_weights(client, order_label_prefix, books)
    pool_eligible: list[tuple[TradeGroup, Decimal, str, Decimal]] = []
    for currency in ("BTC", "ETH"):
        for group, premium in books.get(currency) or []:
            if direct_by_gid.get(group.group_id, Decimal("0")) > 0:
                continue
            weight = weights.get(group.group_id, Decimal("0"))
            if weight <= 0:
                weight = _premium_usdt_weight(group)
            if weight <= 0:
                continue
            pool_eligible.append((group, premium, currency, weight))

    pool_total_weight = sum((weight for _, _, _, weight in pool_eligible), Decimal("0"))
    if pool_total_weight <= 0 and fixed_direct <= 0:
        return _reconcile_premium_proceeds_by_premium_weight(
            groups,
            books,
            target_total_usdt,
            apply=apply,
            summary=summary,
        )

    pool_alloc: dict[str, Decimal] = {}
    allocated = Decimal("0")
    for idx, (group, _premium, _currency, weight) in enumerate(pool_eligible):
        if pool_total_weight <= 0:
            pool_alloc[group.group_id] = Decimal("0")
        elif idx == len(pool_eligible) - 1:
            pool_alloc[group.group_id] = max(Decimal("0"), remainder - allocated)
        else:
            share = (remainder * weight / pool_total_weight).quantize(Decimal("0.00000001"))
            allocated += share
            pool_alloc[group.group_id] = share

    total_usdt = Decimal("0")
    net_by_book: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}

    for currency in ("BTC", "ETH"):
        for group, premium in books.get(currency) or []:
            direct = direct_by_gid.get(group.group_id, Decimal("0"))
            proceeds = direct if direct > 0 else pool_alloc.get(group.group_id, Decimal("0"))
            total_usdt += proceeds
            net_by_book[currency] += proceeds
            row = {
                "group_id": group.group_id,
                "currency": currency,
                "premium_native": format_decimal(premium, 8),
                "proceeds_usdt": format_decimal(proceeds, 4),
                "before_usdt": format_decimal(group.profit_sweep_quote_proceeds, 4),
                "before_lifetime_usdt": format_decimal(group.profit_sweep_quote_proceeds_lifetime, 4),
                "labeled_fill_usdt": format_decimal(direct, 4) if direct > 0 else None,
            }
            summary["groups"].append(row)
            if not apply or group_excluded_from_premium_proceeds_pool(group):
                continue
            if _should_skip_proceeds_reconcile_apply(group):
                continue
            group.profit_sweep_status = "filled"
            group.profit_sweep_amount = premium
            group.profit_sweep_quote_proceeds = proceeds
            record_profit_sweep_lifetime_proceeds(group, proceeds)
            group.profit_sweep_instrument_name = f"{currency}_USDT"
            reason = str(group.profit_sweep_reason or "")
            if "proceeds_reconciled" not in reason:
                group.profit_sweep_reason = (reason + "; proceeds_reconciled").strip("; ")
            summary["updated_groups"] += 1

    summary["net_usdt_by_book"] = {k: format_decimal(v, 4) for k, v in net_by_book.items() if v > 0}
    summary["total_usdt"] = format_decimal(total_usdt, 4)
    return summary


def _reconcile_premium_proceeds_by_premium_weight(
    groups: list[TradeGroup],
    books: dict[str, list[tuple[TradeGroup, Decimal]]],
    target_total_usdt: Decimal,
    *,
    apply: bool,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Fallback when exchange has no labeled fills: split by close-index premium weight."""
    from .trade_journal_backfill import group_excluded_from_premium_proceeds_pool

    book_weights: dict[str, Decimal] = {"BTC": Decimal("0"), "ETH": Decimal("0")}
    for currency in ("BTC", "ETH"):
        for group, _native in books.get(currency) or []:
            book_weights[currency] += _premium_usdt_weight(group)
    total_weight = book_weights["BTC"] + book_weights["ETH"]
    if total_weight <= 0:
        summary["total_usdt"] = format_decimal(Decimal("0"), 4)
        return summary

    total_usdt = Decimal("0")
    for currency in ("BTC", "ETH"):
        premiums = books.get(currency) or []
        if not premiums:
            continue
        total_premium = sum((native for _, native in premiums), Decimal("0"))
        net_usdt = target_total_usdt * book_weights[currency] / total_weight
        summary["net_usdt_by_book"][currency] = format_decimal(net_usdt, 4)
        total_usdt += net_usdt

        allocated = Decimal("0")
        for idx, (group, premium) in enumerate(premiums):
            if idx == len(premiums) - 1:
                proceeds = max(Decimal("0"), net_usdt - allocated)
            else:
                proceeds = (net_usdt * premium / total_premium).quantize(Decimal("0.00000001"))
                allocated += proceeds

            row = {
                "group_id": group.group_id,
                "currency": currency,
                "premium_native": format_decimal(premium, 8),
                "proceeds_usdt": format_decimal(proceeds, 4),
                "before_usdt": format_decimal(group.profit_sweep_quote_proceeds, 4),
                "before_lifetime_usdt": format_decimal(group.profit_sweep_quote_proceeds_lifetime, 4),
            }
            summary["groups"].append(row)

            if not apply or group_excluded_from_premium_proceeds_pool(group):
                continue
            if _should_skip_proceeds_reconcile_apply(group):
                continue
            group.profit_sweep_status = "filled"
            group.profit_sweep_amount = premium
            group.profit_sweep_quote_proceeds = proceeds
            record_profit_sweep_lifetime_proceeds(group, proceeds)
            group.profit_sweep_instrument_name = f"{currency}_USDT"
            reason = str(group.profit_sweep_reason or "")
            if "proceeds_reconciled" not in reason:
                group.profit_sweep_reason = (reason + "; proceeds_reconciled").strip("; ")
            summary["updated_groups"] += 1

    summary["total_usdt"] = format_decimal(total_usdt, 4)
    return summary


def run_reconcile_premium_proceeds(
    bot,
    *,
    live: bool = False,
    target_total_usdt: Decimal | None = None,
) -> dict[str, Any]:
    from .trade_journal_backfill import repair_unlabeled_profit_sweeps_in_groups

    state = bot.state_store.load()
    prefix = bot.config.order_label_prefix
    unlabeled_repaired = repair_unlabeled_profit_sweeps_in_groups(state.groups, bot.client, prefix)
    summary = reconcile_premium_proceeds_to_groups(
        state.groups,
        bot.client,
        prefix,
        apply=live,
        target_total_usdt=target_total_usdt,
    )
    saved = False
    if live:
        from .profit_sweep_ops import sync_filled_profit_sweep_amounts_to_premium

        synced = sync_filled_profit_sweep_amounts_to_premium(state.groups)
        summary["synced_amount_groups"] = synced
        if (
            summary.get("updated_groups", 0) > 0
            or synced > 0
            or unlabeled_repaired > 0
            or summary.get("labeled_fill_repaired_groups", 0) > 0
        ):
            bot.state_store.save(state)
            saved = True
    summary["unlabeled_repaired_groups"] = unlabeled_repaired
    summary["action"] = "reconcile_premium_proceeds"
    summary["live"] = live
    summary["state_saved"] = saved
    return summary


def repair_premium_swap_alignment(
    bot,
    *,
    live: bool = False,
    buyback: bool = True,
    sell_deficit: bool = True,
) -> dict[str, Any]:
    state = bot.state_store.load()
    plan = build_premium_alignment_plan(bot.client, state.groups)
    buyback_actions: list[dict[str, Any]] = []
    if buyback and any(plan.buyback_native.get(c, Decimal(0)) > 0 for c in ("BTC", "ETH")):
        buyback_actions = execute_buyback_native(
            bot.config,
            bot.client,
            plan.buyback_native,
            live=live,
        )
        plan = build_premium_alignment_plan(bot.client, state.groups)
    sell_actions: list[dict[str, Any]] = []
    if sell_deficit and any(plan.sell_native.get(c, Decimal(0)) > 0 for c in ("BTC", "ETH")):
        sell_actions = execute_premium_deficit_sell(bot, plan.sell_native, live=live)
    return {
        "action": "repair_premium_swap_alignment",
        "live": live,
        "plan": plan.to_dict(),
        "buyback_actions": buyback_actions,
        "sell_actions": sell_actions,
    }


def repair_double_profit_sweeps(
    bot,
    *,
    live: bool = False,
    buyback: bool = True,
    save_state: bool = True,
) -> dict[str, Any]:
    state = bot.state_store.load()
    plan = build_profit_sweep_repair_plan(bot.client, state.groups)
    repaired = apply_profit_sweep_state_repairs(state.groups, plan)

    buyback_actions: list[dict[str, Any]] = []
    if buyback and (plan.buyback_native.get("BTC", 0) > 0 or plan.buyback_native.get("ETH", 0) > 0):
        buyback_actions = execute_profit_sweep_buyback(
            bot.config,
            bot.client,
            plan,
            live=live,
        )

    saved = False
    if live and save_state and repaired > 0:
        bot.state_store.save(state)
        saved = True

    return {
        "action": "repair_double_profit_sweep",
        "live": live,
        "repaired_groups": repaired,
        "plan": plan.to_dict(),
        "buyback_actions": buyback_actions,
        "state_saved": saved,
    }
