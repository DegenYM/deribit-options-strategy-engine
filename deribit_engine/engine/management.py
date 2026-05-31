from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from ..bull_put_settlement import (
    group_uses_spread_settlement_pricing,
    in_spread_expiry_settlement_window,
    is_bull_put_spread_group,
    long_instrument_for_spread_reconcile,
    spread_expiry_close_debit_usdc,
)
from ..exceptions import TransientExchangeError
from ..exit_eval import (
    dynamic_tp_capture_pct,
    evaluate_early_exit_reason,
    exit_eval_context_from_config,
)
from ..exit_reasons import INCOME_EXIT_REASONS
from ..investor_cash_flow import sum_external_flow_native_in_window
from ..margin import (
    linear_usdc_short_call_initial_per_contract_usdc,
    linear_usdc_short_put_initial_per_contract_usdc,
    short_call_initial_unit,
    short_put_initial_unit,
)
from ..models import (
    AccountSummary,
    HedgePlan,
    OptionInstrument,
    OrderBookSnapshot,
    PortfolioSnapshot,
    Position,
    RiskRegime,
    StrategyState,
    TradeGroup,
)
from ..stress import _intrinsic_settlement
from ..utils import (
    align_option_order_amount,
    format_decimal,
    safe_div,
    utc_now,
    utc_now_ms,
)
from .context import (
    _MAX_SCAN_BLOCKER_LOG_LINES,
    LOG_REASON_NUMBER_RE,
    LOGGER,
    RECONCILE_EXTERNAL_CLOSE_GRACE_MS,
    ExchangePrefetch,
    RuntimeContext,
)


class ManagementMixin:
    def manage(self, *, live: bool = False, context: RuntimeContext | None = None) -> dict[str, Any]:
        if context is None:
            context = self._load_runtime()
        actions: list[dict[str, Any]] = []

        actions.extend(self._pending_covered_call_spot_exit_actions(context, live=live))
        actions.extend(self._pending_profit_sweep_actions(context, live=live))

        if context.snapshot.hard_derisk:
            cooldown_until = utc_now_ms() + (self.config.cooldown_hours * 3600 * 1000)
            # Route the cooldown to the specific book(s) that triggered the hard
            # derisk so the other books keep trading. Fall back to a portfolio-
            # wide cooldown for global triggers (crisis regime on an open group,
            # hard-defense delta / stop-loss hits) that aren't book-scoped.
            hard_books = [book for book, flag in context.snapshot.hard_derisk_by_book.items() if flag]
            global_trigger = context.snapshot.hard_derisk and not hard_books
            if live:
                for book in hard_books:
                    context.state.cooldown_until_ms_by_book[book] = cooldown_until
                if global_trigger:
                    context.state.cooldown_until_ms = cooldown_until
                reasons = context.snapshot.halt_entry_reasons
                self._telegram_alert(
                    "Hard derisk triggered",
                    body=f"books={hard_books or ['portfolio']}",
                    event_key=f"hard_derisk:{self._journal_scope_key()}",
                    level="critical",
                    extra={"reasons": "; ".join(reasons[:5]) if reasons else None},
                )
            actions.append(
                {
                    "action": "cooldown_started" if live else "cooldown_recommended",
                    "cooldown_until_ms": cooldown_until,
                    "reason": "hard_derisk",
                    "books": hard_books or ["portfolio"],
                }
            )

        for group in sorted(self._open_groups(context.state), key=lambda item: item.max_loss, reverse=True):
            group_actions = self._manage_group(context, group, live=live)
            actions.extend(group_actions)

        if self.config.enable_perp_hedge and context.snapshot.regime is RiskRegime.NORMAL:
            for currency in self.config.managed_currencies:
                unwind = self._maybe_unwind_hedge(context, currency=currency, live=live)
                if unwind is not None:
                    actions.append(unwind)

        if live:
            self._persist_trade_journal_actions(actions)
        self.state_store.save(context.state)
        return {
            "action": "manage",
            "live": live,
            "portfolio": context.snapshot.to_dict(),
            "trade_groups": self._trade_groups_payload(
                self._open_groups(context.state),
                context.option_positions,
                context.orderbook_cache,
            ),
            "actions": actions,
        }

    def run(
        self,
        *,
        live: bool = False,
        cycles: int = 1,
        currencies: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        iteration = 0
        cycle_results: list[dict[str, Any]] = []
        retain_results = cycles > 0
        last_log_signature: tuple[Any, ...] | None = None
        transient_failures = 0
        last_regime: str | None = None
        if live:
            self._write_live_heartbeat(cycle=0, regime=None, last_error=None)
        while cycles <= 0 or iteration < cycles:
            cycle_no = iteration + 1
            sleep_seconds = self.config.poll_seconds_normal
            if live:
                self._write_live_heartbeat(cycle=cycle_no, regime=last_regime, last_error=None)
            try:
                context = self._load_runtime()
                manage_result = self.manage(live=live, context=context)
                cycle_result: dict[str, Any] = {"manage": manage_result}

                status_after_manage = self._status_payload(context)
                cycle_result["status"] = status_after_manage
                candidates = self._scan_candidates(context, currencies=currencies, top_n=self.config.top_n)
                cycle_result["scan"] = self._scan_payload(
                    context,
                    candidates,
                    scan_currencies=currencies,
                    include_scan_diagnostics=False,
                )
                portfolio = status_after_manage["portfolio"]
                can_enter = not portfolio["halt_new_entries"]
                if can_enter:
                    cycle_result["entry"] = self._enter_best_from_candidates(
                        context, candidates=candidates[:1], live=live
                    )
                    if cycle_result["entry"].get("group") is not None:
                        context.state.groups.append(cycle_result["entry"]["group"])
                else:
                    reason = (
                        "hard_derisk"
                        if portfolio["hard_derisk"]
                        else "cooling_down"
                        if portfolio["cooling_down"]
                        else "halt_limit_reached"
                    )
                    cycle_result["entry"] = {
                        "action": "entry_skipped",
                        "reason": reason,
                    }

                entry_action = cycle_result["entry"].get("action", "")
                risk_blocked = portfolio["hard_derisk"] or portfolio["cooling_down"]
                if not candidates and not risk_blocked and entry_action != "naked_put_entered":
                    topup_actions = self._topup_existing_naked_groups(context, live=live)
                    if topup_actions:
                        cycle_result["topup"] = topup_actions
                if live:
                    self._persist_trade_journal_result(cycle_result.get("entry"))
                    self._persist_trade_journal_actions(cycle_result.get("topup") or [])
                self.state_store.save(context.state)
                log_signature = self._cycle_log_signature(cycle_result)
                if log_signature != last_log_signature:
                    self._log_cycle_update(cycle_no, cycle_result, live=live)
                    last_log_signature = log_signature
                last_regime = portfolio["regime"]
                if live:
                    self._write_live_heartbeat(cycle=cycle_no, regime=last_regime, last_error=None)
                if retain_results:
                    cycle_results.append(cycle_result)
                iteration += 1
                if cycles > 0 and iteration >= cycles:
                    break
                sleep_seconds = (
                    self.config.poll_seconds_stress
                    if portfolio["regime"] != RiskRegime.NORMAL.value
                    else self.config.poll_seconds_normal
                )
                transient_failures = 0
            except TransientExchangeError as exc:
                transient_failures += 1
                LOGGER.warning(
                    "run cycle=%s transient exchange error: %s; backing off before retry",
                    cycle_no,
                    exc,
                )
                if live:
                    self._write_live_heartbeat(cycle=cycle_no, regime=last_regime, last_error=str(exc))
                sleep_seconds = max(self.config.poll_seconds_stress * 6, 60)
                if live and transient_failures >= 5:
                    self._telegram_alert(
                        "Repeated Deribit API errors",
                        body=str(exc),
                        event_key=f"transient_api:{self._journal_scope_key()}",
                        level="warning",
                        extra={"consecutive_failures": transient_failures, "cycle": cycle_no},
                    )
            except Exception as exc:
                if live:
                    self._telegram_alert(
                        "Bot run loop crashed",
                        body=str(exc),
                        event_key=f"run_fatal:{self._journal_scope_key()}",
                        level="critical",
                        extra={"cycle": cycle_no},
                    )
                raise
            self.sleep_fn(sleep_seconds)
        return {"action": "run", "cycles": iteration, "results": cycle_results}

    def _load_runtime(self) -> RuntimeContext:
        if self.config.has_private_credentials:
            return self._load_runtime_from_exchange(self.fetch_exchange_prefetch())
        return self._load_runtime_from_exchange(None)

    def _load_runtime_from_exchange(
        self,
        prefetch: ExchangePrefetch | None,
        *,
        dashboard_display: bool = False,
    ) -> RuntimeContext:
        state = self.state_store.load()
        if prefetch is not None:
            summaries = prefetch.summaries
            open_orders = prefetch.open_orders
            positions = prefetch.positions
            option_positions = prefetch.option_positions
            future_positions = prefetch.future_positions
            future_markets_by_name = prefetch.future_markets_by_name
            markets_by_currency = prefetch.markets_by_currency
        else:
            summaries = {}
            open_orders = []
            positions = []
            option_positions = []
            future_positions = []
            future_markets_by_name = {}
            markets_by_currency = {currency: [] for currency in self.config.managed_currencies}
        orderbook_cache: dict[str, OrderBookSnapshot] = {}
        state = self._reset_daily_state(state, summaries)
        if not dashboard_display:
            # Refresh external cash-flow (deposit / withdrawal / transfer) tallies
            # from Deribit's transaction log so drawdown is measured against
            # trading P&L only, not user-initiated balance changes.
            self._refresh_cash_flows_by_book(state, orderbook_cache, summaries=summaries)
        state = self._reconcile_state(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
        )
        regime_by_currency: dict[str, RiskRegime] = {}
        regime_detail_by_currency: dict[str, tuple[str, ...]] = {}
        for currency in self.config.managed_currencies:
            if dashboard_display:
                regime, detail = self._determine_regime_for_dashboard(currency)
            else:
                regime, detail = self._determine_regime_with_detail(
                    currency,
                    markets=markets_by_currency[currency],
                    orderbook_cache=orderbook_cache,
                )
            regime_by_currency[currency] = regime
            regime_detail_by_currency[currency] = tuple(detail)
        self._refresh_vol_entry_context()
        self._update_recovery_counts(state, regime_by_currency)
        for group in self._open_groups(state):
            self._refresh_group(context_markets=markets_by_currency, group=group, orderbook_cache=orderbook_cache)
        if self._is_covered_call_strategy():
            self._clear_covered_call_book_cooldowns(state, summaries)
        snapshot = self._build_portfolio_snapshot(
            state=state,
            summaries=summaries,
            regime_by_currency=regime_by_currency,
            regime_detail_by_currency=regime_detail_by_currency,
            future_positions=future_positions,
            orderbook_cache=orderbook_cache,
        )
        return RuntimeContext(
            state=state,
            summaries=summaries,
            open_orders=open_orders,
            positions=positions,
            option_positions=option_positions,
            future_positions=future_positions,
            future_markets_by_name=future_markets_by_name,
            markets_by_currency=markets_by_currency,
            orderbook_cache=orderbook_cache,
            regime_by_currency=regime_by_currency,
            snapshot=snapshot,
        )

    def _log_cycle_update(self, cycle_no: int, cycle_result: dict[str, Any], *, live: bool) -> None:
        status = cycle_result["status"]
        portfolio = status["portfolio"]
        manage_actions = cycle_result["manage"].get("actions", [])
        entry = cycle_result["entry"]
        log_extra = {"cycle": cycle_no, "regime": portfolio["regime"]}
        LOGGER.info(
            "run cycle=%s live=%s regime=%s open_groups=%s manage_actions=%s entry_action=%s",
            cycle_no,
            live,
            portfolio["regime"],
            len(status.get("trade_groups", [])),
            len(manage_actions),
            entry["action"],
            extra=log_extra,
        )
        if manage_actions:
            LOGGER.info(
                "run cycle=%s manage_action_types=%s",
                cycle_no,
                ",".join(action["action"] for action in manage_actions),
            )
        if entry.get("reason"):
            LOGGER.info("run cycle=%s entry_reason=%s", cycle_no, entry["reason"])
        regime_by_currency = portfolio.get("regime_by_currency") or {}
        regime_detail_by_currency = portfolio.get("regime_detail_by_currency") or {}
        for currency in sorted(regime_by_currency):
            regime_value = regime_by_currency[currency]
            if regime_value == RiskRegime.NORMAL.value:
                continue
            detail = regime_detail_by_currency.get(currency) or ()
            detail_text = "; ".join(detail) if detail else "(no detail)"
            LOGGER.info(
                "run cycle=%s [regime] %s=%s — %s",
                cycle_no,
                currency,
                regime_value,
                detail_text,
            )
        blockers = cycle_result["scan"].get("entry_blockers", [])
        if blockers and not cycle_result["scan"].get("candidates"):
            for line in blockers[:_MAX_SCAN_BLOCKER_LOG_LINES]:
                LOGGER.info("run cycle=%s [scan] %s", cycle_no, line)
            if len(blockers) > _MAX_SCAN_BLOCKER_LOG_LINES:
                LOGGER.info(
                    "run cycle=%s [scan] ... %s more blocker lines omitted",
                    cycle_no,
                    len(blockers) - _MAX_SCAN_BLOCKER_LOG_LINES,
                )
        self._log_cycle_candidates(cycle_no, cycle_result["scan"]["candidates"])
        topup_actions = cycle_result.get("topup", [])
        if topup_actions:
            LOGGER.info(
                "run cycle=%s topup_actions=%s",
                cycle_no,
                ",".join(a["action"] for a in topup_actions),
            )

    def _cycle_log_signature(self, cycle_result: dict[str, Any]) -> tuple[Any, ...]:
        status = cycle_result["status"]
        portfolio = status["portfolio"]
        scan = cycle_result["scan"]
        entry = cycle_result["entry"]
        return (
            portfolio["regime"],
            portfolio["halt_new_entries"],
            portfolio["hard_derisk"],
            portfolio["cooling_down"],
            self._normalized_log_reasons(portfolio.get("halt_entry_reasons", [])),
            tuple(
                sorted(
                    (
                        group["group_id"],
                        group["currency"],
                        group["short_instrument_name"],
                        str(group["quantity"]),
                        group["status"],
                    )
                    for group in status.get("trade_groups", [])
                )
            ),
            tuple(self._cycle_action_signature(action) for action in cycle_result["manage"].get("actions", [])),
            scan.get("candidate_count", 0),
            tuple(
                (
                    candidate["currency"],
                    candidate["short_instrument_name"],
                )
                for candidate in scan.get("candidates", [])[:3]
            ),
            self._normalized_log_reasons(scan.get("entry_blockers", [])),
            tuple(
                (
                    currency,
                    portfolio.get("regime_by_currency", {}).get(currency),
                    self._normalized_log_reasons(detail),
                )
                for currency, detail in sorted((portfolio.get("regime_detail_by_currency") or {}).items())
            ),
            (
                entry.get("action"),
                entry.get("reason"),
                self._cycle_entry_signature(entry),
            ),
            tuple(a.get("action") for a in cycle_result.get("topup", [])),
        )

    def _normalized_log_reasons(self, reasons: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self._normalize_log_reason(reason) for reason in reasons)

    @staticmethod
    def _normalize_log_reason(reason: str) -> str:
        normalized = reason.strip()
        if " (" in normalized:
            normalized = normalized.split(" (", 1)[0]
        if "; " in normalized:
            normalized = normalized.split("; ", 1)[0]
        normalized = LOG_REASON_NUMBER_RE.sub("#", normalized)
        return " ".join(normalized.split())

    def _cycle_action_signature(self, action: dict[str, Any]) -> tuple[Any, ...]:
        return (
            action.get("action"),
            action.get("group_id"),
            action.get("reason"),
            action.get("currency"),
            action.get("instrument_name"),
            action.get("short_instrument_name"),
        )

    def _cycle_entry_signature(self, entry: dict[str, Any]) -> tuple[Any, ...] | None:
        candidate = entry.get("candidate")
        if not isinstance(candidate, dict):
            return None
        return (
            candidate.get("currency"),
            candidate.get("short_instrument_name"),
        )

    def _manage_group(self, context: RuntimeContext, group: TradeGroup, *, live: bool) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if self._is_covered_call_group(group):
            return self._manage_covered_call_group(context, group, live=live)
        soft_delta, hard_delta = self._defense_delta_thresholds(group)
        hard_trigger = group.short_delta >= hard_delta or group.loss_pct_of_max_loss >= self.config.hard_stop_loss_pct
        soft_trigger = (
            group.short_delta >= soft_delta or group.loss_pct_of_max_loss >= self.config.soft_defense_loss_pct
        )
        if hard_trigger:
            if self.config.enable_perp_hedge:
                hedge_plan = self._build_hedge_plan(context, group.currency, mode="hard")
                if hedge_plan is not None:
                    actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
            actions.extend(self._close_group(context, group, reason="hard_stop", live=live))
            return actions
        if group.profit_capture >= dynamic_tp_capture_pct(group.dte_days, exit_eval_context_from_config(self.config)):
            actions.extend(self._close_group(context, group, reason="take_profit", live=live))
            return actions
        early_exit_reason = self._maybe_early_exit_reason(context, group)
        if early_exit_reason is not None:
            actions.extend(self._close_group(context, group, reason=early_exit_reason, live=live))
            return actions
        robust_exit_actions = self._maybe_covered_call_robust_spot_exit(context, group, live=live)
        if robust_exit_actions is not None:
            return robust_exit_actions
        if group.dte_days <= self.config.time_exit_dte:
            actions.extend(self._close_group(context, group, reason="time_exit", live=live))
            return actions
        if soft_trigger:
            if self.config.enable_perp_hedge:
                hedge_plan = self._build_hedge_plan(context, group.currency, mode="soft")
                if hedge_plan is not None:
                    actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
                else:
                    actions.extend(self._close_group(context, group, reason="soft_stop_no_hedge", live=live))
            else:
                actions.extend(self._close_group(context, group, reason="soft_stop", live=live))
        return actions

    def _manage_covered_call_group(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """Covered calls: OTM uses income exits (TP / early / time); ITM uses spot exit."""
        if self._covered_call_itm(group, context):
            robust_exit_actions = self._maybe_covered_call_robust_spot_exit(context, group, live=live)
            if robust_exit_actions is not None:
                return robust_exit_actions
            return []
        actions: list[dict[str, Any]] = []
        if group.profit_capture >= dynamic_tp_capture_pct(group.dte_days, exit_eval_context_from_config(self.config)):
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
        """Queue a post-close spot sale of native premium profit to USDT."""
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
        native = group.realized_pnl_collateral_native
        if native is None or native <= 0:
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
        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if (
                group.status == "closed"
                and self._is_covered_call_group(group)
                and group.profit_sweep_status == "pending"
            ):
                actions.append(self._execute_covered_call_profit_sweep(context, group, live=live))
        return actions

    @staticmethod
    def _covered_call_profit_sweep_instrument(currency: str) -> str:
        return ManagementMixin._covered_call_spot_instrument(currency)

    def _profit_sweep_amount(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> Decimal:
        target = group.profit_sweep_amount
        if target <= 0 and group.realized_pnl_collateral_native is not None:
            target = group.realized_pnl_collateral_native
        if target <= 0:
            return Decimal("0")

        summary = context.summaries.get(group.currency)
        if live:
            summary = self._account_summaries_by_currency().get(group.currency, summary)
        if summary is not None:
            available = max(summary.available_funds, summary.available_withdrawal_funds, summary.balance)
            if available <= 0:
                return Decimal("0")
            target = min(target, available)

        instrument_name = self._covered_call_profit_sweep_instrument(group.currency)
        contract_size, min_trade_amount = self._spot_min_trade_amount(instrument_name, group.currency)
        aligned = align_option_order_amount(target, contract_size, min_trade_amount)
        if contract_size > 0 or min_trade_amount > 0:
            return aligned
        return target

    def _execute_covered_call_profit_sweep(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> dict[str, Any]:
        if group.profit_sweep_status in {"submitted", "filled"}:
            return {
                "action": "covered_call_profit_sweep_skipped",
                "group_id": group.group_id,
                "reason": f"already_{group.profit_sweep_status}",
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
        payload["profit_sweep_status"] = group.profit_sweep_status
        payload["profit_sweep_order_id"] = group.profit_sweep_order_id or None
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

    @staticmethod
    def _is_covered_call_group(group: TradeGroup) -> bool:
        return group.option_type == "call" and (
            (group.strategy or "") == "covered_call"
            or group.covered_underlying_quantity > 0
            or group.short_label.startswith("covered_call-")
        )

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

    def _covered_call_spot_exit_amount(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> Decimal:
        target = group.covered_underlying_quantity if group.covered_underlying_quantity > 0 else group.quantity
        if target <= 0:
            return Decimal("0")

        summary = context.summaries.get(group.currency)
        if live:
            summary = self._account_summaries_by_currency().get(group.currency, summary)
        if summary is not None:
            available = max(summary.available_funds, summary.available_withdrawal_funds, summary.balance)
            if available <= 0:
                return Decimal("0")
            target = min(target, available)

        instrument_name = self._covered_call_spot_instrument(group.currency)
        contract_size, min_trade_amount = self._spot_min_trade_amount(instrument_name, group.currency)
        aligned = align_option_order_amount(target, contract_size, min_trade_amount)
        if contract_size > 0 or min_trade_amount > 0:
            return aligned
        return target

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

    def _maybe_unwind_hedge(self, context: RuntimeContext, *, currency: str, live: bool) -> dict[str, Any] | None:
        recovery_count = context.state.normal_recovery_counts.get(currency, 0)
        if recovery_count < self.config.recovery_normal_cycles:
            return None
        current_perp_base = self._current_hedge_base(context.future_positions, currency)
        if current_perp_base >= 0:
            return None
        unwind_base = abs(current_perp_base) / Decimal("2")
        if unwind_base <= 0:
            return None
        index_price = self._currency_index_price(currency, context.orderbook_cache)
        amount = self._align_future_order_amount(
            context,
            instrument_name=self._perp_instrument(currency),
            amount=unwind_base * index_price,
        )
        if amount <= 0:
            return None
        unwind_base = amount / index_price
        action = {
            "action": "hedge_unwind",
            "currency": currency,
            "instrument_name": self._perp_instrument(currency),
            "amount": format_decimal(amount, 8),
            "base_amount": format_decimal(unwind_base, 8),
            "live": live,
        }
        if live:
            response = self.client.place_buy_order(
                instrument_name=self._perp_instrument(currency),
                amount=amount,
                label=self._hedge_label(currency, "recovery"),
                order_type="market",
                reduce_only=True,
            )
            action["response"] = response
        return action

    def _build_portfolio_snapshot(
        self,
        *,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        regime_by_currency: dict[str, RiskRegime],
        regime_detail_by_currency: dict[str, tuple[str, ...]],
        future_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> PortfolioSnapshot:
        total_equity_usdc = self._total_equity_usdc(summaries, orderbook_cache)
        per_book_equities = self._book_equities_usdc(summaries, orderbook_cache)
        per_book_native_equities = self._book_equities_native(summaries)
        # Aggregate day_start: prefer the sum of per-book starts (covers the
        # three-book world); fall back to the legacy scalar for v1 state files
        # that still hold only ``day_start_equity_usdc``.
        per_book_day_start: dict[str, Decimal] = {}
        per_book_native_day_start: dict[str, Decimal] = {}
        for book, equity in per_book_equities.items():
            start = state.day_start_equity_by_book.get(book)
            per_book_day_start[book] = start if (start and start > 0) else equity
            native_equity = per_book_native_equities.get(book, Decimal("0"))
            native_start = state.day_start_equity_native_by_book.get(book)
            if native_start and native_start > 0:
                per_book_native_day_start[book] = native_start
            elif book in ("USDC", "USDT"):
                per_book_native_day_start[book] = per_book_day_start[book]
            else:
                per_book_native_day_start[book] = native_equity
        if state.day_start_equity_by_book:
            day_start_equity = sum(per_book_day_start.values(), Decimal("0"))
        else:
            day_start_equity = state.day_start_equity_usdc or total_equity_usdc

        # ------------------------------------------------------------------
        # Daily PnL (exclude deposit/withdraw/transfer)
        # ------------------------------------------------------------------
        # ``day_net_flow_usdc_by_book`` is refreshed from Deribit's transaction log
        # and tracks external cash flow since UTC day-start.
        day_net_flow_usdc_by_book: dict[str, Decimal] = {
            book: state.day_net_flow_usdc_by_book.get(book, Decimal("0")) for book in per_book_equities
        }
        day_net_flow_native_by_book: dict[str, Decimal] = {
            book: state.day_net_flow_native_by_book.get(book, Decimal("0")) for book in per_book_equities
        }
        day_pnl_usdc_ex_flow_by_book: dict[str, Decimal] = {
            book: per_book_equities.get(book, Decimal("0"))
            - per_book_day_start.get(book, Decimal("0"))
            - day_net_flow_usdc_by_book.get(book, Decimal("0"))
            for book in per_book_equities
        }
        day_net_flow_usdc = sum(day_net_flow_usdc_by_book.values(), Decimal("0"))
        day_pnl_usdc_ex_flow = total_equity_usdc - day_start_equity - day_net_flow_usdc
        day_pnl_usdc_ex_flow_ex_spot_by_book: dict[str, Decimal] = {}
        for book in per_book_equities:
            native_equity = per_book_native_equities.get(book, Decimal("0"))
            native_start = per_book_native_day_start.get(book, native_equity)
            native_flow = day_net_flow_native_by_book.get(book, Decimal("0"))
            spot = Decimal("1") if book in ("USDC", "USDT") else self._currency_index_price(book, orderbook_cache)
            day_pnl_usdc_ex_flow_ex_spot_by_book[book] = (native_equity - native_start - native_flow) * spot
        day_pnl_usdc_ex_flow_ex_spot = sum(
            day_pnl_usdc_ex_flow_ex_spot_by_book.values(),
            Decimal("0"),
        )
        # Per-book drawdown is the primary gate; aggregate drawdown is kept for
        # reports and is the *worst* book's drawdown so a single breach still
        # shows up without misleading dilution across pools.
        #
        # Drawdown is measured in each book's collateral unit (BTC / ETH /
        # USDC), not USDC-equivalent equity. This keeps spot price moves from
        # tripping inverse-native books such as covered calls.
        #
        # Two corrections vs. a naive (start - now) / start:
        #
        # 1. External cash-flow adjustment. ``day_net_flow_native_by_book`` is
        #    refreshed from Deribit's transaction log each cycle. Adding it
        #    to ``start`` turns the formula into "expected equity if no
        #    trading happened", so a user withdrawal / deposit does not
        #    masquerade as a trading loss.
        #
        # 2. Dust floor. Books that started the day below
        #    ``min_book_equity_usdc`` on the reporting view are excluded
        #    entirely. Without this a few-cent BTC dust balance can produce
        #    >100% phantom drawdowns when the tiny balance is moved around.
        per_book_drawdown: dict[str, Decimal] = {}
        min_equity = self.config.min_book_equity_usdc
        for book, equity in per_book_native_equities.items():
            start_usdc = per_book_day_start.get(book, Decimal("0"))
            if start_usdc <= min_equity:
                continue
            start = per_book_native_day_start.get(book, equity)
            if start <= 0:
                continue
            if book in ("USDC", "USDT"):
                net_flow = day_net_flow_usdc_by_book.get(book, Decimal("0"))
            else:
                net_flow = day_net_flow_native_by_book.get(book, Decimal("0"))
            adjusted_start = start + net_flow
            per_book_drawdown[book] = safe_div(
                max(adjusted_start - equity, Decimal("0")),
                start,
            )
        day_drawdown_pct = max(per_book_drawdown.values()) if per_book_drawdown else Decimal("0")
        effective_capital = self._effective_capital(total_equity_usdc)
        open_max_loss = self._open_max_loss(state)
        open_max_loss_pct = safe_div(open_max_loss, effective_capital)
        initial_margin_usdc = self._aggregate_margin(summaries, orderbook_cache, margin_kind="initial")
        maintenance_margin_usdc = self._aggregate_margin(summaries, orderbook_cache, margin_kind="maintenance")
        initial_margin_ratio = safe_div(initial_margin_usdc, total_equity_usdc)
        maintenance_margin_ratio = safe_div(maintenance_margin_usdc, total_equity_usdc)
        projected_max_profit_run_rate = self._projected_max_profit_run_rate(state)
        projected_max_profit_apr = safe_div(projected_max_profit_run_rate, total_equity_usdc)
        target_annual_pnl = effective_capital * self.config.target_portfolio_apr
        target_progress_ratio = safe_div(projected_max_profit_run_rate, target_annual_pnl)
        overall_regime = self._overall_regime(regime_by_currency.values())
        now_ms = utc_now_ms()
        # Per-book cooldown: keep legacy aggregate read as the portfolio-wide
        # fallback, but prefer book-specific values when present.
        cooldown_by_book: dict[str, int | None] = {
            book: state.cooldown_until_ms_by_book.get(book) for book in per_book_equities
        }
        cooling_by_book: dict[str, bool] = {book: bool(ts and ts > now_ms) for book, ts in cooldown_by_book.items()}
        legacy_cooling = bool(state.cooldown_until_ms and state.cooldown_until_ms > now_ms)
        cooling_down = legacy_cooling or any(cooling_by_book.values())
        open_groups = self._open_groups(state)
        crisis_open_group = any(
            regime_by_currency.get(group.currency, RiskRegime.CRISIS) is RiskRegime.CRISIS for group in open_groups
        )
        crisis_derisk = self.config.hard_derisk_on_crisis_open_group and crisis_open_group
        hard_stop_groups = (
            [group for group in open_groups if not self._is_covered_call_group(group)]
            if self._is_covered_call_strategy()
            else open_groups
        )
        hard_stop_open_group = any(
            group.short_delta >= self._defense_delta_thresholds(group)[1]
            or group.loss_pct_of_max_loss >= self.config.hard_stop_loss_pct
            for group in hard_stop_groups
        )
        per_currency_ratios = self._per_currency_margin_ratios(summaries)
        # Per-book gates. A single book breaching its hard IM/MM ceiling or its
        # own drawdown floor halts that book only — the other books stay live.
        hard_derisk_by_book: dict[str, bool] = {book: False for book in per_book_equities}
        halt_entries_by_book: dict[str, bool] = {book: False for book in per_book_equities}
        halt_reasons_by_book: dict[str, list[str]] = {book: [] for book in per_book_equities}
        book_hard_breaches: list[str] = []

        for book in per_book_equities:
            shielded = self._covered_call_book_fully_collateralized(state, summaries, book)
            dd = per_book_drawdown.get(book, Decimal("0"))
            if not shielded and dd >= self.config.hard_derisk_drawdown_pct:
                hard_derisk_by_book[book] = True
                halt_reasons_by_book[book].append(
                    f"hard_derisk: day_drawdown_pct >= hard_derisk_drawdown_pct "
                    f"({format_decimal(dd, 8)} >= {format_decimal(self.config.hard_derisk_drawdown_pct, 6)})"
                )
            if not shielded and dd >= self.config.halt_drawdown_pct:
                halt_entries_by_book[book] = True
                halt_reasons_by_book[book].append(
                    f"day_drawdown_pct >= halt_drawdown_pct "
                    f"({format_decimal(dd, 8)} >= {format_decimal(self.config.halt_drawdown_pct, 6)})"
                )
            if cooling_by_book.get(book) and not shielded:
                halt_entries_by_book[book] = True
                halt_reasons_by_book[book].append("cooldown_active")
            if self._book_entry_cooldown_active(state, book) and not shielded:
                halt_entries_by_book[book] = True
                halt_reasons_by_book[book].append(f"entry_cooldown_active ({self.config.entry_cooldown_minutes}m)")

        for collateral_ccy, (book_im, book_mm) in per_currency_ratios.items():
            if self._covered_call_book_im_mm_shielded(
                state,
                summaries,
                collateral_ccy,
                available_cover=self._available_covered_call_quantity_from_summaries(state, summaries, collateral_ccy),
            ):
                continue
            if book_im >= self.config.book_im_hard:
                breach = (
                    f"{collateral_ccy}: im_ratio>=book_im_hard "
                    f"({format_decimal(book_im, 8)}>={format_decimal(self.config.book_im_hard, 6)})"
                )
                book_hard_breaches.append(breach)
                hard_derisk_by_book[collateral_ccy] = True
                halt_entries_by_book[collateral_ccy] = True
                halt_reasons_by_book.setdefault(collateral_ccy, []).append(f"hard_derisk: book {breach}")
            if book_mm >= self.config.book_mm_hard:
                breach = (
                    f"{collateral_ccy}: mm_ratio>=book_mm_hard "
                    f"({format_decimal(book_mm, 8)}>={format_decimal(self.config.book_mm_hard, 6)})"
                )
                book_hard_breaches.append(breach)
                hard_derisk_by_book[collateral_ccy] = True
                halt_entries_by_book[collateral_ccy] = True
                halt_reasons_by_book.setdefault(collateral_ccy, []).append(f"hard_derisk: book {breach}")

        book_hard_derisk = bool(book_hard_breaches) or any(hard_derisk_by_book.values())
        hard_derisk = book_hard_derisk or crisis_derisk or hard_stop_open_group
        # When macro feeds are unavailable `_determine_regime_with_detail` returns
        # ELEVATED with a detail line prefixed "data_unavailable". Treat that as a
        # halt signal so we don't open new risk while blind; but leave hard_derisk
        # clear so existing positions aren't liquidated on a data blip.
        data_unavailable_regime = any(
            any(note.startswith("data_unavailable") for note in regime_detail_by_currency.get(currency, ()))
            for currency in regime_by_currency
        )
        non_normal_regime = any(regime is not RiskRegime.NORMAL for regime in regime_by_currency.values())
        halt_new_entries = (
            cooling_down
            or open_max_loss_pct >= self.config.halt_open_max_loss_pct
            or hard_derisk
            or non_normal_regime
            or data_unavailable_regime
            or any(halt_entries_by_book.values())
        )
        halt_entry_reasons: list[str] = []
        if legacy_cooling:
            halt_entry_reasons.append("cooldown_active")
        if open_max_loss_pct >= self.config.halt_open_max_loss_pct:
            halt_entry_reasons.append(
                f"open_max_loss_pct >= halt_open_max_loss_pct "
                f"({format_decimal(open_max_loss_pct, 8)} >= {format_decimal(self.config.halt_open_max_loss_pct, 6)})"
            )
        if crisis_derisk:
            halt_entry_reasons.append("hard_derisk: open_trade_group_in_crisis_regime_currency")
        if hard_stop_open_group:
            halt_entry_reasons.append("hard_derisk: open_group_hard_defense_or_stop_trigger")
        # Surface per-book halts so log lines still show which book triggered.
        for book in sorted(halt_entries_by_book):
            for reason in halt_reasons_by_book.get(book, []):
                prefixed = f"book={book} {reason}"
                if prefixed not in halt_entry_reasons:
                    halt_entry_reasons.append(prefixed)
        if data_unavailable_regime:
            affected = [
                currency
                for currency in sorted(regime_by_currency)
                if any(note.startswith("data_unavailable") for note in regime_detail_by_currency.get(currency, ()))
            ]
            halt_entry_reasons.append("regime data_unavailable: " + ", ".join(affected))
        elif non_normal_regime:
            escalated = [
                f"{currency}={regime.value}"
                for currency, regime in sorted(regime_by_currency.items())
                if regime is not RiskRegime.NORMAL
            ]
            halt_entry_reasons.append("regime non_normal: " + ", ".join(escalated))
        if halt_new_entries and not halt_entry_reasons:
            halt_entry_reasons.append("halt_new_entries (composite; check portfolio flags)")
        return PortfolioSnapshot(
            total_equity_usdc=total_equity_usdc,
            day_start_equity_usdc=day_start_equity,
            day_net_flow_usdc=day_net_flow_usdc,
            day_pnl_usdc_ex_flow=day_pnl_usdc_ex_flow,
            day_drawdown_pct=day_drawdown_pct,
            open_max_loss=open_max_loss,
            open_max_loss_pct=open_max_loss_pct,
            initial_margin_ratio=initial_margin_ratio,
            maintenance_margin_ratio=maintenance_margin_ratio,
            projected_max_profit_run_rate_usdc=projected_max_profit_run_rate,
            projected_max_profit_apr=projected_max_profit_apr,
            target_progress_ratio=target_progress_ratio,
            regime=overall_regime,
            halt_new_entries=halt_new_entries,
            hard_derisk=hard_derisk,
            cooldown_until_ms=state.cooldown_until_ms,
            cooling_down=cooling_down,
            delta_totals_by_currency=self._delta_totals_by_currency(summaries, state, future_positions),
            regime_by_currency=regime_by_currency,
            halt_entry_reasons=tuple(halt_entry_reasons),
            regime_detail_by_currency=regime_detail_by_currency,
            margin_ratios_by_currency=per_currency_ratios,
            equity_by_book=per_book_equities,
            day_start_equity_by_book=per_book_day_start,
            day_net_flow_usdc_by_book=day_net_flow_usdc_by_book,
            day_pnl_usdc_ex_flow_by_book=day_pnl_usdc_ex_flow_by_book,
            day_pnl_usdc_ex_flow_ex_spot=day_pnl_usdc_ex_flow_ex_spot,
            day_pnl_usdc_ex_flow_ex_spot_by_book=day_pnl_usdc_ex_flow_ex_spot_by_book,
            day_drawdown_pct_by_book=per_book_drawdown,
            cooldown_until_ms_by_book=cooldown_by_book,
            cooling_down_by_book=cooling_by_book,
            hard_derisk_by_book=hard_derisk_by_book,
            halt_entries_by_book=halt_entries_by_book,
            halt_entry_reasons_by_book={book: tuple(reasons) for book, reasons in halt_reasons_by_book.items()},
        )

    def _determine_regime_for_dashboard(self, currency: str) -> tuple[RiskRegime, list[str]]:
        """Dashboard-only regime: macro feeds + cache, no option-book liquidity scan."""
        drawdown = self._index_drawdown_24h(currency)
        dvol_ratio = self._dvol_ratio(currency)
        return self._regime_from_macro_feeds(
            currency,
            drawdown=drawdown,
            dvol_ratio=dvol_ratio,
            unavailable_default=RiskRegime.NORMAL,
            unavailable_note="dashboard:data_unavailable",
        )

    def _regime_from_macro_feeds(
        self,
        currency: str,
        *,
        drawdown: Decimal | None,
        dvol_ratio: Decimal | None,
        unavailable_default: RiskRegime,
        unavailable_note: str,
    ) -> tuple[RiskRegime, list[str]]:
        if drawdown is None or dvol_ratio is None:
            missing = []
            if drawdown is None:
                missing.append("index_chart_data")
            if dvol_ratio is None:
                missing.append("volatility_index_data")
            cached = self._last_regime_cache.get(currency)
            if cached is not None:
                cached_regime, _ = cached
                return cached_regime, [
                    f"{unavailable_note}({','.join(missing)}); using cached regime={cached_regime.value}",
                ]
            return unavailable_default, [
                f"{unavailable_note}({','.join(missing)}); defaulting to {unavailable_default.value}",
            ]

        if drawdown <= -self.config.index_drawdown_crisis_pct:
            regime = RiskRegime.CRISIS
            detail = [
                f"index_24h_drawdown <= -index_drawdown_crisis_pct "
                f"({format_decimal(drawdown, 8)} <= -{format_decimal(self.config.index_drawdown_crisis_pct, 6)})",
            ]
        elif dvol_ratio > self.config.dvol_crisis_multiplier:
            regime = RiskRegime.CRISIS
            detail = [
                f"dvol_ratio > dvol_crisis_multiplier "
                f"({format_decimal(dvol_ratio, 6)} > {format_decimal(self.config.dvol_crisis_multiplier, 6)})",
            ]
        elif drawdown <= -self.config.index_drawdown_elevated_pct or dvol_ratio > self.config.dvol_elevated_multiplier:
            regime = RiskRegime.ELEVATED
            detail = [
                f"elevated: drawdown={format_decimal(drawdown, 8)} "
                f"dvol_ratio={format_decimal(dvol_ratio, 6)} "
                f"(thresholds -elevated {format_decimal(self.config.index_drawdown_elevated_pct, 6)} / {format_decimal(self.config.dvol_elevated_multiplier, 6)})",
            ]
        else:
            regime = RiskRegime.NORMAL
            detail = ["market_conditions_normal"]

        self._last_regime_cache[currency] = (regime, utc_now_ms())
        return regime, detail

    def _determine_regime_with_detail(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> tuple[RiskRegime, list[str]]:
        if not markets:
            return RiskRegime.CRISIS, ["no_option_markets_loaded_for_currency"]
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        ok, liq_notes = self.strategy.core_regime_liquidity_detail(currency, markets, loader)
        if not ok:
            return RiskRegime.CRISIS, ["core_entry_liquidity_check_failed", *liq_notes]

        drawdown = self._index_drawdown_24h(currency)
        dvol_ratio = self._dvol_ratio(currency)
        return self._regime_from_macro_feeds(
            currency,
            drawdown=drawdown,
            dvol_ratio=dvol_ratio,
            unavailable_default=RiskRegime.ELEVATED,
            unavailable_note="data_unavailable",
        )

    def _determine_regime(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> RiskRegime:
        regime, _ = self._determine_regime_with_detail(currency, markets=markets, orderbook_cache=orderbook_cache)
        return regime

    def _build_hedge_plan(self, context: RuntimeContext, currency: str, *, mode: str) -> HedgePlan | None:
        current_delta = context.snapshot.delta_totals_by_currency.get(currency, Decimal("0"))
        index_price = self._currency_index_price(currency, context.orderbook_cache)
        if index_price <= 0:
            return None
        effective_capital = self._effective_capital(context.snapshot.total_equity_usdc)
        target_pct = self.config.hard_hedge_delta_cap_pct if mode == "hard" else self.config.soft_hedge_delta_cap_pct
        target_cap_base = (effective_capital * target_pct) / index_price
        current_hedge = self._current_hedge_base(context.future_positions, currency)
        option_delta = current_delta - current_hedge
        if option_delta > target_cap_base:
            target_hedge_base = target_cap_base - option_delta
        elif option_delta < -target_cap_base:
            target_hedge_base = -target_cap_base - option_delta
        else:
            return None
        delta_change_base = target_hedge_base - current_hedge
        if abs(delta_change_base) <= Decimal("0.0001"):
            return None
        side = "sell" if delta_change_base < 0 else "buy"
        order_amount = self._align_future_order_amount(
            context,
            instrument_name=self._perp_instrument(currency),
            amount=abs(delta_change_base) * index_price,
        )
        if order_amount <= 0:
            return None
        delta_change_base = order_amount / index_price
        if side == "sell":
            delta_change_base *= Decimal("-1")
        target_hedge_base = current_hedge + delta_change_base
        return HedgePlan(
            currency=currency,
            mode=mode,
            instrument_name=self._perp_instrument(currency),
            side=side,
            delta_change_base=delta_change_base,
            order_amount=order_amount,
            target_delta_cap_base=target_cap_base,
            current_delta_base=current_delta,
            current_hedge_base=current_hedge,
            target_hedge_base=target_hedge_base,
            note=f"{mode}_hedge",
        )

    def _heal_legacy_flow_double_count(
        self,
        state: StrategyState,
        *,
        book: str,
        equity_native: Decimal,
        now_ms: int,
    ) -> None:
        """Re-anchor books whose day_net_flow duplicates day-start deposits."""
        if book in state.day_equity_anchor_ms_by_book:
            return
        flow_native = state.day_net_flow_native_by_book.get(book, Decimal("0"))
        start_native = state.day_start_equity_native_by_book.get(book, Decimal("0"))
        if start_native > 0 and flow_native > 0:
            ratio = safe_div(flow_native, start_native)
            if Decimal("0.95") <= ratio <= Decimal("1.05") and equity_native >= start_native * Decimal("0.95"):
                state.day_equity_anchor_ms_by_book[book] = now_ms
                state.day_net_flow_usdc_by_book[book] = Decimal("0")
                state.day_net_flow_native_by_book[book] = Decimal("0")
                state.last_flow_query_ms_by_book.pop(book, None)
                return
        day_start_ms = self._day_start_ms_from_key(state.day_key)
        if day_start_ms > 0:
            state.day_equity_anchor_ms_by_book[book] = day_start_ms

    def _refresh_cash_flows_by_book(
        self,
        state: StrategyState,
        orderbook_cache: dict[str, OrderBookSnapshot],
        *,
        summaries: dict[str, AccountSummary] | None = None,
        force: bool = False,
    ) -> None:
        """Refresh the net external cash-flow tally for each traded book.

        Queries Deribit's ``private/get_transaction_log`` (paginated) for every
        currency in ``traded_collaterals``, filters to external-flow types
        (deposit, withdrawal, transfer), and sums signed amounts. Sub-account
        transfers from the Deribit UI appear as ``transfer`` on each API login.

        The query window starts at ``max(UTC midnight, day_equity_anchor_ms)`` so
        deposits already baked into day-start equity are not counted twice.

        Calls are throttled per book by ``cash_flow_query_interval_seconds``.
        Failures are logged and swallowed so a single API flake does not
        block the rest of the cycle.
        """
        if not self.config.has_private_credentials:
            return
        if self._day_start_ms_from_key(state.day_key) <= 0:
            return
        now_ms = utc_now_ms()
        interval_ms = max(self.config.cash_flow_query_interval_seconds, 1) * 1000

        for collateral_raw in self.config.traded_collaterals:
            collateral = collateral_raw.upper()
            flow_start_ms = self._flow_query_start_ms(state, collateral)
            if flow_start_ms <= 0:
                continue
            last_query = state.last_flow_query_ms_by_book.get(collateral, 0)
            if not force and last_query and (now_ms - last_query) < interval_ms:
                continue

            try:
                net_native = sum_external_flow_native_in_window(
                    self.client,
                    currency=collateral,
                    start_timestamp_ms=flow_start_ms,
                    end_timestamp_ms=now_ms,
                )
            except Exception as exc:
                LOGGER.warning(
                    "cash_flow_refresh_failed currency=%s err=%s",
                    collateral,
                    exc,
                )
                continue

            if collateral in ("USDC", "USDT"):
                net_usdc = net_native
            else:
                index_price = self._currency_index_price(collateral, orderbook_cache)
                net_usdc = net_native * index_price

            state.day_net_flow_usdc_by_book[collateral] = net_usdc
            state.day_net_flow_native_by_book[collateral] = net_native
            state.last_flow_query_ms_by_book[collateral] = now_ms

        if summaries:
            self._heal_cash_flow_after_large_equity_move(
                state,
                summaries=summaries,
                orderbook_cache=orderbook_cache,
                now_ms=now_ms,
            )

    def _heal_cash_flow_after_large_equity_move(
        self,
        state: StrategyState,
        *,
        summaries: dict[str, AccountSummary],
        orderbook_cache: dict[str, OrderBookSnapshot],
        now_ms: int,
    ) -> None:
        """Re-fetch flows when equity moved a lot but transfer rows were likely truncated."""
        total_equity = self._total_equity_usdc(summaries, orderbook_cache)
        if state.day_start_equity_by_book:
            day_start = sum(state.day_start_equity_by_book.values(), Decimal("0"))
        else:
            day_start = state.day_start_equity_usdc
        equity_delta = total_equity - day_start
        if abs(equity_delta) < Decimal("200"):
            return
        flow_usdc = sum(state.day_net_flow_usdc_by_book.values(), Decimal("0"))
        if abs(flow_usdc) >= abs(equity_delta) * Decimal("0.35"):
            return
        LOGGER.info(
            "cash_flow_heal: equity_delta=%s flow_usdc=%s — re-fetching paginated transaction log",
            equity_delta,
            flow_usdc,
        )
        for collateral_raw in self.config.traded_collaterals:
            collateral = collateral_raw.upper()
            flow_start_ms = self._flow_query_start_ms(state, collateral)
            if flow_start_ms <= 0:
                continue
            try:
                net_native = sum_external_flow_native_in_window(
                    self.client,
                    currency=collateral,
                    start_timestamp_ms=flow_start_ms,
                    end_timestamp_ms=now_ms,
                )
            except Exception as exc:
                LOGGER.warning("cash_flow_heal_failed currency=%s err=%s", collateral, exc)
                continue
            if collateral in ("USDC", "USDT"):
                net_usdc = net_native
            else:
                index_price = self._currency_index_price(collateral, orderbook_cache)
                net_usdc = net_native * index_price
            state.day_net_flow_usdc_by_book[collateral] = net_usdc
            state.day_net_flow_native_by_book[collateral] = net_native
            state.last_flow_query_ms_by_book[collateral] = now_ms

    def _reset_daily_state(self, state: StrategyState, summaries: dict[str, AccountSummary]) -> StrategyState:
        today_key = utc_now().strftime("%Y-%m-%d")
        total_equity = self._total_equity_usdc(summaries, {})
        per_book = self._book_equities_usdc(summaries, {})
        per_book_native = self._book_equities_native(summaries)
        now_ms = utc_now_ms()
        if state.day_key != today_key:
            state.day_key = today_key
            state.day_start_equity_usdc = total_equity
            state.day_start_equity_by_book = dict(per_book)
            state.day_start_equity_native_by_book = dict(per_book_native)
            # New UTC day → flow tallies reset to zero and query timestamps
            # cleared so the next cycle re-queries from the fresh day-start.
            state.day_net_flow_usdc_by_book = {book: Decimal("0") for book in per_book}
            state.day_net_flow_native_by_book = {book: Decimal("0") for book in per_book_native}
            state.last_flow_query_ms_by_book = {}
            state.day_equity_anchor_ms_by_book = {book: now_ms for book in per_book}
        else:
            # First run after schema upgrade: backfill any missing per-book entry
            # from the current equity so we don't treat "unset" as "zero drop".
            for book, equity in per_book.items():
                if book not in state.day_start_equity_by_book:
                    state.day_start_equity_by_book[book] = equity
                    state.day_net_flow_usdc_by_book.setdefault(book, Decimal("0"))
                    state.day_equity_anchor_ms_by_book[book] = now_ms
                else:
                    state.day_net_flow_usdc_by_book.setdefault(book, Decimal("0"))
                    self._heal_legacy_flow_double_count(
                        state,
                        book=book,
                        equity_native=per_book_native.get(book, Decimal("0")),
                        now_ms=now_ms,
                    )
            for book, equity in per_book_native.items():
                native_start = state.day_start_equity_by_book.get(book, equity) if book in ("USDC", "USDT") else equity
                if book not in state.day_start_equity_native_by_book:
                    state.day_start_equity_native_by_book[book] = native_start
                    state.day_net_flow_native_by_book.setdefault(book, Decimal("0"))
                    state.day_equity_anchor_ms_by_book.setdefault(book, now_ms)
                else:
                    state.day_net_flow_native_by_book.setdefault(book, Decimal("0"))
        state.last_equity_usdc = total_equity
        state.last_equity_by_book = dict(per_book)
        state.last_equity_native_by_book = dict(per_book_native)
        # Drop expired per-book cooldowns so the dict doesn't grow unbounded.
        state.cooldown_until_ms_by_book = {
            book: ts for book, ts in state.cooldown_until_ms_by_book.items() if ts and ts > now_ms
        }
        return state

    def _sync_naked_open_groups_from_positions(self, state: StrategyState, option_positions: list[Position]) -> None:
        """Align naked short group quantity with exchange; scale entry_credit / max_loss proportionally (manual scale-in)."""
        open_naked = [g for g in state.groups if g.status == "open"]
        short_counts = Counter(g.short_instrument_name for g in open_naked)
        dupes = {name for name, n in short_counts.items() if n > 1}
        if dupes:
            LOGGER.warning("naked qty sync skipped: multiple open groups share short leg %s", sorted(dupes))

        sell_qty_by_name: dict[str, Decimal] = {}
        for p in option_positions:
            q = self._short_option_open_size(p)
            if q is not None:
                sell_qty_by_name[p.instrument_name] = q

        for group in open_naked:
            if group.short_instrument_name in dupes:
                continue
            ex_qty = sell_qty_by_name.get(group.short_instrument_name)
            if ex_qty is None:
                continue
            if ex_qty == group.quantity:
                if group.last_action and group.last_action.endswith("_incomplete"):
                    group.last_action = ""
                continue
            if group.quantity > 0:
                old_q = group.quantity
                group.quantity = ex_qty
                group.max_loss = group.max_loss * ex_qty / old_q
                group.estimated_im_collateral = group.estimated_im_collateral * ex_qty / old_q
                group.entry_credit = group.entry_credit * ex_qty / old_q
                group.original_entry_credit = group.original_entry_credit * ex_qty / old_q
                group.entry_fee = group.entry_fee * ex_qty / old_q
                LOGGER.info(
                    "naked group=%s quantity synced to exchange size=%s (was %s)",
                    group.group_id,
                    format_decimal(ex_qty, 8),
                    format_decimal(old_q, 8),
                )
            else:
                group.quantity = ex_qty
                LOGGER.warning("naked group=%s had quantity 0; set to exchange size=%s", group.group_id, ex_qty)
            group.last_action = "quantity_synced_exchange"

    def _find_bull_put_spread_long_match(
        self,
        *,
        short_instrument: OptionInstrument,
        short_quantity: Decimal,
        option_positions: list[Position],
        markets_by_currency: dict[str, list[OptionInstrument]],
        excluded_long_names: set[str],
    ) -> tuple[Position, OptionInstrument, Decimal] | None:
        if short_instrument.option_type != "put" or short_quantity <= 0:
            return None
        candidates: list[tuple[Decimal, Position, OptionInstrument, Decimal]] = []
        for position in option_positions:
            if position.instrument_name in excluded_long_names:
                continue
            long_quantity = self._long_option_open_size(position)
            if long_quantity is None or long_quantity < short_quantity:
                continue
            long_instrument = self._try_find_instrument(markets_by_currency, position.instrument_name)
            if long_instrument is None:
                continue
            if (
                long_instrument.option_type != "put"
                or long_instrument.base_currency != short_instrument.base_currency
                or long_instrument.quote_currency != short_instrument.quote_currency
                or long_instrument.settlement_currency != short_instrument.settlement_currency
                or long_instrument.expiration_timestamp_ms != short_instrument.expiration_timestamp_ms
                or long_instrument.strike >= short_instrument.strike
            ):
                continue
            quantity = align_option_order_amount(
                short_quantity,
                short_instrument.contract_size,
                short_instrument.min_trade_amount,
            )
            quantity = align_option_order_amount(
                quantity,
                long_instrument.contract_size,
                long_instrument.min_trade_amount,
            )
            if quantity <= 0 or long_quantity < quantity:
                continue
            candidates.append((short_instrument.strike - long_instrument.strike, position, long_instrument, quantity))
        if not candidates:
            return None
        _, position, long_instrument, quantity = min(candidates, key=lambda item: item[0])
        return position, long_instrument, quantity

    def _bull_put_spread_adoption_metrics(
        self,
        *,
        short_position: Position,
        short_instrument: OptionInstrument,
        long_position: Position,
        long_instrument: OptionInstrument,
        quantity: Decimal,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> dict[str, Decimal]:
        short_book = self._get_orderbook(short_instrument.instrument_name, orderbook_cache)
        long_book = self._get_orderbook(long_instrument.instrument_name, orderbook_cache)
        idx = short_book.index_price if short_book.index_price > 0 else long_book.index_price
        short_premium = (
            abs(short_position.average_price) if short_position.average_price != 0 else short_book.mark_price
        )
        long_premium = abs(long_position.average_price) if long_position.average_price != 0 else long_book.mark_price
        short_credit = self._premium_value_usdc(
            premium=short_premium,
            quantity=quantity,
            index_price=idx,
            instrument=short_instrument,
        )
        long_debit = self._premium_value_usdc(
            premium=long_premium,
            quantity=quantity,
            index_price=idx,
            instrument=long_instrument,
        )
        short_fee = self._option_fee_usdc(
            premium=short_premium,
            quantity=quantity,
            index_price=idx,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        long_fee = self._option_fee_usdc(
            premium=long_premium,
            quantity=quantity,
            index_price=idx,
            base_currency=long_instrument.base_currency,
            quote_currency=long_instrument.quote_currency,
            settlement_currency=long_instrument.settlement_currency,
        )
        entry_fee = short_fee + long_fee
        net_credit = short_credit - long_debit - entry_fee
        width_usdc = max(short_instrument.strike - long_instrument.strike, Decimal("0")) * quantity
        max_loss_usdc = max(width_usdc - net_credit, Decimal("0"))
        collateral = (
            "USDC"
            if short_instrument.quote_currency.upper() == "USDC"
            and short_instrument.settlement_currency.upper() == "USDC"
            else short_instrument.base_currency
        )
        estimated_im_collateral = (
            max_loss_usdc if collateral == "USDC" else (max_loss_usdc / idx if idx > 0 else Decimal("0"))
        )
        return {
            "entry_credit": net_credit,
            "entry_fee": entry_fee,
            "max_loss": max_loss_usdc,
            "estimated_im_collateral": estimated_im_collateral,
        }

    def _promote_group_to_bull_put_spread(
        self,
        group: TradeGroup,
        *,
        short_position: Position,
        short_instrument: OptionInstrument,
        long_position: Position,
        long_instrument: OptionInstrument,
        quantity: Decimal,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> None:
        metrics = self._bull_put_spread_adoption_metrics(
            short_position=short_position,
            short_instrument=short_instrument,
            long_position=long_position,
            long_instrument=long_instrument,
            quantity=quantity,
            orderbook_cache=orderbook_cache,
        )
        labels = self._spread_labels(short_instrument.base_currency, group.group_id)
        group.strategy = "bull_put_spread"
        group.option_type = "put"
        group.quantity = quantity
        group.long_instrument_name = long_instrument.instrument_name
        group.long_strike = long_instrument.strike
        group.long_label = group.long_label or labels["long"]
        group.short_label = group.short_label or labels["short"]
        group.entry_credit = metrics["entry_credit"]
        group.original_entry_credit = metrics["entry_credit"]
        group.entry_fee = metrics["entry_fee"]
        group.max_loss = metrics["max_loss"]
        group.estimated_im_collateral = metrics["estimated_im_collateral"]
        group.last_action = "adopted_bull_put_spread_from_exchange"

    def _sync_bull_put_spread_groups_from_positions(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> None:
        if self.config.option_strategy != "bull_put_spread":
            return
        short_positions = {
            p.instrument_name: p for p in option_positions if self._short_option_open_size(p) is not None
        }
        used_long_names = {g.long_instrument_name for g in self._open_groups(state) if g.long_instrument_name}
        for group in self._open_groups(state):
            if group.option_type != "put" or group.long_instrument_name:
                continue
            short_position = short_positions.get(group.short_instrument_name)
            if short_position is None:
                continue
            short_instrument = self._try_find_instrument(markets_by_currency, group.short_instrument_name)
            if short_instrument is None:
                continue
            short_quantity = self._short_option_open_size(short_position) or group.quantity
            match = self._find_bull_put_spread_long_match(
                short_instrument=short_instrument,
                short_quantity=short_quantity,
                option_positions=option_positions,
                markets_by_currency=markets_by_currency,
                excluded_long_names=used_long_names,
            )
            if match is None:
                continue
            long_position, long_instrument, quantity = match
            self._promote_group_to_bull_put_spread(
                group,
                short_position=short_position,
                short_instrument=short_instrument,
                long_position=long_position,
                long_instrument=long_instrument,
                quantity=quantity,
                orderbook_cache=orderbook_cache,
            )
            used_long_names.add(long_instrument.instrument_name)

    def _demote_unwound_bull_put_groups(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
    ) -> None:
        """When the long protection leg is gone but the short remains, treat as naked.

        Manual \"convert spread to naked\" (close long only) leaves the short on
        this sub-account; keeping ``bull_put_spread`` would mis-label closes and
        break reconcile PnL when the short later expires or moves.
        """
        if self.config.option_strategy != "bull_put_spread":
            return
        open_long_names = {p.instrument_name for p in option_positions if self._long_option_open_size(p) is not None}
        for group in self._open_groups(state):
            if not group.long_instrument_name:
                continue
            if group.long_instrument_name in open_long_names:
                continue
            if group_uses_spread_settlement_pricing(group):
                LOGGER.debug(
                    "skip demote spread→naked group=%s (expiry settlement window)",
                    group.group_id,
                )
                continue
            short_still_open = any(
                p.instrument_name == group.short_instrument_name and self._short_option_open_size(p) is not None
                for p in option_positions
            )
            if not short_still_open:
                continue
            LOGGER.info(
                "demote spread→naked group=%s short=%s (long %s no longer on book)",
                group.group_id,
                group.short_instrument_name,
                group.long_instrument_name,
            )
            group.strategy = "naked_short"
            group.long_instrument_name = ""
            group.long_strike = Decimal("0")
            group.long_label = ""
            group.long_entry_average_price = Decimal("0")
            group.last_action = "demoted_to_naked_after_long_unwound"

    def _adopt_untracked_naked_short_positions(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> None:
        """Create ``TradeGroup`` rows for naked short options on the exchange that are missing from state.

        Without this, positions opened outside the bot (or after state loss) never appear in
        ``open_groups``, so ``manage`` / early-exit / TP logic never runs on them.
        """
        if not self.config.enable_adopt_exchange_positions:
            return
        tracked_open = {g.short_instrument_name for g in state.groups if g.status == "open"}
        tracked_longs = {g.long_instrument_name for g in state.groups if g.status == "open" and g.long_instrument_name}

        for position in option_positions:
            open_sz = self._short_option_open_size(position)
            if open_sz is None:
                continue
            name = position.instrument_name
            if name in tracked_open:
                continue
            if name.endswith("-P"):
                if not self.config.enable_short_put:
                    continue
                option_type = "put"
            elif name.endswith("-C"):
                if not self.config.enable_short_call:
                    continue
                option_type = "call"
            else:
                continue

            try:
                inst = self._find_or_fetch_instrument(markets_by_currency, name)
            except KeyError:
                LOGGER.warning("adopt skipped: missing instrument metadata for %s", name)
                continue

            try:
                book = self._get_orderbook(name, orderbook_cache)
            except Exception as exc:
                LOGGER.warning("adopt skipped: no orderbook for %s (%s)", name, exc)
                continue

            idx = book.index_price
            if idx <= 0:
                continue
            mark = (
                book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
            )
            if mark <= 0:
                continue

            qty = align_option_order_amount(open_sz, inst.contract_size, inst.min_trade_amount)
            if qty <= 0:
                continue

            premium = abs(position.average_price) if position.average_price != 0 else mark
            entry_fee = self._option_fee_usdc(
                premium=premium,
                quantity=qty,
                index_price=idx,
                base_currency=inst.base_currency,
                quote_currency=inst.quote_currency,
                settlement_currency=inst.settlement_currency,
            )
            gross = self._premium_value_usdc(
                premium=premium,
                quantity=qty,
                index_price=idx,
                instrument=inst,
            )
            net_credit = gross - entry_fee

            usdc_linear = inst.quote_currency.upper() == "USDC" and inst.settlement_currency.upper() == "USDC"
            collateral = "USDC" if usdc_linear else inst.base_currency

            if usdc_linear:
                if option_type == "call":
                    im_1 = linear_usdc_short_call_initial_per_contract_usdc(
                        index_price=idx,
                        strike=inst.strike,
                        mark_usdc=mark,
                        contract_size=inst.contract_size,
                    )
                else:
                    im_1 = linear_usdc_short_put_initial_per_contract_usdc(
                        index_price=idx,
                        strike=inst.strike,
                        mark_usdc=mark,
                        contract_size=inst.contract_size,
                    )
                estimated_im_collateral = im_1 * qty  # USDC-native
                max_loss_usdc = estimated_im_collateral
            else:
                if option_type == "call":
                    im_u = short_call_initial_unit(index_price=idx, strike=inst.strike, mark_price=mark)
                else:
                    im_u = short_put_initial_unit(index_price=idx, strike=inst.strike, mark_price=mark)
                estimated_im_collateral = im_u * qty  # BTC/ETH-native
                max_loss_usdc = estimated_im_collateral * idx if idx > 0 else estimated_im_collateral

            group_id = self._next_group_id(state)
            labels = self._spread_labels(inst.base_currency, group_id)
            spread_match = (
                self._find_bull_put_spread_long_match(
                    short_instrument=inst,
                    short_quantity=qty,
                    option_positions=option_positions,
                    markets_by_currency=markets_by_currency,
                    excluded_long_names=tracked_longs,
                )
                if self.config.option_strategy == "bull_put_spread" and option_type == "put"
                else None
            )
            strategy = (
                "bull_put_spread"
                if spread_match is not None
                else (
                    "covered_call"
                    if self.config.option_strategy == "covered_call"
                    and option_type == "call"
                    and collateral == inst.base_currency
                    else "naked_short"
                )
            )
            group = TradeGroup(
                group_id=group_id,
                currency=inst.base_currency,
                collateral_currency=collateral,
                quantity=qty,
                entry_timestamp_ms=utc_now_ms(),
                expiration_timestamp_ms=inst.expiration_timestamp_ms,
                short_instrument_name=name,
                short_strike=inst.strike,
                entry_credit=net_credit,
                original_entry_credit=net_credit,
                max_loss=max_loss_usdc,
                estimated_im_collateral=estimated_im_collateral,
                regime_at_entry=RiskRegime.NORMAL.value,
                entry_fee=entry_fee,
                short_entry_average_price=premium,
                entry_index_usd=idx,
                short_label=labels["short"],
                option_type=option_type,
                strategy=strategy,
                covered_underlying_quantity=qty if strategy == "covered_call" else Decimal("0"),
            )
            if spread_match is not None:
                long_position, long_instrument, spread_quantity = spread_match
                self._promote_group_to_bull_put_spread(
                    group,
                    short_position=position,
                    short_instrument=inst,
                    long_position=long_position,
                    long_instrument=long_instrument,
                    quantity=spread_quantity,
                    orderbook_cache=orderbook_cache,
                )
                tracked_longs.add(long_instrument.instrument_name)
            group.last_action = "adopted_from_exchange"
            self._attach_open_group_stats(group)
            state.groups.append(group)
            tracked_open.add(name)
            LOGGER.info(
                "adopted %s %s from exchange: group=%s qty=%s entry_credit_usdc=%s max_loss_usdc=%s",
                strategy,
                name,
                group_id,
                format_decimal(qty, 8),
                format_decimal(net_credit, 8),
                format_decimal(max_loss_usdc, 8),
            )

    def _reconcile_state(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> StrategyState:
        self._sync_naked_open_groups_from_positions(state, option_positions)
        self._sync_bull_put_spread_groups_from_positions(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
        )
        self._demote_unwound_bull_put_groups(state, option_positions=option_positions)
        self._adopt_untracked_naked_short_positions(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
        )
        open_short_option_names = {
            p.instrument_name for p in option_positions if self._short_option_open_size(p) is not None
        }
        for group in state.groups:
            if group.status != "open":
                continue
            still_open = group.short_instrument_name in open_short_option_names
            if still_open:
                continue
            age_ms = utc_now_ms() - int(group.entry_timestamp_ms or 0)
            if age_ms < RECONCILE_EXTERNAL_CLOSE_GRACE_MS:
                LOGGER.debug(
                    "reconcile defer external close group=%s instrument=%s (age_ms=%s)",
                    group.group_id,
                    group.short_instrument_name,
                    age_ms,
                )
                continue
            expired = self._group_is_expired(group)
            closed_timestamp_ms = (
                max(int(group.expiration_timestamp_ms or 0), int(group.entry_timestamp_ms or 0))
                if expired and group.expiration_timestamp_ms > 0
                else utc_now_ms()
            )
            estimated_close_debit = self._estimate_reconcile_close_debit(
                group,
                orderbook_cache,
                markets_by_currency=markets_by_currency,
            )
            realized_pnl: Decimal | None = None
            realized_return_on_max_loss: Decimal | None = None
            if estimated_close_debit is not None:
                realized_pnl = group.entry_credit_net_usdc() - estimated_close_debit
                realized_return_on_max_loss = safe_div(realized_pnl, group.max_loss)
            else:
                realized_pnl = None
                realized_return_on_max_loss = None
            close_index_usd: Decimal | None = None
            try:
                short_book = self._get_orderbook(group.short_instrument_name, orderbook_cache)
                if short_book.index_price > 0:
                    close_index_usd = short_book.index_price
                    group.close_index_usd = close_index_usd
            except Exception:
                close_index_usd = None
            if (
                self.config.covered_call_spot_exit_enabled
                and not self.config.covered_call_robust_exit_enabled
                and self._is_covered_call_group(group)
                and group.spot_exit_status not in {"submitted", "filled", "pending"}
                and self._covered_call_itm_from_cache(group, orderbook_cache)
            ):
                group.spot_exit_status = "pending"
                group.spot_exit_amount = (
                    group.covered_underlying_quantity if group.covered_underlying_quantity > 0 else group.quantity
                )
                group.spot_exit_instrument_name = self._covered_call_spot_instrument(group.currency)
                group.spot_exit_reason = "covered_call_settlement_exit"
            group.backfill_realized_pnl_collateral_native()
            group.backfill_realized_pnl_usdc()
            self._mark_group_closed(
                group,
                reason="reconciled_expiry" if expired else "reconciled_external",
                closed_timestamp_ms=closed_timestamp_ms,
                realized_close_debit=estimated_close_debit,
                realized_pnl=group.realized_pnl or realized_pnl,
                realized_return_on_max_loss=realized_return_on_max_loss,
                index_price_usd=close_index_usd,
            )
            self._journal_reconcile_close(group, closed_timestamp_ms=closed_timestamp_ms)
            if realized_pnl is not None:
                LOGGER.info("reconcile group=%s estimated_pnl=%s", group.group_id, realized_pnl)
            else:
                LOGGER.warning("reconcile group=%s could not estimate PnL", group.group_id)
        return state

    def _group_is_expired(self, group: TradeGroup, *, now_ms: int | None = None) -> bool:
        exp = int(group.expiration_timestamp_ms or 0)
        if exp <= 0:
            return False
        now = utc_now_ms() if now_ms is None else now_ms
        return now >= exp - 60_000

    def _is_bull_put_spread_group(self, group: TradeGroup) -> bool:
        return is_bull_put_spread_group(group, default_strategy=self.config.option_strategy)

    def _resolve_reconcile_index_price(
        self,
        group: TradeGroup,
        *,
        short_book: OrderBookSnapshot | None,
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets: dict[str, list[OptionInstrument]],
    ) -> Decimal:
        if short_book is not None and short_book.index_price > 0:
            return short_book.index_price
        if group.close_index_usd and group.close_index_usd > 0:
            return group.close_index_usd
        if group.entry_index_usd > 0:
            return group.entry_index_usd
        try:
            short_instrument = self._find_or_fetch_instrument(markets, group.short_instrument_name)
            return self._currency_index_price(short_instrument.base_currency, orderbook_cache)
        except Exception:
            return Decimal("0")

    def _naked_reconcile_close_debit_at_index(
        self,
        group: TradeGroup,
        *,
        short_instrument: OptionInstrument,
        index_price: Decimal,
    ) -> Decimal:
        option_type = (group.option_type or "put").lower()
        settle = _intrinsic_settlement(short_instrument, shocked_spot=index_price, option_type=option_type)
        close_debit = self._premium_value_usdc(
            premium=settle,
            quantity=group.quantity,
            index_price=index_price,
            instrument=short_instrument,
        )
        close_debit += self._option_fee_usdc(
            premium=settle,
            quantity=group.quantity,
            index_price=index_price,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        return max(close_debit, Decimal("0"))

    def _cap_spread_reconcile_close_debit(self, group: TradeGroup, close_debit: Decimal) -> Decimal:
        """Bull put max loss is bounded; reconcile must not book naked-style assignment."""
        if group.max_loss <= 0:
            return close_debit
        ceiling = group.entry_credit_net_usdc() + group.max_loss
        return min(close_debit, ceiling)

    def _long_instrument_for_spread_reconcile(
        self,
        group: TradeGroup,
        markets: dict[str, list[OptionInstrument]],
        *,
        short_instrument: OptionInstrument,
    ) -> OptionInstrument | None:
        try:
            if group.long_instrument_name:
                return self._find_or_fetch_instrument(markets, group.long_instrument_name)
        except Exception:
            pass
        return long_instrument_for_spread_reconcile(group, short_instrument, markets)

    def _spread_reconcile_close_debit_at_index(
        self,
        group: TradeGroup,
        *,
        short_instrument: OptionInstrument,
        long_instrument: OptionInstrument | None,
        index_price: Decimal,
    ) -> Decimal:
        return spread_expiry_close_debit_usdc(
            group,
            short_instrument=short_instrument,
            long_instrument=long_instrument,
            index_price=index_price,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
        )

    def _estimate_reconcile_close_debit(
        self,
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
        *,
        markets_by_currency: dict[str, list[OptionInstrument]] | None = None,
    ) -> Decimal | None:
        markets = markets_by_currency or {}
        is_spread = self._is_bull_put_spread_group(group)
        spread_settlement = is_spread and group_uses_spread_settlement_pricing(group)
        if group.current_debit > 0 and not spread_settlement:
            debit = group.current_debit
            if is_spread:
                return self._cap_spread_reconcile_close_debit(group, debit)
            return debit
        try:
            short_book = self._get_orderbook(group.short_instrument_name, orderbook_cache)
            short_instrument = self._find_or_fetch_instrument(markets, group.short_instrument_name)
            index_price = short_book.index_price
            if index_price <= 0:
                index_price = group.close_index_usd or group.entry_index_usd or Decimal("0")
            expired = self._group_is_expired(group)
            if spread_settlement and index_price > 0:
                long_instrument = self._long_instrument_for_spread_reconcile(
                    group, markets, short_instrument=short_instrument
                )
                return self._spread_reconcile_close_debit_at_index(
                    group,
                    short_instrument=short_instrument,
                    long_instrument=long_instrument,
                    index_price=index_price,
                )
            if expired and index_price > 0:
                return self._naked_reconcile_close_debit_at_index(
                    group,
                    short_instrument=short_instrument,
                    index_price=index_price,
                )
            mark_premium = short_book.mark_price if short_book.mark_price > 0 else short_book.best_ask_price
            mark_debit = self._premium_value_usdc(
                premium=max(mark_premium, Decimal("0")),
                quantity=group.quantity,
                index_price=short_book.index_price,
                instrument=short_instrument,
            )
            close_fee = self._option_fee_usdc(
                premium=max(mark_premium, Decimal("0")),
                quantity=group.quantity,
                index_price=short_book.index_price,
                base_currency=short_instrument.base_currency,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
            )
            mark_debit += close_fee
            if is_spread and group.long_strike > 0:
                long_instrument = self._long_instrument_for_spread_reconcile(
                    group, markets, short_instrument=short_instrument
                )
                long_premium = Decimal("0")
                long_index = index_price
                if group.long_instrument_name:
                    try:
                        long_book = self._get_orderbook(group.long_instrument_name, orderbook_cache)
                        long_index = long_book.index_price if long_book.index_price > 0 else index_price
                        if long_instrument is not None:
                            long_premium = (
                                long_book.best_bid_price if long_book.best_bid_price > 0 else long_book.mark_price
                            )
                    except Exception:
                        if spread_settlement and index_price > 0 and long_instrument is not None:
                            return self._spread_reconcile_close_debit_at_index(
                                group,
                                short_instrument=short_instrument,
                                long_instrument=long_instrument,
                                index_price=index_price,
                            )
                if long_instrument is not None and long_premium > 0:
                    long_credit = self._premium_value_usdc(
                        premium=long_premium,
                        quantity=group.quantity,
                        index_price=long_index,
                        instrument=long_instrument,
                    )
                    long_fee = self._option_fee_usdc(
                        premium=long_premium,
                        quantity=group.quantity,
                        index_price=long_index,
                        base_currency=long_instrument.base_currency,
                        quote_currency=long_instrument.quote_currency,
                        settlement_currency=long_instrument.settlement_currency,
                    )
                    mark_debit = max(mark_debit - max(long_credit - long_fee, Decimal("0")), Decimal("0"))
                elif spread_settlement and index_price > 0 and long_instrument is not None:
                    return self._spread_reconcile_close_debit_at_index(
                        group,
                        short_instrument=short_instrument,
                        long_instrument=long_instrument,
                        index_price=index_price,
                    )
            if is_spread:
                return self._cap_spread_reconcile_close_debit(group, max(mark_debit, Decimal("0")))
            return max(mark_debit, Decimal("0"))
        except Exception:
            expired = self._group_is_expired(group) or (
                is_spread and in_spread_expiry_settlement_window(int(group.expiration_timestamp_ms or 0))
            )
            if expired:
                idx = self._resolve_reconcile_index_price(
                    group,
                    short_book=None,
                    orderbook_cache=orderbook_cache,
                    markets=markets,
                )
                if idx > 0:
                    try:
                        short_instrument = self._find_or_fetch_instrument(markets, group.short_instrument_name)
                        if is_spread:
                            long_instrument = self._long_instrument_for_spread_reconcile(
                                group, markets, short_instrument=short_instrument
                            )
                            return self._spread_reconcile_close_debit_at_index(
                                group,
                                short_instrument=short_instrument,
                                long_instrument=long_instrument,
                                index_price=idx,
                            )
                        return self._naked_reconcile_close_debit_at_index(
                            group,
                            short_instrument=short_instrument,
                            index_price=idx,
                        )
                    except Exception:
                        pass
            if group.current_debit >= 0:
                debit = group.current_debit
                if is_spread:
                    return self._cap_spread_reconcile_close_debit(group, debit)
                return debit
            return None

    def _refresh_group(
        self,
        *,
        context_markets: dict[str, list[OptionInstrument]],
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> None:
        short_book = self._get_orderbook(group.short_instrument_name, orderbook_cache)
        short_instrument = self._find_or_fetch_instrument(context_markets, group.short_instrument_name)
        # Estimate close-cost premium. Prefer a tight best_ask, but ignore outlier
        # quotes that sit far from mark (stale/fat-finger orders) so loss_pct and
        # defense stops are not spuriously triggered.
        close_premium = short_book.buy_close_premium(max_spread_ratio=self.config.early_exit_max_spread_ratio)
        if close_premium <= 0:
            close_premium = max(short_book.best_ask_price, short_book.mark_price)
        if close_premium <= 0:
            close_premium = Decimal("0")
        group.current_debit = max(
            self._premium_value_usdc(
                premium=close_premium,
                quantity=group.quantity,
                index_price=short_book.index_price,
                instrument=short_instrument,
            ),
            Decimal("0"),
        )
        group.current_close_fee = self._option_fee_usdc(
            premium=close_premium,
            quantity=group.quantity,
            index_price=short_book.index_price,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        group.current_debit += group.current_close_fee
        if group.long_instrument_name:
            try:
                long_book = self._get_orderbook(group.long_instrument_name, orderbook_cache)
                long_instrument = self._find_or_fetch_instrument(context_markets, group.long_instrument_name)
                long_close_premium = long_book.sell_close_premium(
                    max_spread_ratio=self.config.early_exit_max_spread_ratio
                )
                if long_close_premium <= 0:
                    long_close_premium = (
                        long_book.best_bid_price if long_book.best_bid_price > 0 else long_book.mark_price
                    )
                long_credit = self._premium_value_usdc(
                    premium=max(long_close_premium, Decimal("0")),
                    quantity=group.quantity,
                    index_price=long_book.index_price,
                    instrument=long_instrument,
                )
                long_close_fee = self._option_fee_usdc(
                    premium=max(long_close_premium, Decimal("0")),
                    quantity=group.quantity,
                    index_price=long_book.index_price,
                    base_currency=long_instrument.base_currency,
                    quote_currency=long_instrument.quote_currency,
                    settlement_currency=long_instrument.settlement_currency,
                )
                group.current_debit = max(
                    group.current_debit - max(long_credit - long_close_fee, Decimal("0")), Decimal("0")
                )
                group.current_close_fee += long_close_fee
            except Exception as exc:
                LOGGER.warning(
                    "refresh_group %s: unable to refresh long leg %s (%s)",
                    group.group_id,
                    group.long_instrument_name,
                    exc,
                )
        group.profit_capture = safe_div(max(group.entry_credit - group.current_debit, Decimal("0")), group.entry_credit)
        group.short_delta = abs(short_book.delta)

    def _delta_totals_by_currency(
        self,
        summaries: dict[str, AccountSummary],
        state: StrategyState,
        future_positions: list[Position],
    ) -> dict[str, Decimal]:
        """Self-compute per-currency net delta from our own tracked legs + perp hedge.

        We intentionally do NOT use ``summary.delta_total`` (even as a fallback)
        because Deribit includes hedge perps from every strategy/subaccount that
        shares credentials, which can blow up the hedge plan. The matching
        ``summary.delta_total`` monitoring value is exposed via the account
        summaries for dashboards but hedging decisions run off this value.

        Formula per open group:
            group_delta = option_sign * abs(greek_delta) * quantity

        where ``option_sign`` is +1 for a short put (short puts are long-delta)
        and -1 for a short call (short calls are short-delta). Stage B introduces
        ``option_type`` on TradeGroup; until then all groups are short puts.
        """
        _ = summaries  # retained for signature parity / future parity checks
        totals: dict[str, Decimal] = {}
        for currency in self.config.managed_currencies:
            group_delta = Decimal("0")
            for group in self._open_groups(state):
                if group.currency != currency:
                    continue
                option_type = getattr(group, "option_type", "put") or "put"
                option_sign = Decimal("-1") if option_type == "call" else Decimal("1")
                group_delta += option_sign * group.short_delta * group.quantity
            hedge_delta = sum(
                (
                    position.signed_size_currency
                    for position in future_positions
                    if position.instrument_name == self._perp_instrument(currency)
                ),
                Decimal("0"),
            )
            totals[currency] = group_delta + hedge_delta
        return totals

    def _current_hedge_base(self, positions: list[Position], currency: str) -> Decimal:
        instrument_name = self._perp_instrument(currency)
        return sum(
            (position.signed_size_currency for position in positions if position.instrument_name == instrument_name),
            Decimal("0"),
        )

    def _overall_regime(self, values: Any) -> RiskRegime:
        severity = {RiskRegime.NORMAL: 0, RiskRegime.ELEVATED: 1, RiskRegime.CRISIS: 2}
        worst = RiskRegime.NORMAL
        for value in values:
            if severity[value] > severity[worst]:
                worst = value
        return worst

    def _update_recovery_counts(self, state: StrategyState, regimes: dict[str, RiskRegime]) -> None:
        for currency, regime in regimes.items():
            if regime is RiskRegime.NORMAL:
                state.normal_recovery_counts[currency] = state.normal_recovery_counts.get(currency, 0) + 1
            else:
                state.normal_recovery_counts[currency] = 0
