"""Pure exit-evaluation helpers shared by live management and backtest."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .fees import annualized_return
from .trade_apr import position_apr_capital_base, remaining_apr_for_group

if TYPE_CHECKING:
    from .config import BotConfig
    from .models import OrderBookSnapshot, TradeGroup


@dataclass(frozen=True)
class ExitEvalContext:
    enable_early_exit: bool
    early_exit_remaining_apr: Decimal
    early_exit_min_profit_capture: Decimal
    early_exit_max_spread_ratio: Decimal
    tp_capture_pct: Decimal
    time_exit_dte: int
    soft_defense_delta: Decimal
    hard_defense_delta: Decimal
    soft_defense_loss_pct: Decimal
    hard_stop_loss_pct: Decimal
    enable_dynamic_tp: bool
    tp_capture_pct_dte_long: Decimal
    tp_capture_pct_dte_short: Decimal
    tp_dte_long_threshold: Decimal
    tp_dte_short_threshold: Decimal
    defense_trigger_use_mark: bool = True


def exit_eval_context_from_config(config: BotConfig) -> ExitEvalContext:
    return ExitEvalContext(
        enable_early_exit=config.enable_early_exit,
        early_exit_remaining_apr=config.early_exit_remaining_apr,
        early_exit_min_profit_capture=config.early_exit_min_profit_capture,
        early_exit_max_spread_ratio=config.early_exit_max_spread_ratio,
        tp_capture_pct=config.tp_capture_pct,
        time_exit_dte=config.time_exit_dte,
        soft_defense_delta=config.soft_defense_delta,
        hard_defense_delta=config.hard_defense_delta,
        soft_defense_loss_pct=config.soft_defense_loss_pct,
        hard_stop_loss_pct=config.hard_stop_loss_pct,
        enable_dynamic_tp=config.enable_dynamic_tp,
        tp_capture_pct_dte_long=config.tp_capture_pct_dte_long,
        tp_capture_pct_dte_short=config.tp_capture_pct_dte_short,
        tp_dte_long_threshold=config.tp_dte_long_threshold,
        tp_dte_short_threshold=config.tp_dte_short_threshold,
        defense_trigger_use_mark=config.defense_trigger_use_mark,
    )


def dynamic_tp_capture_pct(
    dte_days: Decimal,
    ctx: ExitEvalContext,
) -> Decimal:
    """Lookup-table dynamic take-profit threshold by remaining DTE."""
    if not ctx.enable_dynamic_tp:
        return ctx.tp_capture_pct
    if dte_days >= ctx.tp_dte_long_threshold:
        return ctx.tp_capture_pct_dte_long
    if dte_days <= ctx.tp_dte_short_threshold:
        return ctx.tp_capture_pct_dte_short
    return ctx.tp_capture_pct


def evaluate_early_exit_reason(
    group: TradeGroup,
    short_book: OrderBookSnapshot,
    ctx: ExitEvalContext,
) -> str | None:
    if not ctx.enable_early_exit:
        return None
    if group.dte_days <= 0:
        return None
    capital_base = position_apr_capital_base(
        strategy=group.strategy,
        collateral_currency=group.collateral_currency,
        option_type=group.option_type,
        quantity=group.quantity,
        contract_size=Decimal("1"),
        strike=group.short_strike,
        index_price_usd=group.underlying_index_usd_for_apr(),
        estimated_im_collateral=group.estimated_im_collateral,
        covered_underlying_quantity=group.covered_underlying_quantity,
    )
    if capital_base <= 0:
        return None
    if short_book.best_ask_price <= 0 or short_book.best_bid_price <= 0:
        return None
    if short_book.spread_ratio > ctx.early_exit_max_spread_ratio:
        return None
    if group.profit_capture < ctx.early_exit_min_profit_capture:
        return None
    remaining_credit = max(group.current_debit - group.current_close_fee, Decimal("0"))
    if remaining_credit <= 0:
        return "early_exit_low_apr"
    remaining_apr = remaining_apr_for_group(
        remaining_credit=remaining_credit,
        capital_base=capital_base,
        dte_days=group.dte_days,
    )
    if remaining_apr < ctx.early_exit_remaining_apr:
        return "early_exit_low_apr"
    return None


def evaluate_defense_triggers(
    group: TradeGroup,
    *,
    soft_delta: Decimal,
    hard_delta: Decimal,
    ctx: ExitEvalContext,
) -> tuple[bool, bool]:
    loss_pct = group.mark_loss_pct_of_max_loss if ctx.defense_trigger_use_mark else group.loss_pct_of_max_loss
    hard = group.short_delta >= hard_delta or loss_pct >= ctx.hard_stop_loss_pct
    soft = group.short_delta >= soft_delta or loss_pct >= ctx.soft_defense_loss_pct
    return soft, hard


def evaluate_income_exit_reason(
    group: TradeGroup,
    short_book: OrderBookSnapshot | None,
    ctx: ExitEvalContext,
) -> str | None:
    """Income-path exits only (TP, early exit, time exit). Defense handled separately."""
    tp_threshold = dynamic_tp_capture_pct(group.dte_days, ctx)
    if group.profit_capture >= tp_threshold:
        return "take_profit"
    if short_book is not None:
        early = evaluate_early_exit_reason(group, short_book, ctx)
        if early is not None:
            return early
    if group.dte_days <= Decimal(str(ctx.time_exit_dte)):
        return "time_exit"
    return None


def evaluate_exit_reason_priority(
    group: TradeGroup,
    short_book: OrderBookSnapshot | None,
    ctx: ExitEvalContext,
    *,
    soft_delta: Decimal | None = None,
    hard_delta: Decimal | None = None,
    skip_defense: bool = False,
) -> str | None:
    """Mirror live ``_manage_group`` priority: hard > TP/early/time > soft."""
    sd = soft_delta if soft_delta is not None else ctx.soft_defense_delta
    hd = hard_delta if hard_delta is not None else ctx.hard_defense_delta
    if not skip_defense:
        _soft, hard = evaluate_defense_triggers(group, soft_delta=sd, hard_delta=hd, ctx=ctx)
        if hard:
            return "hard_stop"
    income = evaluate_income_exit_reason(group, short_book, ctx)
    if income is not None:
        return income
    if not skip_defense:
        soft, _hard = evaluate_defense_triggers(group, soft_delta=sd, hard_delta=hd, ctx=ctx)
        if soft:
            return "soft_stop"
    return None


def backtest_tp_target_premium(
    entry_premium: Decimal,
    dte_days: Decimal,
    ctx: ExitEvalContext,
) -> Decimal:
    """Premium level at which take-profit triggers in backtest."""
    threshold = dynamic_tp_capture_pct(dte_days, ctx)
    return entry_premium * (Decimal("1") - threshold)


def backtest_remaining_apr_gate(
    *,
    entry_premium: Decimal,
    current_premium: Decimal,
    close_fee_per_contract: Decimal,
    quantity: Decimal,
    capital_base: Decimal,
    dte_days: Decimal,
    ctx: ExitEvalContext,
) -> bool:
    """True when early-exit remaining APR is below threshold."""
    if not ctx.enable_early_exit or capital_base <= 0 or dte_days <= 0:
        return False
    entry_credit = entry_premium * quantity
    remaining_credit = max(current_premium * quantity + close_fee_per_contract * quantity, Decimal("0"))
    if entry_credit <= 0:
        return False
    profit_capture = (entry_credit - remaining_credit) / entry_credit
    if profit_capture < ctx.early_exit_min_profit_capture:
        return False
    remaining_apr = annualized_return(
        net_credit=remaining_credit,
        capital_base=capital_base,
        dte_days=dte_days,
    )
    return remaining_apr < ctx.early_exit_remaining_apr
