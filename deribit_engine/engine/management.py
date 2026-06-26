from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..cross_book_flow import cross_book_flow_adjustments_native
from ..entry_gates import (
    append_underlying_regime_halt_reasons_for_usdc_book,
    build_halt_new_entries_by_currency,
)
from ..exceptions import TransientExchangeError
from ..exit_eval import (
    evaluate_defense_triggers,
    evaluate_early_exit_reason,
    exit_eval_context_from_config,
    income_exit_close_premium,
    take_profit_triggered,
)
from ..investor_cash_flow import cash_flow_scan_currencies, sum_external_flow_native_in_window
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
from ..utils import (
    format_decimal,
    safe_div,
    utc_now,
    utc_now_ms,
)
from .context import (
    _MAX_SCAN_BLOCKER_LOG_LINES,
    LOG_REASON_NUMBER_RE,
    LOGGER,
    ExchangePrefetch,
    RuntimeContext,
)


class ManagementMixin:
    def manage(self, *, live: bool = False, context: RuntimeContext | None = None) -> dict[str, Any]:
        if context is None:
            context = self._load_runtime(live=live)
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
            now_ms = utc_now_ms()
            newly_cooled: list[str] = []
            if live:
                for book in hard_books:
                    existing = context.state.cooldown_until_ms_by_book.get(book)
                    if not existing or existing <= now_ms:
                        context.state.cooldown_until_ms_by_book[book] = cooldown_until
                        newly_cooled.append(book)
                if global_trigger:
                    existing = context.state.cooldown_until_ms
                    if not existing or existing <= now_ms:
                        context.state.cooldown_until_ms = cooldown_until
                        newly_cooled.append("portfolio")
                if newly_cooled:
                    reasons = context.snapshot.halt_entry_reasons
                    self._telegram_alert(
                        "Hard derisk triggered",
                        body=f"books={hard_books or ['portfolio']}",
                        event_key=f"hard_derisk:{self._journal_scope_key()}",
                        level="critical",
                        extra={"reasons": "; ".join(reasons[:5]) if reasons else None},
                    )
            if newly_cooled or not live:
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

        self._clear_stale_drawdown_cooldowns(context.state, context.snapshot)

        if self.config.enable_perp_hedge:
            if self.config.per_position_hedge:
                # Per-position hedging: drive each currency's perp to the sum of
                # every open group's intended hedge. This subsumes orphan-close
                # (closed groups drop out of the sum) and unwind (recovered
                # groups shrink their target), so it runs every cycle.
                actions.extend(self._reconcile_position_hedges(context, live=live))
            else:
                # Legacy currency-net hedging. Flatten orphaned hedges (option leg
                # gone) every cycle regardless of regime, so we never sit on an
                # unintended naked perp.
                for currency in self.config.managed_currencies:
                    orphan = self._maybe_close_orphan_hedge(context, currency=currency, live=live)
                    if orphan is not None:
                        actions.append(orphan)
                if context.snapshot.regime is RiskRegime.NORMAL:
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
                context = self._load_runtime(live=live)
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
                enterable = self._filter_enterable_candidates(context, candidates)
                can_enter = bool(enterable)
                if can_enter:
                    cycle_result["entry"] = self._enter_best_from_candidates(
                        context, candidates=enterable[:1], live=live
                    )
                    if cycle_result["entry"].get("group") is not None:
                        context.state.groups.append(cycle_result["entry"]["group"])
                else:
                    cycle_result["entry"] = {
                        "action": "entry_skipped",
                        "reason": self._entry_skip_reason(portfolio, candidates=candidates),
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

    def _load_runtime(self, *, live: bool = False) -> RuntimeContext:
        if self.config.has_private_credentials:
            context, _ = self._load_runtime_from_exchange(self.fetch_exchange_prefetch(), live=live)
            return context
        context, _ = self._load_runtime_from_exchange(None, live=live)
        return context

    def _load_runtime_from_exchange(
        self,
        prefetch: ExchangePrefetch | None,
        *,
        dashboard_display: bool = False,
        live: bool = False,
    ) -> tuple[RuntimeContext, bool]:
        state = self.state_store.load()
        if self._repair_reconciled_bot_income_exits_in_state(state) and not dashboard_display:
            self.state_store.save(state)
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
        state, reconcile_closed = self._reconcile_state(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
            live=live,
        )
        regime_by_currency: dict[str, RiskRegime] = {}
        regime_detail_by_currency: dict[str, tuple[str, ...]] = {}
        for currency in self.config.managed_currencies:
            markets = markets_by_currency.get(currency) or []
            # Use the same regime path for dashboard and live when markets are loaded
            # so ETH elevated on the USDC book (ETH-USDC linear) matches bot entry gates.
            if markets:
                regime, detail = self._determine_regime_with_detail(
                    currency,
                    markets=markets,
                    orderbook_cache=orderbook_cache,
                )
            elif dashboard_display:
                regime, detail = self._determine_regime_for_dashboard(currency)
            else:
                regime, detail = self._determine_regime_with_detail(
                    currency,
                    markets=markets,
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
            markets_by_currency=markets_by_currency,
        )
        if not dashboard_display:
            self._prefetch_scan_book_summaries(markets_by_currency, orderbook_cache)
        return (
            RuntimeContext(
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
            ),
            reconcile_closed,
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

    def _entry_skip_reason(self, portfolio: dict[str, Any], *, candidates: list[Any]) -> str:
        if portfolio.get("portfolio_wide_entry_halt"):
            if portfolio.get("cooling_down"):
                return "cooling_down"
            return "halt_limit_reached"
        if portfolio.get("hard_derisk"):
            return "hard_derisk"
        halted_by_ccy = portfolio.get("halt_new_entries_by_currency") or {}
        if candidates:
            blocked = sorted(
                {
                    (getattr(c, "currency", None) or (c.get("currency") if isinstance(c, dict) else "") or "").upper()
                    for c in candidates
                }
                - {""}
            )
            if blocked and all(halted_by_ccy.get(ccy, True) for ccy in blocked):
                return "currency_regime_or_crisis_halt"
        if halted_by_ccy and all(halted_by_ccy.values()):
            return "all_currencies_halted"
        return "halt_limit_reached"

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
        if group.dte_days <= self.config.time_exit_dte:
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

    def _take_profit_triggered(self, context: RuntimeContext, group: TradeGroup) -> bool:
        close_debit = self._income_exit_close_debit(context, group)
        return take_profit_triggered(
            group,
            close_debit_usdc=close_debit,
            ctx=exit_eval_context_from_config(self.config),
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

    def _maybe_close_orphan_hedge(self, context: RuntimeContext, *, currency: str, live: bool) -> dict[str, Any] | None:
        """Flatten a perp hedge left behind once no option needs it.

        When the last open option group in a currency closes (time exit, TP,
        etc.) while a hedge is still on, the perp becomes a naked directional
        bet. Close it immediately, regardless of regime, so we are not left
        holding an unintended position until recovery unwinds it.
        """
        perp_name = self._perp_instrument(currency)
        if any(group.currency == currency for group in self._open_groups(context.state)):
            return None
        position = next(
            (pos for pos in context.future_positions if pos.instrument_name == perp_name and pos.size != 0),
            None,
        )
        if position is None:
            return None
        closed = self._close_perp_position(context, position, live=live)
        if closed is None:
            return None
        closed["reason"] = "orphan_hedge_no_open_options"
        closed["currency"] = currency
        return closed

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
        uses_base = self._perp_uses_base_amount(currency)
        if not uses_base and index_price <= 0:
            return None
        raw_amount = unwind_base if uses_base else unwind_base * index_price
        amount = self._align_future_order_amount(
            context,
            instrument_name=self._perp_instrument(currency),
            amount=raw_amount,
        )
        if amount <= 0:
            return None
        unwind_base = amount if uses_base else amount / index_price
        action = {
            "action": "hedge_unwind",
            "currency": currency,
            "instrument_name": self._perp_instrument(currency),
            "amount": format_decimal(amount, 8),
            "base_amount": format_decimal(unwind_base, 8),
            "live": live,
        }
        if live:
            response = self._place_hedge_perp_order(
                context,
                direction="buy",
                instrument_name=self._perp_instrument(currency),
                amount=amount,
                label=self._hedge_label(currency, "recovery"),
                reduce_only=True,
            )
            action["response"] = response
            if isinstance(response, dict) and response.get("skipped"):
                action["skipped"] = True
                action["skip_reason"] = response.get("reason")
        return action

    def _contract_size_for_group(
        self,
        group: TradeGroup,
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> Decimal:
        """Return the short leg's contract size in base (coin) units.

        ``TradeGroup.quantity`` is stored in Deribit contract count; linear
        USDC options use ``contract_size`` < 1 (e.g. 0.1 ETH per contract).
        """
        try:
            instrument = self._find_instrument_by_markets(markets_by_currency, group.short_instrument_name)
        except KeyError:
            return Decimal("1")
        return instrument.contract_size if instrument.contract_size > 0 else Decimal("1")

    def _group_option_delta(self, group: TradeGroup, *, contract_size: Decimal = Decimal("1")) -> Decimal:
        """Signed option delta of a single group in base (coin) units.

        +ve = long-delta (short put), -ve = short-delta (short call).
        """
        option_type = getattr(group, "option_type", "put") or "put"
        option_sign = Decimal("-1") if option_type == "call" else Decimal("1")
        underlying_base = group.quantity * contract_size
        return option_sign * group.short_delta * underlying_base

    def _update_position_hedge(
        self,
        group: TradeGroup,
        *,
        raw_soft: bool,
        raw_hard: bool,
        soft_trigger: bool,
        hard_trigger: bool,
        contract_size: Decimal = Decimal("1"),
    ) -> None:
        """Refresh a group's intended per-position perp hedge (signed base units).

        soft (confirmed) neutralizes ``soft_hedge_neutralize_pct`` of the group's
        own option delta; hard (confirmed) neutralizes 100%. While a hedge is
        active the target is recomputed from the live delta every cycle, so it
        scales down automatically as price recovers. Once the raw defense
        trigger has stayed clear for ``recovery_normal_cycles`` cycles the hedge
        is fully removed. The actual perp order is placed once per currency in
        ``_reconcile_position_hedges``.
        """
        soft_frac = self.config.soft_hedge_neutralize_pct
        if hard_trigger:
            group.hedge_mode = "hard"
            group.hedge_recovery_streak = 0
            frac = Decimal("1")
        elif soft_trigger:
            # Never downgrade an already-on hard hedge to soft on a fresh soft.
            if group.hedge_mode != "hard":
                group.hedge_mode = "soft"
            group.hedge_recovery_streak = 0
            frac = Decimal("1") if group.hedge_mode == "hard" else soft_frac
        elif group.hedge_mode:
            # Active hedge, no confirmed trigger this cycle.
            if raw_soft or raw_hard:
                # Still elevated (just not confirmed) -> hold, do not start unwind.
                group.hedge_recovery_streak = 0
            else:
                group.hedge_recovery_streak += 1
                if group.hedge_recovery_streak >= max(self.config.hedge_unwind_recovery_cycles, 1):
                    group.hedge_mode = ""
                    group.hedge_recovery_streak = 0
                    group.hedge_size_base = Decimal("0")
                    return
            frac = Decimal("1") if group.hedge_mode == "hard" else soft_frac
        else:
            group.hedge_size_base = Decimal("0")
            return
        # The perp offsets the option delta, hence the opposite sign.
        group.hedge_size_base = -self._group_option_delta(group, contract_size=contract_size) * frac

    def _max_abs_per_position_hedge_base(
        self,
        context: RuntimeContext,
        *,
        currency: str,
        open_groups: list[TradeGroup],
    ) -> Decimal:
        """Upper bound on |perp hedge| from tracked option delta (base coin units)."""
        markets = getattr(context, "markets_by_currency", None) or {}
        total = Decimal("0")
        for group in open_groups:
            if group.currency != currency:
                continue
            contract_size = self._contract_size_for_group(group, markets)
            total += abs(self._group_option_delta(group, contract_size=contract_size))
        return total

    def _cap_per_position_hedge_target(
        self,
        context: RuntimeContext,
        *,
        currency: str,
        target_base: Decimal,
        open_groups: list[TradeGroup],
    ) -> tuple[Decimal, bool]:
        """Clamp a reconcile target so a sizing bug cannot open a naked perp leg."""
        max_abs = self._max_abs_per_position_hedge_base(context, currency=currency, open_groups=open_groups)
        if max_abs <= 0:
            return target_base, False
        ceiling = max_abs * Decimal("1.1")
        if abs(target_base) <= ceiling:
            return target_base, False
        capped = ceiling if target_base > 0 else -ceiling
        LOGGER.error(
            "hedge reconcile capped for %s: target_base=%s exceeds 110%% of option_delta_cap=%s",
            currency,
            format_decimal(target_base, 8),
            format_decimal(max_abs, 8),
        )
        return capped, True

    def _reconcile_position_hedges(self, context: RuntimeContext, *, live: bool) -> list[dict[str, Any]]:
        """Drive each currency's perp to the sum of every open group's intended hedge.

        Under per-position hedging the exchange still holds a single perp per
        currency, so we net every group's ``hedge_size_base`` and place one
        market order to close the gap. Closed groups drop out of the sum, which
        also flattens orphaned hedges and unwinds recovered positions without a
        separate code path.
        """
        actions: list[dict[str, Any]] = []
        open_groups = self._open_groups(context.state)
        for currency in self.config.managed_currencies:
            target_base = sum(
                (group.hedge_size_base for group in open_groups if group.currency == currency),
                Decimal("0"),
            )
            raw_target_base = target_base
            target_base, hedge_capped = self._cap_per_position_hedge_target(
                context,
                currency=currency,
                target_base=target_base,
                open_groups=open_groups,
            )
            current_base = self._current_hedge_base(context.future_positions, currency)
            diff = target_base - current_base
            deadband = self.config.hedge_reconcile_deadband_base(currency)
            if abs(diff) <= deadband:
                continue
            perp_name = self._perp_instrument(currency)
            uses_base = self._perp_uses_base_amount(currency)
            index_price = self._currency_index_price(currency, context.orderbook_cache)
            if not uses_base and index_price <= 0:
                continue
            raw_amount = abs(diff) if uses_base else abs(diff) * index_price
            order_amount = self._align_future_order_amount(
                context,
                instrument_name=perp_name,
                amount=raw_amount,
            )
            if order_amount <= 0:
                continue
            realized_base = order_amount if uses_base else order_amount / index_price
            side = "buy" if diff > 0 else "sell"
            signed_change = realized_base if side == "buy" else -realized_base
            new_base = current_base + signed_change
            # reduce_only only when shrinking magnitude without flipping sign,
            # so we never block a genuine open and never overshoot into a flip.
            reduce_only = abs(target_base) < abs(current_base) and current_base * target_base >= 0
            action = {
                "action": "hedge_position_reconcile",
                "currency": currency,
                "instrument_name": perp_name,
                "side": side,
                "amount": format_decimal(order_amount, 8),
                "target_hedge_base": format_decimal(target_base, 8),
                "current_hedge_base": format_decimal(current_base, 8),
                "new_hedge_base": format_decimal(new_base, 8),
                "reduce_only": reduce_only,
                "hedge_order_type": self.config.hedge_order_type,
                "live": live,
            }
            if hedge_capped:
                action["hedge_target_capped"] = True
                action["raw_target_hedge_base"] = format_decimal(raw_target_base, 8)
            if live:
                response = self._place_hedge_perp_order(
                    context,
                    direction=side,
                    instrument_name=perp_name,
                    amount=order_amount,
                    label=self._hedge_label(currency, "position"),
                    reduce_only=reduce_only,
                )
                action["response"] = response
                if isinstance(response, dict) and response.get("skipped"):
                    action["skipped"] = True
                    action["skip_reason"] = response.get("reason")
            actions.append(action)
        return actions

    def _build_portfolio_snapshot(
        self,
        *,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        regime_by_currency: dict[str, RiskRegime],
        regime_detail_by_currency: dict[str, tuple[str, ...]],
        future_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]] | None = None,
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
        flow_books = self._cash_flow_books_for_snapshot(per_book_equities)
        day_net_flow_usdc_by_book: dict[str, Decimal] = {
            book: state.day_net_flow_usdc_by_book.get(book, Decimal("0")) for book in flow_books
        }
        day_net_flow_native_by_book: dict[str, Decimal] = {
            book: state.day_net_flow_native_by_book.get(book, Decimal("0")) for book in flow_books
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
        #
        # 3. Cross-book spot swaps (e.g. USDT → BTC) are matched stable ↔
        #    crypto so internal reallocations do not trip per-book drawdown.
        min_equity = self.config.min_book_equity_usdc
        index_price_by_book = {
            book: Decimal("1")
            if book in ("USDC", "USDT", "USDE")
            else self._currency_index_price(book, orderbook_cache)
            for book in per_book_native_equities
        }
        cross_book_flow_native = cross_book_flow_adjustments_native(
            per_book_native_equities=per_book_native_equities,
            per_book_native_day_start=per_book_native_day_start,
            day_net_flow_native_by_book=day_net_flow_native_by_book,
            day_net_flow_usdc_by_book=day_net_flow_usdc_by_book,
            index_price_by_book=index_price_by_book,
            min_match_usdc=min_equity,
        )
        per_book_drawdown: dict[str, Decimal] = {}
        for book, equity in per_book_native_equities.items():
            start_usdc = per_book_day_start.get(book, Decimal("0"))
            if start_usdc <= min_equity:
                continue
            start = per_book_native_day_start.get(book, equity)
            if start <= 0:
                continue
            if book in ("USDC", "USDT", "USDE"):
                net_flow = day_net_flow_usdc_by_book.get(book, Decimal("0"))
            else:
                net_flow = day_net_flow_native_by_book.get(book, Decimal("0"))
            net_flow += cross_book_flow_native.get(book, Decimal("0"))
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
        crisis_currencies_with_open_groups = {
            group.currency.upper()
            for group in open_groups
            if regime_by_currency.get(group.currency, RiskRegime.CRISIS) is RiskRegime.CRISIS
        }
        crisis_open_group = bool(crisis_currencies_with_open_groups)
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
        portfolio_wide_entry_halt = (
            legacy_cooling or open_max_loss_pct >= self.config.halt_open_max_loss_pct or hard_stop_open_group
        )
        halt_new_entries_by_currency = build_halt_new_entries_by_currency(
            managed_currencies=self.config.managed_currencies,
            regime_by_currency=regime_by_currency,
            regime_detail_by_currency=regime_detail_by_currency,
            crisis_currencies_with_open_groups=crisis_currencies_with_open_groups,
            hard_derisk_on_crisis_open_group=self.config.hard_derisk_on_crisis_open_group,
            portfolio_blocks_all=portfolio_wide_entry_halt,
        )
        halt_new_entries = portfolio_wide_entry_halt or not any(
            not halted for halted in halt_new_entries_by_currency.values()
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
            halted = sorted(crisis_currencies_with_open_groups)
            halt_entry_reasons.append("hard_derisk: open_trade_group_in_crisis_regime_currency: " + ", ".join(halted))
        if hard_stop_open_group:
            halt_entry_reasons.append("hard_derisk: open_group_hard_defense_or_stop_trigger")
        # Surface per-book halts so log lines still show which book triggered.
        for book in sorted(halt_entries_by_book):
            for reason in halt_reasons_by_book.get(book, []):
                prefixed = f"book={book} {reason}"
                if prefixed not in halt_entry_reasons:
                    halt_entry_reasons.append(prefixed)
        for currency in sorted(halt_new_entries_by_currency):
            if halt_new_entries_by_currency[currency]:
                regime = regime_by_currency.get(currency, RiskRegime.CRISIS)
                if regime is not RiskRegime.NORMAL:
                    halt_entry_reasons.append(f"{currency}: regime={regime.value}")
                detail = regime_detail_by_currency.get(currency, ())
                if any(note.startswith("data_unavailable") for note in detail):
                    halt_entry_reasons.append(f"{currency}: regime data_unavailable")
        append_underlying_regime_halt_reasons_for_usdc_book(
            halt_reasons_by_book,
            scan_underlyings=self.config.scan_underlyings,
            halt_new_entries_by_currency=halt_new_entries_by_currency,
            regime_by_currency=regime_by_currency,
        )
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
            halt_new_entries_by_currency=halt_new_entries_by_currency,
            portfolio_wide_entry_halt=portfolio_wide_entry_halt,
            hard_derisk=hard_derisk,
            cooldown_until_ms=state.cooldown_until_ms,
            cooling_down=cooling_down,
            delta_totals_by_currency=self._delta_totals_by_currency(
                summaries,
                state,
                future_positions,
                markets_by_currency=markets_by_currency or {},
            ),
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

    def _build_hedge_plan(
        self,
        context: RuntimeContext,
        currency: str,
        *,
        mode: str,
        target_pct: Decimal | None = None,
    ) -> HedgePlan | None:
        current_delta = context.snapshot.delta_totals_by_currency.get(currency, Decimal("0"))
        index_price = self._currency_index_price(currency, context.orderbook_cache)
        if index_price <= 0:
            return None
        effective_capital = self._effective_capital(context.snapshot.total_equity_usdc)
        if target_pct is None:
            target_pct = (
                self.config.hard_hedge_delta_cap_pct if mode == "hard" else self.config.soft_hedge_delta_cap_pct
            )
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
        # Linear USDC perps size the order in base (coin) units; inverse perps
        # use a USD notional. Convert accordingly so we hedge the right size.
        uses_base = self._perp_uses_base_amount(currency)
        raw_amount = abs(delta_change_base) if uses_base else abs(delta_change_base) * index_price
        order_amount = self._align_future_order_amount(
            context,
            instrument_name=self._perp_instrument(currency),
            amount=raw_amount,
        )
        if order_amount <= 0:
            return None
        realized_base = order_amount if uses_base else order_amount / index_price
        delta_change_base = -realized_base if side == "sell" else realized_base
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

        for collateral_raw in cash_flow_scan_currencies(self.config.traded_collaterals):
            self._refresh_cash_flow_for_currency(
                state,
                orderbook_cache,
                collateral=collateral_raw.upper(),
                now_ms=now_ms,
                force=force,
                interval_ms=interval_ms,
            )

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
        for collateral_raw in cash_flow_scan_currencies(self.config.traded_collaterals):
            self._refresh_cash_flow_for_currency(
                state,
                orderbook_cache,
                collateral=collateral_raw.upper(),
                now_ms=now_ms,
                force=True,
                interval_ms=0,
            )

    def _cash_flow_books_for_snapshot(self, per_book_equities: dict[str, Decimal]) -> list[str]:
        """Equity books plus any extra flow-only books (e.g. USDT on inverse subs)."""
        books = list(per_book_equities.keys())
        for book in cash_flow_scan_currencies(self.config.traded_collaterals):
            if book not in books:
                books.append(book)
        return books

    def _refresh_cash_flow_for_currency(
        self,
        state: StrategyState,
        orderbook_cache: dict[str, OrderBookSnapshot],
        *,
        collateral: str,
        now_ms: int,
        force: bool,
        interval_ms: int,
    ) -> None:
        flow_start_ms = self._flow_query_start_ms(state, collateral)
        if flow_start_ms <= 0:
            return
        last_query = state.last_flow_query_ms_by_book.get(collateral, 0)
        if not force and last_query and (now_ms - last_query) < interval_ms:
            return
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
            return
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
        flow_books = self._cash_flow_books_for_snapshot(per_book)
        now_ms = utc_now_ms()
        if state.day_key != today_key:
            state.day_key = today_key
            state.day_start_equity_usdc = total_equity
            state.day_start_equity_by_book = dict(per_book)
            state.day_start_equity_native_by_book = dict(per_book_native)
            # New UTC day → flow tallies reset to zero and query timestamps
            # cleared so the next cycle re-queries from the fresh day-start.
            state.day_net_flow_usdc_by_book = {book: Decimal("0") for book in flow_books}
            state.day_net_flow_native_by_book = {book: Decimal("0") for book in flow_books}
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
            for book in flow_books:
                state.day_net_flow_usdc_by_book.setdefault(book, Decimal("0"))
                state.day_net_flow_native_by_book.setdefault(book, Decimal("0"))
        state.last_equity_usdc = total_equity
        state.last_equity_by_book = dict(per_book)
        state.last_equity_native_by_book = dict(per_book_native)
        # Drop expired per-book cooldowns so the dict doesn't grow unbounded.
        state.cooldown_until_ms_by_book = {
            book: ts for book, ts in state.cooldown_until_ms_by_book.items() if ts and ts > now_ms
        }
        return state

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
        usdc_linear = (
            short_instrument.quote_currency.upper() == "USDC" and short_instrument.settlement_currency.upper() == "USDC"
        )
        idx = short_book.index_price
        usdc_path = usdc_linear or group.collateral_currency.upper() == "USDC"
        # Capture the ask-based short-leg debit so we can derive a mark-based
        # variant (mark_debit) without re-running the long-leg subtraction: the
        # long credit cancels out, so mark_debit = current_debit + (mark - ask)
        # short-leg delta. mark_debit drives the loss-based defense stop so an
        # IV/spread spike on the short ask does not stop us out at a local peak.
        short_debit_ask = self._short_close_debit_usdc(
            premium=close_premium,
            quantity=group.quantity,
            short_book=short_book,
            short_instrument=short_instrument,
            usdc_path=usdc_path,
            idx=idx,
        )
        mark_premium = short_book.mark_price if short_book.mark_price > 0 else close_premium
        short_debit_mark = self._short_close_debit_usdc(
            premium=mark_premium,
            quantity=group.quantity,
            short_book=short_book,
            short_instrument=short_instrument,
            usdc_path=usdc_path,
            idx=idx,
        )
        if usdc_path:
            group.current_close_fee = self._option_fee_usdc(
                premium=close_premium,
                quantity=group.quantity,
                index_price=short_book.index_price,
                base_currency=short_instrument.base_currency,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
            )
            group.current_close_fee_collateral = Decimal("0")
            group.current_debit += group.current_close_fee
        else:
            close_fee_collateral = self._option_fee_native(
                premium=close_premium,
                quantity=group.quantity,
                index_price=short_book.index_price,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
            )
            gross_native = close_premium * group.quantity
            group.current_close_fee_collateral = close_fee_collateral
            group.current_close_fee = close_fee_collateral * idx if idx > 0 else Decimal("0")
            group.current_debit = (gross_native + close_fee_collateral) * idx if idx > 0 else group.current_debit
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
                if usdc_linear or group.collateral_currency.upper() == "USDC":
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
                else:
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
                    group.current_debit = max(
                        group.current_debit - long_net_native * idx_long if idx_long > 0 else Decimal("0"),
                        Decimal("0"),
                    )
                    group.current_close_fee += long_fee_collateral * idx_long if idx_long > 0 else Decimal("0")
                    group.current_close_fee_collateral += long_fee_collateral
            except Exception as exc:
                LOGGER.warning(
                    "refresh_group %s: unable to refresh long leg %s (%s)",
                    group.group_id,
                    group.long_instrument_name,
                    exc,
                )
        group.mark_debit = max(group.current_debit + (short_debit_mark - short_debit_ask), Decimal("0"))
        group.profit_capture = safe_div(max(group.entry_credit - group.current_debit, Decimal("0")), group.entry_credit)
        group.short_delta = abs(short_book.delta)

    def _delta_totals_by_currency(
        self,
        summaries: dict[str, AccountSummary],
        state: StrategyState,
        future_positions: list[Position],
        *,
        markets_by_currency: dict[str, list[OptionInstrument]] | None = None,
    ) -> dict[str, Decimal]:
        """Self-compute per-currency net delta from our own tracked legs + perp hedge.

        We intentionally do NOT use ``summary.delta_total`` (even as a fallback)
        because Deribit includes hedge perps from every strategy/subaccount that
        shares credentials, which can blow up the hedge plan. The matching
        ``summary.delta_total`` monitoring value is exposed via the account
        summaries for dashboards but hedging decisions run off this value.

        Formula per open group:
            group_delta = option_sign * abs(greek_delta) * quantity * contract_size

        where ``option_sign`` is +1 for a short put (short puts are long-delta)
        and -1 for a short call (short calls are short-delta). ``quantity`` is
        Deribit contract count; ``contract_size`` converts to base coin units.
        """
        _ = summaries  # retained for signature parity / future parity checks
        markets = markets_by_currency or {}
        totals: dict[str, Decimal] = {}
        for currency in self.config.managed_currencies:
            group_delta = Decimal("0")
            for group in self._open_groups(state):
                if group.currency != currency:
                    continue
                contract_size = self._contract_size_for_group(group, markets)
                group_delta += self._group_option_delta(group, contract_size=contract_size)
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
