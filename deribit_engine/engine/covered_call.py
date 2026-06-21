from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..covered_call_settlement import resolve_covered_call_settlement_loss
from ..exit_reasons import INCOME_EXIT_REASONS
from ..models import (
    AccountSummary,
    OptionInstrument,
    OrderBookSnapshot,
    PortfolioSnapshot,
    StrategyState,
    TradeGroup,
)
from ..utils import (
    align_option_order_amount,
    format_decimal,
    utc_now_ms,
)
from .context import (
    LOGGER,
    RuntimeContext,
)


class CoveredCallMixin:
    """Covered-call lifecycle: ITM spot exits, profit sweeps, collateral/cooldown helpers."""

    def _manage_covered_call_group(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """Covered calls: OTM uses income exits (TP / early / time); ITM uses spot exit."""
        itm = self._covered_call_itm(group, context)
        group.itm_defense_streak = group.itm_defense_streak + 1 if itm else 0
        if itm:
            robust_exit_actions = self._maybe_covered_call_robust_spot_exit(context, group, live=live)
            if robust_exit_actions is not None:
                return robust_exit_actions
            return []
        actions: list[dict[str, Any]] = []
        if self._take_profit_triggered(context, group):
            actions.extend(self._close_group(context, group, reason="take_profit", live=live))
            return actions
        early_exit_reason = self._maybe_early_exit_reason(context, group)
        if early_exit_reason is not None:
            actions.extend(self._close_group(context, group, reason=early_exit_reason, live=live))
            return actions
        if group.dte_days <= self.config.time_exit_dte:
            actions.extend(self._close_group(context, group, reason="time_exit", live=live))
            return actions
        return actions

    def _defense_delta_thresholds(self, group: TradeGroup) -> tuple[Decimal, Decimal]:
        if (group.option_type or "").lower() == "call":
            return self.config.soft_defense_delta_call, self.config.hard_defense_delta_call
        return self.config.soft_defense_delta, self.config.hard_defense_delta

    def _maybe_covered_call_robust_spot_exit(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> list[dict[str, Any]] | None:
        if not self.config.covered_call_spot_exit_enabled:
            return None
        if not self.config.covered_call_robust_exit_enabled:
            return None
        if not self._is_covered_call_group(group):
            return None
        if group.dte_days > self.config.covered_call_robust_exit_dte:
            return None
        if not self._covered_call_itm(group, context):
            return None
        # Confirmation window: require ITM to hold for a few cycles before
        # buying back the call + dumping spot, so a brief wick above the strike
        # does not crystallize the loss at a local top.
        confirm = self.config.covered_call_itm_confirm_cycles
        if confirm is None:
            confirm = self.config.defense_confirm_cycles
        if group.itm_defense_streak < max(confirm, 1):
            return None

        actions = self._close_group(context, group, reason="covered_call_robust_exit", live=live)
        if not live:
            actions.append(
                self._execute_covered_call_spot_exit(
                    context,
                    group,
                    reason="covered_call_robust_exit_preview",
                    live=False,
                )
            )
            return actions
        if group.status != "closed":
            actions.append(
                {
                    "action": "covered_call_spot_exit_skipped",
                    "group_id": group.group_id,
                    "reason": "option_close_incomplete",
                }
            )
            return actions
        actions.append(
            self._execute_covered_call_spot_exit(
                context,
                group,
                reason="covered_call_robust_exit",
                live=True,
            )
        )
        return actions

    def _pending_covered_call_spot_exit_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        if not self.config.covered_call_spot_exit_enabled:
            return []
        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if group.status == "closed" and self._is_covered_call_group(group) and group.spot_exit_status == "pending":
                actions.append(
                    self._execute_covered_call_spot_exit(
                        context,
                        group,
                        reason=group.spot_exit_reason or "covered_call_settlement_exit",
                        live=live,
                    )
                )
        return actions

    def _maybe_schedule_profit_sweep(self, group: TradeGroup, *, reason: str, live: bool) -> None:
        """Queue a post-close spot sale of realized premium profit to USDT (not collateral)."""
        if not live:
            return
        if not self.config.covered_call_profit_sweep_enabled:
            return
        if self.config.option_strategy != "covered_call":
            return
        if reason not in INCOME_EXIT_REASONS:
            return
        if not self._is_covered_call_group(group):
            return
        native = self._coin_profit_native_for_sweep(group)
        if native is None:
            return
        status = str(group.profit_sweep_status or "").lower()
        if status == "filled":
            unswept = self._unswept_profit_native_for_sweep(group)
            if unswept is None or unswept <= 0:
                return
            group.profit_sweep_status = "pending"
            group.profit_sweep_reason = reason
            group.profit_sweep_amount = unswept
            return
        if status in {"pending", "submitted"}:
            return
        group.profit_sweep_status = "pending"
        group.profit_sweep_reason = reason
        group.profit_sweep_amount = native

    def _pending_profit_sweep_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        if not self.config.covered_call_profit_sweep_enabled:
            return []
        if live:
            from ..profit_sweep_ops import (
                heal_reconciled_proceeds_drift,
                reschedule_failed_profit_sweeps,
                reschedule_ledger_only_profit_sweeps,
            )
            from ..trade_journal_backfill import (
                repair_manual_swap_proceeds_in_groups,
                repair_unlabeled_profit_sweeps_in_groups,
            )

            repair_manual_swap_proceeds_in_groups(context.state.groups)
            repair_unlabeled_profit_sweeps_in_groups(
                context.state.groups,
                self.client,
                self.config.order_label_prefix,
            )
            self._reconcile_profit_sweeps_from_exchange(context)
            reschedule_ledger_only_profit_sweeps(self, context.state.groups)
            reschedule_failed_profit_sweeps(self, context.state.groups)
        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if (
                group.status == "closed"
                and self._is_covered_call_group(group)
                and group.profit_sweep_status == "pending"
            ):
                actions.append(self._execute_covered_call_profit_sweep(context, group, live=live))
        from ..profit_sweep_dust import run_dust_pool_profit_sweeps

        actions.extend(run_dust_pool_profit_sweeps(self, context, live=live))
        if live:
            from ..profit_sweep_dust import reconcile_dust_sweep_from_exchange
            from ..profit_sweep_ops import heal_reconciled_proceeds_drift

            heal_reconciled_proceeds_drift(self, context.state.groups)
            self._reconcile_profit_sweep_quote_proceeds(context)
            reconcile_dust_sweep_from_exchange(self, context.state.groups)
        return actions

    def _reconcile_profit_sweeps_from_exchange(self, context: RuntimeContext) -> None:
        from ..trade_journal_backfill import reconcile_profit_sweep_from_exchange

        for group in context.state.groups:
            reconcile_profit_sweep_from_exchange(
                group,
                client=self.client,
                order_label_prefix=self.config.order_label_prefix,
            )

    def _reconcile_profit_sweep_quote_proceeds(self, context: RuntimeContext) -> None:
        from ..wallet_ops import spot_sell_quote_proceeds_from_trades

        for group in context.state.groups:
            if group.profit_sweep_status != "filled":
                continue
            if group.profit_sweep_quote_proceeds > 0:
                continue
            order_id = str(group.profit_sweep_order_id or "").strip()
            if not order_id:
                continue
            try:
                trades = self.client.get_user_trades_by_order(order_id)
            except Exception:
                LOGGER.exception(
                    "profit_sweep: failed to load trades for order %s (group=%s)",
                    order_id,
                    group.group_id,
                )
                continue
            proceeds = spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
            if proceeds > 0:
                group.profit_sweep_quote_proceeds = proceeds
                from ..profit_sweep_ops import record_profit_sweep_lifetime_proceeds

                record_profit_sweep_lifetime_proceeds(group, proceeds)

    def _apply_profit_sweep_quote_proceeds(
        self,
        group: TradeGroup,
        response: dict[str, Any] | None,
        *,
        cumulative: bool = False,
    ) -> Decimal:
        from ..wallet_ops import spot_sell_quote_proceeds_from_trades

        trades = self._order_trades(response)
        proceeds = spot_sell_quote_proceeds_from_trades(trades, quote_currency="USDT")
        if proceeds > 0:
            if cumulative:
                group.profit_sweep_quote_proceeds += proceeds
            else:
                group.profit_sweep_quote_proceeds = proceeds
            from ..profit_sweep_ops import record_profit_sweep_lifetime_proceeds

            record_profit_sweep_lifetime_proceeds(group, group.profit_sweep_quote_proceeds)
        return proceeds

    @staticmethod
    def _covered_call_profit_sweep_instrument(currency: str) -> str:
        return CoveredCallMixin._covered_call_spot_instrument(currency)

    def _unswept_profit_native_for_sweep(self, group: TradeGroup) -> Decimal | None:
        """Coin profit not yet sold to USDT (supports partial prior sweeps)."""
        native_cap = self._coin_profit_native_for_sweep(group)
        if native_cap is None or native_cap <= 0:
            return None
        status = str(group.profit_sweep_status or "").lower()
        swept = group.profit_sweep_amount if group.profit_sweep_amount > 0 else Decimal("0")
        if status == "filled":
            from ..profit_sweep_ops import profit_sweep_has_exchange_fill

            if not profit_sweep_has_exchange_fill(group):
                return native_cap
            return max(native_cap - min(swept, native_cap), Decimal("0"))
        if status in {"pending", "submitted"} and swept > 0 and swept < native_cap:
            return max(native_cap - swept, Decimal("0"))
        return native_cap

    def _prior_swept_profit_native(self, group: TradeGroup) -> Decimal:
        native_cap = self._coin_profit_native_for_sweep(group)
        if native_cap is None or native_cap <= 0:
            return Decimal("0")
        status = str(group.profit_sweep_status or "").lower()
        swept = group.profit_sweep_amount if group.profit_sweep_amount > 0 else Decimal("0")
        if status == "filled":
            return min(swept, native_cap)
        if status == "pending" and swept > 0 and swept < native_cap:
            return swept
        return Decimal("0")

    def _profit_sweep_wallet_unswept_budget(
        self,
        context: RuntimeContext,
        currency: str,
    ) -> Decimal:
        """Sum of per-group unswept premium profit — cross-group oversell guard."""
        ccy = currency.upper()
        total = Decimal("0")
        for group in context.state.groups:
            if group.status != "closed" or not self._is_covered_call_group(group):
                continue
            if group.currency.upper() != ccy or not group.is_coin_collateral():
                continue
            unswept = self._unswept_profit_native_for_sweep(group)
            if unswept is not None and unswept > 0:
                total += unswept
        return total

    def _profit_sweep_sellable_native_cap(
        self,
        context: RuntimeContext,
        currency: str,
        *,
        live: bool,
    ) -> Decimal:
        """Native free to sell without touching open covered-call collateral."""
        summaries = self._account_summaries_by_currency() if live else context.summaries
        return self._available_covered_call_quantity_from_summaries(
            context.state,
            summaries,
            currency,
        )

    def _profit_sweep_amount(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> Decimal:
        unswept = self._unswept_profit_native_for_sweep(group)
        if unswept is None or unswept <= 0:
            return Decimal("0")
        target = unswept

        wallet_budget = self._profit_sweep_wallet_unswept_budget(context, group.currency)
        if wallet_budget <= 0:
            return Decimal("0")
        target = min(target, wallet_budget)

        free_native = self._profit_sweep_sellable_native_cap(context, group.currency, live=live)
        if free_native <= 0:
            return Decimal("0")
        target = min(target, free_native)

        instrument_name = self._covered_call_profit_sweep_instrument(group.currency)
        contract_size, min_trade_amount = self._spot_min_trade_amount(instrument_name, group.currency)
        aligned = align_option_order_amount(target, contract_size, min_trade_amount)
        if aligned <= 0:
            return Decimal("0")
        return min(aligned, unswept)

    def _execute_covered_call_profit_sweep(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> dict[str, Any]:
        realized_profit = self._coin_profit_native_for_sweep(group)
        if realized_profit is None or realized_profit <= 0:
            if live and group.profit_sweep_status == "pending":
                group.profit_sweep_status = "skipped"
                group.profit_sweep_reason = "no_realized_spot_profit"
            return {
                "action": "covered_call_profit_sweep_skipped",
                "group_id": group.group_id,
                "reason": "no_realized_spot_profit",
                "profit_sweep_status": group.profit_sweep_status or None,
                "live": live,
            }
        if group.profit_sweep_status == "submitted":
            return {
                "action": "covered_call_profit_sweep_skipped",
                "group_id": group.group_id,
                "reason": "already_submitted",
                "profit_sweep_status": group.profit_sweep_status,
                "profit_sweep_order_id": group.profit_sweep_order_id or None,
            }
        if group.profit_sweep_status == "filled":
            unswept = self._unswept_profit_native_for_sweep(group)
            if unswept is None or unswept <= 0:
                return {
                    "action": "covered_call_profit_sweep_skipped",
                    "group_id": group.group_id,
                    "reason": "already_filled",
                    "profit_sweep_status": group.profit_sweep_status,
                    "profit_sweep_order_id": group.profit_sweep_order_id or None,
                }

        instrument_name = self._covered_call_profit_sweep_instrument(group.currency)
        amount = self._profit_sweep_amount(context, group, live=live)
        if amount <= 0:
            if live:
                group.profit_sweep_status = "skipped"
                if not group.profit_sweep_reason:
                    group.profit_sweep_reason = "amount_below_min_or_unavailable"
            return {
                "action": "covered_call_profit_sweep_skipped",
                "group_id": group.group_id,
                "reason": "amount_below_min_or_unavailable",
                "instrument_name": instrument_name,
                "amount": format_decimal(amount, 8),
                "live": live,
            }

        payload: dict[str, Any] = {
            "action": "covered_call_profit_sweep" if live else "covered_call_profit_sweep_preview",
            "group_id": group.group_id,
            "reason": group.profit_sweep_reason or "profit_sweep",
            "instrument_name": instrument_name,
            "amount": format_decimal(amount, 8),
            "order_type": self.config.covered_call_spot_order_type,
            "live": live,
        }
        if not live:
            return payload

        unswept_cap = self._unswept_profit_native_for_sweep(group)
        if unswept_cap is not None and amount > unswept_cap:
            amount = unswept_cap
        prior_swept = self._prior_swept_profit_native(group)
        group.profit_sweep_status = "submitted"
        group.profit_sweep_amount = amount
        group.profit_sweep_instrument_name = instrument_name
        label = f"{self.config.order_label_prefix}-profit-sweep-{group.currency.lower()}-{group.group_id}"
        try:
            from ..wallet_ops import trade_spot

            result = trade_spot(
                self.config,
                self.client,
                from_currency=group.currency,
                to_currency="USDT",
                amount=format_decimal(amount, 8),
                instrument_name=instrument_name,
                order_type=self.config.covered_call_spot_order_type,
                live=True,
                label=label,
            )
        except Exception as exc:
            group.profit_sweep_reason = f"{group.profit_sweep_reason or 'profit_sweep'}: submission_failed: {exc}"
            payload["profit_sweep_status"] = group.profit_sweep_status
            payload["error"] = str(exc)
            return payload

        if result.get("action") == "trade_spot_skipped":
            skip_reason = str(result.get("reason") or "skipped")
            if skip_reason == "slippage_exceeded":
                group.profit_sweep_status = "pending"
            else:
                group.profit_sweep_status = "skipped"
            group.profit_sweep_reason = f"{group.profit_sweep_reason or 'profit_sweep'}: {skip_reason}"
            payload["action"] = "covered_call_profit_sweep_skipped"
            payload["reason"] = skip_reason
            payload["reference_mark_price"] = result.get("reference_mark_price")
            payload["slippage_limit_price"] = result.get("slippage_limit_price")
            payload["profit_sweep_status"] = group.profit_sweep_status
            return payload

        order_id = result.get("order_id")
        if order_id:
            group.profit_sweep_order_id = str(order_id)
        order_state = str(result.get("order_state") or "").lower()
        if order_state == "filled":
            group.profit_sweep_status = "filled"
        elif order_state in {"cancelled", "rejected"}:
            group.profit_sweep_status = "failed"
        else:
            group.profit_sweep_status = "filled"
        cumulative = prior_swept > 0
        proceeds = self._apply_profit_sweep_quote_proceeds(
            group,
            result.get("response"),
            cumulative=cumulative,
        )
        if prior_swept > 0:
            group.profit_sweep_amount = prior_swept + amount
        payload["profit_sweep_status"] = group.profit_sweep_status
        payload["profit_sweep_order_id"] = group.profit_sweep_order_id or None
        if proceeds > 0:
            payload["profit_sweep_quote_proceeds"] = format_decimal(proceeds, 4)
        payload["response"] = result.get("response")
        return payload

    def _is_covered_call_strategy(self) -> bool:
        return self.config.option_strategy == "covered_call"

    def _available_covered_call_quantity_from_summaries(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        currency: str,
    ) -> Decimal:
        ccy = currency.upper()
        summary = summaries.get(ccy)
        if summary is None:
            return Decimal("0")
        reserved = self._reserved_covered_call_quantity(state, ccy)
        return max(summary.equity - reserved, Decimal("0"))

    def _covered_call_book_im_mm_shielded(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        currency: str,
        *,
        available_cover: Decimal | None = None,
    ) -> bool:
        """Skip book IM/MM gates when covered_call still has native spot backing."""
        if not self._is_covered_call_strategy():
            return False
        ccy = currency.upper()
        if available_cover is None:
            available_cover = self._available_covered_call_quantity_from_summaries(state, summaries, ccy)
        if available_cover > 0:
            return True
        return self._covered_call_book_fully_collateralized(state, summaries, ccy)

    def _covered_call_book_fully_collateralized(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        currency: str,
    ) -> bool:
        """True when this collateral book still holds enough native equity for open covered calls."""
        if not self._is_covered_call_strategy():
            return False
        ccy = currency.upper()
        summary = summaries.get(ccy)
        if summary is None or summary.equity <= 0:
            return False
        reserved = self._reserved_covered_call_quantity(state, ccy)
        if reserved <= 0:
            return False
        return summary.equity >= reserved

    def _clear_covered_call_book_cooldowns(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
    ) -> None:
        """Drop stale cooldowns once native book equity still covers open short calls."""
        for ccy in summaries:
            if not self._covered_call_book_fully_collateralized(state, summaries, ccy):
                continue
            state.cooldown_until_ms_by_book.pop(ccy.upper(), None)
        if not any(not self._is_covered_call_group(group) for group in self._open_groups(state)):
            state.cooldown_until_ms = None

    def _clear_stale_drawdown_cooldowns(
        self,
        state: StrategyState,
        snapshot: PortfolioSnapshot,
    ) -> None:
        """Drop per-book cooldowns left over from a phantom drawdown breach."""
        now_ms = utc_now_ms()
        for book, ts in list(state.cooldown_until_ms_by_book.items()):
            if not ts or ts <= now_ms:
                continue
            dd = snapshot.day_drawdown_pct_by_book.get(book, Decimal("0"))
            if dd < self.config.halt_drawdown_pct:
                state.cooldown_until_ms_by_book.pop(book, None)

    @staticmethod
    def _is_covered_call_group(group: TradeGroup) -> bool:
        return group.is_covered_call_group()

    def _covered_call_itm(self, group: TradeGroup, context: RuntimeContext) -> bool:
        index_price = self._currency_index_price(group.currency, context.orderbook_cache)
        if index_price <= 0:
            try:
                index_price = self._get_orderbook(group.short_instrument_name, context.orderbook_cache).index_price
            except Exception:
                index_price = Decimal("0")
        if index_price <= 0 or group.short_strike <= 0:
            return False
        trigger = group.short_strike * (Decimal("1") + self.config.covered_call_itm_buffer_pct)
        return index_price > trigger

    def _covered_call_itm_from_cache(
        self,
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> bool:
        index_price = self._currency_index_price(group.currency, orderbook_cache)
        if index_price <= 0 or group.short_strike <= 0:
            return False
        trigger = group.short_strike * (Decimal("1") + self.config.covered_call_itm_buffer_pct)
        return index_price > trigger

    @staticmethod
    def _covered_call_spot_instrument(currency: str) -> str:
        return f"{currency.upper()}_USDT"

    def _spot_min_trade_amount(self, instrument_name: str, currency: str) -> tuple[Decimal, Decimal]:
        for lookup_currency in ("USDT", "USDC", currency.upper()):
            try:
                rows = self.client.get_instruments(lookup_currency, kind="spot", expired=False)
            except Exception:
                continue
            for row in rows:
                instrument = OptionInstrument.from_api(row)
                if instrument.instrument_name == instrument_name:
                    return instrument.contract_size, instrument.min_trade_amount
        return Decimal("0"), Decimal("0")

    def _spot_exit_index_price_usd(
        self,
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> Decimal:
        if group.close_index_usd and group.close_index_usd > 0:
            return group.close_index_usd
        index_price = self._currency_index_price(group.currency, orderbook_cache)
        if index_price > 0:
            return index_price
        try:
            return self._get_orderbook(group.short_instrument_name, orderbook_cache).index_price
        except Exception:
            return Decimal("0")

    def _spot_exit_short_instrument(
        self,
        group: TradeGroup,
        markets_by_currency: dict[str, list[OptionInstrument]] | None,
    ) -> OptionInstrument | None:
        if not markets_by_currency:
            return None
        try:
            return self._find_or_fetch_instrument(markets_by_currency, group.short_instrument_name)
        except Exception:
            return None

    def _plan_covered_call_spot_exit(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        return self._plan_covered_call_spot_exit_fields(
            group,
            orderbook_cache=context.orderbook_cache,
            markets_by_currency=context.markets_by_currency,
            summaries=context.summaries,
            live=live,
            reason=reason,
        )

    def _plan_covered_call_spot_exit_fields(
        self,
        group: TradeGroup,
        *,
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]] | None,
        summaries: dict[str, AccountSummary],
        live: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        cover = group.covered_underlying_quantity if group.covered_underlying_quantity > 0 else group.quantity
        if cover <= 0:
            return {
                "amount": Decimal("0"),
                "cover": Decimal("0"),
                "settlement_loss": Decimal("0"),
                "settlement_loss_source": "none",
            }

        index_price = self._spot_exit_index_price_usd(group, orderbook_cache)
        short_instrument = self._spot_exit_short_instrument(group, markets_by_currency)
        settlement_loss, settlement_loss_source = resolve_covered_call_settlement_loss(
            group,
            index_price_usd=index_price,
            short_instrument=short_instrument,
            client=self.client,
            reason=reason,
            prefer_log=self.config.has_private_credentials,
        )
        target = max(cover - settlement_loss, Decimal("0"))

        summary = summaries.get(group.currency)
        if live:
            summary = self._account_summaries_by_currency().get(group.currency, summary)
        if summary is not None:
            available = max(summary.available_funds, summary.available_withdrawal_funds, summary.balance)
            if available <= 0:
                return {
                    "amount": Decimal("0"),
                    "cover": cover,
                    "settlement_loss": settlement_loss,
                    "settlement_loss_source": settlement_loss_source,
                }
            target = min(target, available)

        instrument_name = self._covered_call_spot_instrument(group.currency)
        contract_size, min_trade_amount = self._spot_min_trade_amount(instrument_name, group.currency)
        aligned = align_option_order_amount(target, contract_size, min_trade_amount)
        amount = aligned if contract_size > 0 or min_trade_amount > 0 else target
        return {
            "amount": amount,
            "cover": cover,
            "settlement_loss": settlement_loss,
            "settlement_loss_source": settlement_loss_source,
        }

    def _covered_call_spot_exit_amount(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
        reason: str = "",
    ) -> Decimal:
        return self._plan_covered_call_spot_exit(context, group, live=live, reason=reason)["amount"]
