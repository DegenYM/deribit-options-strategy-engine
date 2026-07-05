from __future__ import annotations

from typing import Any

from ..exceptions import TransientExchangeError
from ..models import OrderBookSnapshot, RiskRegime, StrategyState
from ..utils import utc_now_ms
from .cash_flow import CashFlowMixin
from .context import LOGGER, ExchangePrefetch, RuntimeContext
from .cycle_logging import CycleLoggingMixin
from .group_exit import GroupExitMixin
from .group_refresh import GroupRefreshMixin
from .hedge_management import HedgeManagementMixin
from .portfolio_snapshot import PortfolioSnapshotMixin
from .regime import RegimeMixin

__all__ = ["ManagementMixin"]


class ManagementMixin(
    CycleLoggingMixin,
    GroupExitMixin,
    HedgeManagementMixin,
    PortfolioSnapshotMixin,
    RegimeMixin,
    CashFlowMixin,
    GroupRefreshMixin,
):
    """Manage-cycle orchestration (``manage``/``run``) plus small shared helpers.

    The bulk of the previous 1927-line module has been split by
    responsibility into sibling mixins (cycle logging, group exit/defense,
    hedge management, portfolio snapshot, regime detection, cash flow, group
    refresh) that are composed here. See ``docs/roadmap-2026H2-zh-TW.md``
    Workstream D.
    """

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
