"""Per-group defense/exit decision logic used by :mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..exit_eval import (
    evaluate_defense_triggers,
    evaluate_early_exit_reason,
    exit_eval_context_from_config,
    income_exit_close_premium,
    take_profit_triggered,
    time_exit_triggered,
)
from ..models import OptionInstrument, OrderBookSnapshot, TradeGroup
from .context import LOGGER, RuntimeContext


class GroupExitMixin:
    def _confirm_defense_triggers(
        self,
        group: TradeGroup,
        *,
        raw_soft: bool,
        raw_hard: bool,
    ) -> tuple[bool, bool]:
        """Apply the confirmation window to raw defense triggers.

        A trigger only fires once its condition has held for
        ``DEFENSE_CONFIRM_CYCLES`` consecutive manage cycles, so a single
        snapshot spike (delta or loss) that mean-reverts does not stop us out
        at a local extreme. Streaks reset the moment the condition clears.
        """
        group.hard_defense_streak = group.hard_defense_streak + 1 if raw_hard else 0
        group.soft_defense_streak = group.soft_defense_streak + 1 if raw_soft else 0
        need = max(self.config.defense_confirm_cycles, 1)
        confirmed_hard = raw_hard and group.hard_defense_streak >= need
        confirmed_soft = raw_soft and group.soft_defense_streak >= need
        return confirmed_soft, confirmed_hard

    def _manage_group(self, context: RuntimeContext, group: TradeGroup, *, live: bool) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if self._is_covered_call_group(group):
            return self._manage_covered_call_group(context, group, live=live)
        soft_delta, hard_delta = self._defense_delta_thresholds(group)
        raw_soft, raw_hard = evaluate_defense_triggers(
            group,
            soft_delta=soft_delta,
            hard_delta=hard_delta,
            ctx=exit_eval_context_from_config(self.config),
        )
        soft_trigger, hard_trigger = self._confirm_defense_triggers(group, raw_soft=raw_soft, raw_hard=raw_hard)
        per_position = self.config.enable_perp_hedge and self.config.per_position_hedge
        hold_on_hard = (
            hard_trigger
            and self.config.enable_perp_hedge
            and self.config.hedge_first_on_hard
            and not self._hedge_giveup_breached(group)
        )
        if per_position:
            # Refresh this group's intended per-position hedge every cycle
            # (soft=partial, hard=full, auto-unwind on recovery). The perp order
            # itself is reconciled once per currency after the group loop.
            contract_size = self._contract_size_for_group(
                group,
                getattr(context, "markets_by_currency", None) or {},
            )
            self._update_position_hedge(
                group,
                raw_soft=raw_soft,
                raw_hard=raw_hard,
                soft_trigger=soft_trigger,
                hard_trigger=hard_trigger,
                contract_size=contract_size,
            )
        if hard_trigger and not hold_on_hard:
            if self.config.enable_perp_hedge and not per_position:
                hedge_plan = self._build_hedge_plan(context, group.currency, mode="hard")
                if hedge_plan is not None:
                    actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
            actions.extend(self._close_group(context, group, reason="hard_stop", live=live))
            return actions
        if hold_on_hard and not per_position:
            # Hedge-first (legacy currency-net): neutralize the position delta and
            # HOLD the option instead of crystallizing the loss at a local
            # extreme. Income / time / expiry exits below can still close it; the
            # soft defense close is skipped (we are already fully hedged this
            # cycle). Under per-position hedging the hold target is set by
            # _update_position_hedge instead.
            hedge_plan = self._build_hedge_plan(context, group.currency, mode="hard", target_pct=Decimal("0"))
            if hedge_plan is not None:
                actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
        if self._take_profit_triggered(context, group):
            actions.extend(self._close_group(context, group, reason="take_profit", live=live))
            return actions
        early_exit_reason = self._maybe_early_exit_reason(context, group)
        if early_exit_reason is not None:
            actions.extend(self._close_group(context, group, reason=early_exit_reason, live=live))
            return actions
        robust_exit_actions = self._maybe_covered_call_robust_spot_exit(context, group, live=live)
        if robust_exit_actions is not None:
            return robust_exit_actions
        if self._time_exit_triggered(context, group):
            actions.extend(self._close_group(context, group, reason="time_exit", live=live))
            return actions
        if soft_trigger and not hold_on_hard:
            if per_position:
                # Hedge target already set by _update_position_hedge; hold the
                # option and let the reconcile place the partial perp hedge.
                pass
            elif self.config.enable_perp_hedge:
                hedge_plan = self._build_hedge_plan(context, group.currency, mode="soft")
                if hedge_plan is not None:
                    actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
                else:
                    actions.extend(self._close_group(context, group, reason="soft_stop_no_hedge", live=live))
            else:
                actions.extend(self._close_group(context, group, reason="soft_stop", live=live))
        return actions

    def _hedge_giveup_breached(self, group: TradeGroup) -> bool:
        """True when a hedged hard-stop position has lost enough to force a close."""
        giveup = self.config.hedge_giveup_loss_pct
        return giveup > 0 and group.mark_loss_pct_of_max_loss >= giveup

    def _maybe_early_exit_reason(self, context: RuntimeContext, group: TradeGroup) -> str | None:
        """Decide whether to close a short option leg early."""
        if not self.config.enable_early_exit:
            return None
        if group.dte_days <= 0:
            return None
        try:
            short_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        except Exception:
            LOGGER.exception(
                "early_exit: failed to load orderbook for %s, skipping",
                group.short_instrument_name,
            )
            return None
        return evaluate_early_exit_reason(
            group,
            short_book,
            exit_eval_context_from_config(self.config),
        )

    def _uses_coin_native_income_exit(self, group: TradeGroup) -> bool:
        """Covered calls on BTC/ETH books measure premium capture in coin, not USDC."""
        return self._is_covered_call_group(group) and group.is_coin_collateral()

    def _take_profit_triggered(self, context: RuntimeContext, group: TradeGroup) -> bool:
        ctx = exit_eval_context_from_config(self.config)
        if self._uses_coin_native_income_exit(group):
            entry_native, close_native = self._income_exit_native_pair(context, group)
            if entry_native is None:
                close_debit = self._income_exit_close_debit(context, group)
                return take_profit_triggered(group, close_debit_usdc=close_debit, ctx=ctx)
            return take_profit_triggered(
                group,
                close_debit_usdc=None,
                ctx=ctx,
                entry_credit=entry_native,
                close_debit=close_native,
            )
        close_debit = self._income_exit_close_debit(context, group)
        return take_profit_triggered(group, close_debit_usdc=close_debit, ctx=ctx)

    def _time_exit_triggered(self, context: RuntimeContext, group: TradeGroup) -> bool:
        ctx = exit_eval_context_from_config(self.config)
        if self._uses_coin_native_income_exit(group):
            entry_native, close_native = self._income_exit_native_pair(context, group)
            if entry_native is None:
                close_debit = self._income_exit_close_debit(context, group)
                return time_exit_triggered(group, close_debit_usdc=close_debit, ctx=ctx)
            return time_exit_triggered(
                group,
                close_debit_usdc=None,
                ctx=ctx,
                entry_credit=entry_native,
                close_debit=close_native,
            )
        close_debit = self._income_exit_close_debit(context, group)
        return time_exit_triggered(group, close_debit_usdc=close_debit, ctx=ctx)

    def _income_exit_native_pair(
        self,
        context: RuntimeContext,
        group: TradeGroup,
    ) -> tuple[Decimal | None, Decimal | None]:
        """Return ``(entry_native, close_native)`` for coin-collateral covered calls."""
        from ..exit_eval import close_debit_native, entry_credit_native

        entry_native = entry_credit_native(group)
        if entry_native is None:
            return None, None
        ctx = exit_eval_context_from_config(self.config)
        try:
            short_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        except Exception:
            return entry_native, None
        close_premium = income_exit_close_premium(short_book, ctx)
        if close_premium is None:
            return entry_native, None
        markets = getattr(context, "markets_by_currency", None) or {}
        try:
            short_instrument = self._find_or_fetch_instrument(markets, group.short_instrument_name)
        except Exception:
            return entry_native, None
        fee_collateral = self._option_fee_native(
            premium=close_premium,
            quantity=group.quantity,
            index_price=short_book.index_price,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        return entry_native, close_debit_native(
            premium=close_premium,
            quantity=group.quantity,
            fee_collateral=fee_collateral,
        )

    def _income_exit_close_debit(
        self,
        context: RuntimeContext,
        group: TradeGroup,
    ) -> Decimal | None:
        """Executable buy-to-close debit (incl. fees) for income-exit evaluation."""
        ctx = exit_eval_context_from_config(self.config)
        try:
            short_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        except Exception:
            LOGGER.debug(
                "income_exit: orderbook unavailable for %s",
                group.short_instrument_name,
            )
            return None
        close_premium = income_exit_close_premium(short_book, ctx)
        if close_premium is None:
            return None
        markets = getattr(context, "markets_by_currency", None) or {}
        try:
            short_instrument = self._find_or_fetch_instrument(markets, group.short_instrument_name)
        except Exception:
            LOGGER.debug(
                "income_exit: instrument metadata unavailable for %s",
                group.short_instrument_name,
            )
            return None
        usdc_linear = (
            short_instrument.quote_currency.upper() == "USDC" and short_instrument.settlement_currency.upper() == "USDC"
        )
        idx = short_book.index_price
        usdc_path = usdc_linear or group.collateral_currency.upper() == "USDC"
        close_debit = self._short_close_debit_usdc(
            premium=close_premium,
            quantity=group.quantity,
            short_book=short_book,
            short_instrument=short_instrument,
            usdc_path=usdc_path,
            idx=idx,
        )
        if not group.long_instrument_name:
            return close_debit
        try:
            long_book = self._get_orderbook(group.long_instrument_name, context.orderbook_cache)
            long_instrument = self._find_or_fetch_instrument(markets, group.long_instrument_name)
            long_close_premium = long_book.sell_close_premium(
                max_spread_ratio=ctx.income_exit_max_spread_ratio,
            )
            if long_close_premium <= 0:
                long_close_premium = long_book.best_bid_price if long_book.best_bid_price > 0 else long_book.mark_price
            if usdc_linear or group.collateral_currency.upper() == "USDC":
                long_credit = self._premium_value_usdc(
                    premium=max(long_close_premium, Decimal("0")),
                    quantity=group.quantity,
                    index_price=long_book.index_price,
                    instrument=long_instrument,
                )
                long_fee = self._option_fee_usdc(
                    premium=max(long_close_premium, Decimal("0")),
                    quantity=group.quantity,
                    index_price=long_book.index_price,
                    base_currency=long_instrument.base_currency,
                    quote_currency=long_instrument.quote_currency,
                    settlement_currency=long_instrument.settlement_currency,
                )
                return max(close_debit - max(long_credit - long_fee, Decimal("0")), Decimal("0"))
            long_fee_collateral = self._option_fee_native(
                premium=max(long_close_premium, Decimal("0")),
                quantity=group.quantity,
                index_price=long_book.index_price,
                quote_currency=long_instrument.quote_currency,
                settlement_currency=long_instrument.settlement_currency,
            )
            long_gross_native = max(long_close_premium, Decimal("0")) * group.quantity
            long_net_native = long_gross_native - long_fee_collateral
            idx_long = long_book.index_price if long_book.index_price > 0 else idx
            return max(
                close_debit - long_net_native * idx_long if idx_long > 0 else close_debit,
                Decimal("0"),
            )
        except Exception as exc:
            LOGGER.warning(
                "income_exit: unable to net long leg %s for group=%s (%s)",
                group.long_instrument_name,
                group.group_id,
                exc,
            )
            return None

    def _short_close_debit_usdc(
        self,
        *,
        premium: Decimal,
        quantity: Decimal,
        short_book: OrderBookSnapshot,
        short_instrument: OptionInstrument,
        usdc_path: bool,
        idx: Decimal,
    ) -> Decimal:
        """USDC buy-to-close cost of the short leg (incl. fee) for a given premium."""
        value = max(
            self._premium_value_usdc(
                premium=premium,
                quantity=quantity,
                index_price=short_book.index_price,
                instrument=short_instrument,
            ),
            Decimal("0"),
        )
        if usdc_path:
            fee = self._option_fee_usdc(
                premium=premium,
                quantity=quantity,
                index_price=short_book.index_price,
                base_currency=short_instrument.base_currency,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
            )
            return value + fee
        fee_collateral = self._option_fee_native(
            premium=premium,
            quantity=quantity,
            index_price=short_book.index_price,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        gross_native = premium * quantity
        return (gross_native + fee_collateral) * idx if idx > 0 else value
