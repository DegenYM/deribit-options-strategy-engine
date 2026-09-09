from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..covered_call_settlement import (
    covered_call_spot_exit_premium_native,
    covered_call_spot_exit_target_native,
    resolve_covered_call_settlement_loss,
)
from ..exit_reasons import INCOME_EXIT_REASONS
from ..models import (
    AccountSummary,
    OptionInstrument,
    OrderBookSnapshot,
    PortfolioSnapshot,
    RiskRegime,
    StrategyState,
    TradeGroup,
)
from ..utils import (
    align_option_order_amount,
    format_decimal,
    to_decimal,
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
        if self._time_exit_triggered(context, group):
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
        if live and self.config.has_private_credentials:
            from ..spot_exit_ops import reconcile_spot_exits_in_groups, reschedule_incomplete_spot_exits

            reconcile_spot_exits_in_groups(context.state.groups, self.client)
            reschedule_incomplete_spot_exits(
                context.state.groups,
                remaining_native_for_group=lambda group: self._plan_covered_call_spot_exit_fields(
                    group,
                    orderbook_cache=context.orderbook_cache,
                    markets_by_currency=context.markets_by_currency,
                    summaries={},
                    live=False,
                    reason=group.spot_exit_reason or "covered_call_settlement_exit",
                )["amount"],
            )
        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if (
                group.status == "closed"
                and self._is_covered_call_group(group)
                and group.spot_exit_status in {"pending", "failed"}
            ):
                actions.append(
                    self._execute_covered_call_spot_exit(
                        context,
                        group,
                        reason=group.spot_exit_reason or "covered_call_settlement_exit",
                        live=live,
                    )
                )
        if live and actions and self.config.has_private_credentials:
            refreshed = self._account_summaries_by_currency()
            if refreshed:
                context.summaries.update(refreshed)
        return actions

    def _pending_auto_spot_restore_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        if not self._is_covered_call_strategy():
            return []
        if not self.config.covered_call_auto_spot_restore_enabled:
            return []
        from ..spot_restore_ops import (
            _spot_restore_order_is_open,
            evaluate_auto_spot_restore_park,
            execute_spot_restore_for_group,
            mark_spot_restore_operator_cancelled,
            reconcile_spot_restore_from_exchange,
            spot_restore_operator_cancelled,
            unrestored_spot_exit_native,
        )

        min_edge = self.config.covered_call_auto_spot_restore_min_edge_pct
        prefix = self.config.order_label_prefix
        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if group.status != "closed" or not self._is_covered_call_group(group):
                continue
            currency = group.currency.upper()
            if currency not in {"BTC", "ETH"}:
                continue
            if self._cash_secured_owns_itm_group(group):
                continue
            if str(group.spot_exit_status or "").lower() == "skipped":
                if not live:
                    actions.append(
                        {
                            "action": "auto_spot_restore_skipped",
                            "group_id": group.group_id,
                            "reason": "spot_exit_skipped",
                        }
                    )
                continue
            reconcile_spot_restore_from_exchange(
                group,
                client=self.client,
                order_label_prefix=prefix,
            )
            unrestored = unrestored_spot_exit_native(group, groups=context.state.groups)
            if unrestored <= 0:
                continue
            if spot_restore_operator_cancelled(group):
                if not live:
                    actions.append(
                        {
                            "action": "auto_spot_restore_skipped",
                            "group_id": group.group_id,
                            "reason": "operator_cancelled",
                        }
                    )
                continue
            restore_status = str(group.spot_restore_status or "").lower()
            if restore_status in {"submitted", "pending"} or group.spot_restore_order_id:
                if _spot_restore_order_is_open(self.client, group.spot_restore_order_id):
                    if not live:
                        actions.append(
                            {
                                "action": "auto_spot_restore_skipped",
                                "group_id": group.group_id,
                                "reason": "restore_in_flight",
                                "spot_restore_order_id": group.spot_restore_order_id or None,
                                "max_buy_price": evaluate_auto_spot_restore_park(
                                    group,
                                    min_edge_pct=min_edge,
                                    groups=context.state.groups,
                                ).get("max_buy_price"),
                            }
                        )
                    continue
                if restore_status in {"submitted", "pending"}:
                    mark_spot_restore_operator_cancelled(group)
                    actions.append(
                        {
                            "action": "auto_spot_restore_skipped",
                            "group_id": group.group_id,
                            "reason": "operator_cancelled",
                            "spot_restore_order_id": None,
                        }
                    )
                    continue
            decision = evaluate_auto_spot_restore_park(
                group,
                min_edge_pct=min_edge,
                groups=context.state.groups,
            )
            if not decision.get("ok"):
                if not live:
                    actions.append(
                        {
                            "action": "auto_spot_restore_skipped",
                            "group_id": group.group_id,
                            "reason": decision.get("reason"),
                            "max_buy_price": decision.get("max_buy_price"),
                        }
                    )
                continue
            # Park GTC at the cap. Never market-buy — a later cycle only reconciles.
            action = execute_spot_restore_for_group(
                self,
                group,
                live=live,
                park_resting=True,
                restore_reason="auto_spot_restore_park",
                groups=context.state.groups,
            )
            action["auto"] = True
            action["park_resting"] = True
            action["max_buy_price"] = decision.get("max_buy_price")
            actions.append(action)
            if live and action.get("action") == "spot_restore":
                self._telegram_alert(
                    "ITM cover auto-restored",
                    body=f"group={group.group_id} {currency} limit filled at cap",
                    event_key=f"auto_spot_restore:{self._journal_scope_key()}:{group.group_id}",
                    level="info",
                )
            elif live and action.get("action") == "spot_restore_submitted":
                self._telegram_alert(
                    "ITM cover restore parked",
                    body=(f"group={group.group_id} {currency} limit@{decision.get('max_buy_price')}"),
                    event_key=f"auto_spot_restore_park:{self._journal_scope_key()}:{group.group_id}",
                    level="info",
                )
        return actions

    def _pending_itm_cash_secured_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        if not self._is_covered_call_strategy():
            return []
        if not self.config.covered_call_itm_to_cash_secured_enabled:
            return []
        if self._cash_secured_blocked_by_hard_derisk(context):
            return []

        from ..cash_secured_ops import (
            cash_secured_child_is_open,
            cash_secured_hold_credit_dte_from_closed_roll,
            cash_secured_last_active_roll_child,
            cash_secured_quantity,
            cash_secured_strike_bounds,
            cash_secured_target_native,
            itm_sold_ready_for_cash_secured,
        )
        from ..spot_restore_ops import covered_call_cover_native

        actions: list[dict[str, Any]] = []
        summaries = self._account_summaries_by_currency() if live else context.summaries
        if summaries:
            context.summaries.update(summaries)

        for group in context.state.groups:
            if str(group.cash_secured_status or "").lower() != "submitted":
                continue
            actions.append(self._reconcile_parked_cash_secured(context, group, live=live))

        for group in context.state.groups:
            ready, reason = itm_sold_ready_for_cash_secured(group, context.state.groups)
            if not ready:
                if not live and reason in {"spot_exit_not_usdc", "spot_exit_not_filled"}:
                    actions.append(
                        {
                            "action": "cash_secured_skipped",
                            "group_id": group.group_id,
                            "reason": reason,
                        }
                    )
                continue
            if cash_secured_child_is_open(context.state.groups, group):
                continue
            regime = context.regime_by_currency.get(group.currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                if not live:
                    actions.append(
                        {
                            "action": "cash_secured_skipped",
                            "group_id": group.group_id,
                            "reason": "crisis_regime",
                        }
                    )
                continue

            usdc = context.summaries.get("USDC")
            if usdc is None or usdc.equity <= 0:
                if not live:
                    actions.append(
                        {
                            "action": "cash_secured_skipped",
                            "group_id": group.group_id,
                            "reason": "usdc_unavailable",
                        }
                    )
                continue
            usdc_free = max(usdc.available_funds, usdc.available_withdrawal_funds, Decimal("0"))
            sold = cash_secured_target_native(group, context.state.groups)
            if sold <= 0:
                LOGGER.info(
                    "cash_secured: skip entry, cover already restored group=%s",
                    group.group_id,
                )
                if not live:
                    actions.append(
                        {
                            "action": "cash_secured_skipped",
                            "group_id": group.group_id,
                            "reason": "cover_restored",
                        }
                    )
                continue
            cover = covered_call_cover_native(group)
            min_strike, max_strike = cash_secured_strike_bounds(
                group.short_strike,
                self.config.covered_call_csp_strike_floor_pct,
            )
            qty_cap = cover if cover > 0 else sold
            roll_child = cash_secured_last_active_roll_child(group, context.state.groups)
            if roll_child is not None:
                hold_credit, hold_dte = cash_secured_hold_credit_dte_from_closed_roll(roll_child)
                freed = max(roll_child.short_strike * roll_child.quantity, Decimal("0"))
                retry_qty_cap = qty_cap
                if roll_child.quantity > 0:
                    retry_qty_cap = min(qty_cap, roll_child.quantity) if qty_cap > 0 else roll_child.quantity
                ranked = self._rank_itm_cash_secured_candidates(
                    context,
                    group,
                    sold_native=sold,
                    usdc_available=usdc_free + freed,
                    min_strike=min_strike,
                    max_strike=max_strike,
                    summary_equity=max(usdc.equity, usdc_free + freed),
                    summary_maintenance_margin=usdc.maintenance_margin,
                    quantity_cap=retry_qty_cap,
                )
                picked = self._pick_csp_daily_yield_candidate(
                    context,
                    ranked,
                    hold_credit=hold_credit,
                    hold_dte=hold_dte,
                    close_fee=Decimal("0"),
                    index_price=self._currency_index_price(group.currency, context.orderbook_cache),
                    amortize_close_fee=False,
                )
                if picked is None:
                    LOGGER.info(
                        "cash_secured: skip retry after active roll, daily yield not higher group=%s",
                        group.group_id,
                    )
                    if not live:
                        actions.append(
                            {
                                "action": "cash_secured_skipped",
                                "group_id": group.group_id,
                                "reason": "daily_yield_not_higher",
                            }
                        )
                    continue
                candidate = picked[0]
            else:
                candidate = self._scan_itm_cash_secured_candidate(
                    context,
                    group,
                    sold_native=sold,
                    usdc_available=usdc_free,
                    quantity_cap=qty_cap,
                    min_strike=min_strike,
                    max_strike=max_strike,
                    summary_equity=usdc.equity,
                    summary_maintenance_margin=usdc.maintenance_margin,
                )
            if candidate is None:
                if not live:
                    actions.append(
                        {
                            "action": "cash_secured_skipped",
                            "group_id": group.group_id,
                            "reason": "no_short_dated_put",
                            "strike_min": format_decimal(min_strike, 2),
                            "strike_max": format_decimal(max_strike, 2),
                            "quantity": cash_secured_quantity(
                                sold_native=sold,
                                usdc_available=usdc_free,
                                strike=group.short_strike,
                                contract_size=Decimal("1"),
                                min_trade_amount=Decimal("0.01"),
                                cap=cover if cover > 0 else None,
                            ),
                        }
                    )
                continue
            actions.append(
                self._execute_itm_cash_secured_entry(
                    context,
                    group,
                    candidate,
                    live=live,
                )
            )
        return actions

    def _load_linear_usdc_puts(self, currency: str) -> list[OptionInstrument]:
        ccy = currency.upper()
        markets: list[OptionInstrument] = []
        try:
            rows = self.client.get_instruments("USDC", kind="option", expired=False)
        except Exception:
            LOGGER.exception("cash_secured: failed to load USDC puts for %s", ccy)
            return []
        for row in rows:
            instrument = OptionInstrument.from_api(row)
            if instrument.base_currency.upper() != ccy:
                continue
            if instrument.option_type != "put":
                continue
            if instrument.quote_currency.upper() != "USDC":
                continue
            if instrument.settlement_currency.upper() != "USDC":
                continue
            markets.append(instrument)
        return markets

    def _rank_itm_cash_secured_candidates(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        sold_native: Decimal,
        usdc_available: Decimal,
        min_strike: Decimal,
        max_strike: Decimal,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        quantity_cap: Decimal | None = None,
        expiry_after_ms: int | None = None,
        exclude_instrument: str | None = None,
    ) -> list[Any]:
        from ..cash_secured_ops import cash_secured_quantity, cash_secured_scan_rank

        dte_min = self.config.covered_call_csp_dte_min
        dte_max = self.config.covered_call_csp_dte_max
        regime = context.regime_by_currency.get(group.currency, RiskRegime.NORMAL)
        markets = self._load_linear_usdc_puts(group.currency)
        if markets:
            context.markets_by_currency.setdefault(group.currency, [])
            known = {item.instrument_name for item in context.markets_by_currency[group.currency]}
            for instrument in markets:
                if instrument.instrument_name not in known:
                    context.markets_by_currency[group.currency].append(instrument)
                    known.add(instrument.instrument_name)

        skip = str(exclude_instrument or "").strip()
        ranked: list[tuple[tuple[Decimal, Decimal, Decimal, Decimal], Any]] = []
        for instrument in markets:
            if skip and instrument.instrument_name == skip:
                continue
            if expiry_after_ms is not None and instrument.expiration_timestamp_ms <= expiry_after_ms:
                continue
            dte = instrument.dte_days()
            if dte < dte_min or dte > dte_max:
                continue
            if instrument.strike < min_strike or instrument.strike > max_strike:
                continue
            quantity = cash_secured_quantity(
                sold_native=sold_native,
                usdc_available=usdc_available,
                strike=instrument.strike,
                contract_size=instrument.contract_size,
                min_trade_amount=instrument.min_trade_amount,
                cap=quantity_cap,
            )
            if quantity <= 0:
                continue
            try:
                book = self._get_orderbook(instrument.instrument_name, context.orderbook_cache)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("cash_secured: skip candidate %s (orderbook): %s", instrument.instrument_name, exc)
                continue
            candidate, _fail = self.strategy.refresh_cash_secured_put_candidate(
                instrument=instrument,
                book=book,
                regime=regime,
                summary_equity=summary_equity,
                summary_maintenance_margin=summary_maintenance_margin,
                collateral_currency="USDC",
                currency=group.currency,
                quantity=quantity,
                existing_im_for_expiry=Decimal("0"),
            )
            if candidate is None:
                continue
            key = cash_secured_scan_rank(
                quantity=quantity,
                strike=instrument.strike,
                dte=Decimal(str(dte)),
                net_apr=candidate.net_apr,
            )
            ranked.append((key, candidate))
        ranked.sort(key=lambda item: item[0])
        return [candidate for _key, candidate in ranked]

    def _csp_linear_option_fee(
        self,
        *,
        index_price: Decimal,
        premium: Decimal,
        quantity: Decimal,
        currency: str,
    ) -> Decimal:
        from ..fees import option_trade_fee_usdc

        return option_trade_fee_usdc(
            index_price=index_price,
            premium=premium,
            quantity=quantity,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
            base_currency=currency,
            quote_currency="USDC",
            settlement_currency="USDC",
            fee_discount_rate=self._option_fee_discount_rate_at(),
        )

    def _pick_csp_daily_yield_candidate(
        self,
        context: RuntimeContext,
        ranked: list[Any],
        *,
        hold_credit: Decimal,
        hold_dte: Decimal,
        close_fee: Decimal,
        index_price: Decimal,
        amortize_close_fee: bool,
    ) -> tuple[Any, dict[str, str]] | None:
        """Best window candidate whose daily yield beats holding remaining TV."""
        from ..cash_secured_ops import (
            cash_secured_active_roll_daily_beats_hold,
            cash_secured_self_assign_liquidity_ok,
        )

        best: tuple[Any, dict[str, str], Decimal] | None = None
        for candidate in ranked:
            try:
                repl_book = self._get_orderbook(candidate.short_leg.instrument_name, context.orderbook_cache)
            except Exception:  # noqa: BLE001
                continue
            repl_liquid, _why = cash_secured_self_assign_liquidity_ok(
                spread_ratio=repl_book.spread_ratio,
                best_bid_price=repl_book.best_bid_price,
                best_ask_price=repl_book.best_ask_price,
                best_ask_amount=repl_book.best_bid_amount,
                quantity=candidate.quantity,
                max_spread_ratio=self.config.covered_call_csp_self_assign_max_spread_ratio,
            )
            if not repl_liquid:
                continue
            new_credit = max(candidate.short_leg.best_bid_price, Decimal("0")) * candidate.quantity
            open_fee = self._csp_linear_option_fee(
                index_price=repl_book.index_price if repl_book.index_price > 0 else index_price,
                premium=candidate.short_leg.best_bid_price,
                quantity=candidate.quantity,
                currency=str(candidate.currency or "BTC"),
            )
            switch_fees = open_fee + (close_fee if amortize_close_fee else Decimal("0"))
            hold_daily, roll_daily, beats = cash_secured_active_roll_daily_beats_hold(
                hold_credit=hold_credit,
                hold_dte=hold_dte,
                new_credit=new_credit,
                new_dte=candidate.dte_days,
                switch_fees=switch_fees,
            )
            if not beats:
                continue
            numbers = {
                "replacement_instrument": candidate.short_leg.instrument_name,
                "close_debit": format_decimal(hold_credit, 8),
                "new_credit": format_decimal(new_credit, 8),
                "close_fee": format_decimal(close_fee, 8),
                "open_fee": format_decimal(open_fee, 8),
                "hold_daily": format_decimal(hold_daily, 8),
                "roll_daily": format_decimal(roll_daily, 8),
                "quantity": format_decimal(candidate.quantity, 8),
            }
            if best is None or roll_daily > best[2]:
                best = (candidate, numbers, roll_daily)
        if best is None:
            return None
        return best[0], best[1]

    def _scan_itm_cash_secured_candidate(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        sold_native: Decimal,
        usdc_available: Decimal,
        min_strike: Decimal,
        max_strike: Decimal,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        quantity_cap: Decimal | None = None,
        expiry_after_ms: int | None = None,
        exclude_instrument: str | None = None,
    ):
        ranked = self._rank_itm_cash_secured_candidates(
            context,
            group,
            sold_native=sold_native,
            usdc_available=usdc_available,
            min_strike=min_strike,
            max_strike=max_strike,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            quantity_cap=quantity_cap,
            expiry_after_ms=expiry_after_ms,
            exclude_instrument=exclude_instrument,
        )
        return ranked[0] if ranked else None

    def _scan_cash_secured_one_parent(
        self,
        context: RuntimeContext,
        parent: TradeGroup,
        *,
        parent_note: str,
        top_n: int | None,
        usdc,
        strike_floor_pct: Decimal | None = None,
    ) -> dict[str, Any]:
        from ..cash_secured_ops import (
            cash_secured_strike_bounds,
            cash_secured_target_native,
            itm_sold_ready_for_cash_secured,
        )
        from ..spot_restore_ops import covered_call_cover_native

        ready, ready_reason = itm_sold_ready_for_cash_secured(parent, context.state.groups)
        sold = cash_secured_target_native(parent, context.state.groups)
        cover = covered_call_cover_native(parent)
        if sold <= 0 and cover > 0 and ready_reason in {"not_closed", "spot_exit_not_filled"}:
            sold = cover
        floor = strike_floor_pct if strike_floor_pct is not None else self.config.covered_call_csp_strike_floor_pct
        min_strike, max_strike = cash_secured_strike_bounds(
            parent.short_strike,
            floor,
        )
        usdc_free = Decimal("0")
        usdc_equity = Decimal("0")
        usdc_mm = Decimal("0")
        if usdc is not None:
            usdc_free = max(usdc.available_funds, usdc.available_withdrawal_funds, Decimal("0"))
            usdc_equity = usdc.equity
            usdc_mm = usdc.maintenance_margin
        hypothetical_usdc = False
        if not ready and ready_reason in {"not_closed", "spot_exit_not_filled"}:
            assumed = (cover if cover > 0 else sold) * parent.short_strike
            if assumed > usdc_free:
                usdc_free = assumed
                hypothetical_usdc = True
            if assumed > usdc_equity:
                usdc_equity = assumed
        if usdc_equity <= 0 and usdc_free <= 0:
            return {
                "source_group_id": parent.group_id,
                "parent_note": parent_note,
                "currency": parent.currency,
                "reason": "usdc_unavailable",
                "ready": ready,
                "ready_reason": ready_reason,
                "would_place": False,
                "pick": None,
                "candidates": [],
                "candidate_count": 0,
            }
        ranked = self._rank_itm_cash_secured_candidates(
            context,
            parent,
            sold_native=sold,
            usdc_available=usdc_free,
            min_strike=min_strike,
            max_strike=max_strike,
            summary_equity=usdc_equity,
            summary_maintenance_margin=usdc_mm,
            quantity_cap=cover if cover > 0 else sold,
        )
        limit = top_n or self.config.top_n
        rows = []
        for index, candidate in enumerate(ranked[: max(1, limit)], start=1):
            preview = self._cash_secured_entry_preview(candidate)
            payload = candidate.to_dict()
            payload.update(preview)
            payload["rank"] = index
            payload["pick"] = index == 1
            rows.append(payload)
        pick = rows[0] if rows else None
        csp_enabled = bool(self.config.covered_call_itm_to_cash_secured_enabled)
        return {
            "source_group_id": parent.group_id,
            "parent_note": parent_note,
            "currency": parent.currency,
            "original_strike": format_decimal(parent.short_strike, 2),
            "strike_min": format_decimal(min_strike, 2),
            "strike_max": format_decimal(max_strike, 2),
            "dte_min": self.config.covered_call_csp_dte_min,
            "dte_max": self.config.covered_call_csp_dte_max,
            "sold_native": format_decimal(sold, 8),
            "usdc_available": format_decimal(usdc_free, 4),
            "hypothetical_usdc": hypothetical_usdc,
            "ready": ready,
            "ready_reason": ready_reason,
            "would_place": bool(ready and pick and csp_enabled),
            "reason": "" if ranked else "no_short_dated_put",
            "candidate_count": len(ranked),
            "pick": pick,
            "candidates": rows,
        }

    def scan_cash_secured(
        self,
        *,
        from_group_id: str | None = None,
        top_n: int | None = None,
        strike_floor_pct: Decimal | None = None,
    ) -> dict[str, Any]:
        """Dry-run the CSP picker for every ITM-sold covered call (or one ``from_group``)."""
        from ..cash_secured_ops import list_cash_secured_preview_parents, resolve_cash_secured_preview_parent

        if not self._is_covered_call_strategy():
            return {
                "action": "cash_secured_scan",
                "live": False,
                "reason": "not_covered_call",
                "candidates": [],
                "groups": [],
            }
        context = self._load_runtime()
        parents = list_cash_secured_preview_parents(context.state.groups, from_group_id)
        if not parents:
            _, parent_note = resolve_cash_secured_preview_parent(context.state.groups, from_group_id)
            return {
                "action": "cash_secured_scan",
                "live": False,
                "reason": parent_note,
                "from_group_id": from_group_id,
                "candidates": [],
                "groups": [],
            }
        usdc = context.summaries.get("USDC")
        group_rows = [
            self._scan_cash_secured_one_parent(
                context,
                parent,
                parent_note=note,
                top_n=top_n,
                usdc=usdc,
                strike_floor_pct=strike_floor_pct,
            )
            for parent, note in parents
        ]
        first = group_rows[0]
        csp_enabled = bool(self.config.covered_call_itm_to_cash_secured_enabled)
        payload = {
            "action": "cash_secured_scan",
            "live": False,
            "csp_enabled": csp_enabled,
            "from_group_id": from_group_id,
            "strike_floor_pct": format_decimal(
                strike_floor_pct if strike_floor_pct is not None else self.config.covered_call_csp_strike_floor_pct,
                4,
            ),
            "group_count": len(group_rows),
            "groups": group_rows,
            **first,
        }
        payload["would_place"] = any(row.get("would_place") for row in group_rows)
        payload["reason"] = (
            "" if any(row.get("pick") for row in group_rows) else (first.get("reason") or "no_short_dated_put")
        )
        return payload

    def _cash_secured_entry_preview(self, candidate) -> dict[str, Any]:
        instrument = self._entry_leg_instrument(candidate.currency, candidate.short_leg, candidate.quantity)
        book = OrderBookSnapshot(
            instrument_name=candidate.short_leg.instrument_name,
            best_bid_price=candidate.short_leg.best_bid_price,
            best_bid_amount=candidate.quantity,
            best_ask_price=candidate.short_leg.best_ask_price,
            best_ask_amount=candidate.quantity,
            mark_price=candidate.screening_mark,
            index_price=candidate.short_leg.index_price,
            delta=candidate.short_leg.delta,
            iv=Decimal("0"),
            open_interest=Decimal("0"),
        )
        bid = self.strategy.sell_taker_price(instrument, book)
        return {
            "take_bid": True,
            "park_mid": False,
            "time_in_force": "immediate_or_cancel",
            "instrument_name": candidate.short_leg.instrument_name,
            "limit_price": format_decimal(bid, 4) if bid > 0 else None,
        }

    def _link_cash_secured_child(
        self,
        context: RuntimeContext,
        parent: TradeGroup,
        group: TradeGroup,
        *,
        reason: str,
    ) -> None:
        from ..cash_secured_ops import cash_secured_children

        group.cash_secured_from_group_id = parent.group_id
        group.strategy = "cash_secured"
        context.state.groups.append(group)
        ids = [str(item).strip() for item in (parent.cash_secured_group_ids or []) if str(item).strip()]
        prior = str(parent.cash_secured_group_id or "").strip()
        if prior and prior not in ids:
            ids.append(prior)
        if group.group_id not in ids:
            ids.append(group.group_id)
        parent.cash_secured_group_ids = ids
        parent.cash_secured_status = "entered"
        parent.cash_secured_group_id = group.group_id
        parent.cash_secured_reason = reason
        parent.cash_secured_order_id = ""
        roll = len(cash_secured_children(context.state.groups, parent)) > 1
        title = "CSP roll" if roll else "ITM sold → cash secured"
        self._telegram_alert(
            title,
            body=(
                f"source={parent.group_id} child={group.group_id} {group.short_instrument_name} qty={group.quantity}"
            ),
            event_key=f"cash_secured:{self._journal_scope_key()}:{parent.group_id}:{group.group_id}",
            level="info",
        )

    def _open_cash_secured_group_from_fill(
        self,
        context: RuntimeContext,
        parent: TradeGroup,
        candidate,
        *,
        filled_amount: Decimal,
        responses: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        group_id: str | None = None,
    ) -> TradeGroup:
        child_id = str(group_id or "").strip() or self._next_group_id(context.state)
        labels = self._cash_secured_labels(parent.currency, child_id)
        primary_short_average_price = self._filled_average_price(responses)
        short_instrument = self._find_instrument(context, candidate.short_leg.instrument_name)
        short_book = self._get_orderbook(candidate.short_leg.instrument_name, context.orderbook_cache)
        idx = short_book.index_price
        actual_net_credit, short_entry_fee, entry_fee_collateral = self._short_entry_ledger(
            premium=primary_short_average_price,
            quantity=filled_amount,
            index_price=idx,
            trades=trades,
            instrument=short_instrument,
            collateral_currency=candidate.collateral_currency,
            at_timestamp_ms=utc_now_ms(),
        )
        im_per = candidate.estimated_im_total / candidate.quantity if candidate.quantity > 0 else Decimal("0")
        estimated_im_collateral = im_per * filled_amount
        if candidate.collateral_currency.upper() == "USDC":
            max_loss_usdc = estimated_im_collateral
        else:
            max_loss_usdc = estimated_im_collateral * idx if idx > 0 else candidate.estimated_im_total
        group = TradeGroup(
            group_id=child_id,
            currency=candidate.currency,
            collateral_currency=candidate.collateral_currency,
            quantity=filled_amount,
            entry_timestamp_ms=utc_now_ms(),
            expiration_timestamp_ms=candidate.short_leg.expiration_timestamp_ms,
            short_instrument_name=candidate.short_leg.instrument_name,
            short_strike=candidate.short_leg.strike,
            entry_credit=actual_net_credit,
            original_entry_credit=actual_net_credit,
            max_loss=max_loss_usdc,
            estimated_im_collateral=estimated_im_collateral,
            regime_at_entry=candidate.regime.value,
            entry_fee=short_entry_fee,
            entry_fee_collateral=entry_fee_collateral,
            short_entry_average_price=primary_short_average_price,
            entry_index_usd=idx,
            entry_net_apr=candidate.net_apr,
            short_label=labels["short"],
            hedge_label=labels["hedge"] if self.config.enable_perp_hedge else "",
            hedge_instrument_name=self._perp_instrument(candidate.currency) if self.config.enable_perp_hedge else "",
            option_type=candidate.option_type,
            strategy="cash_secured",
            covered_underlying_quantity=candidate.covered_underlying_quantity,
            cash_secured_from_group_id=parent.group_id,
        )
        self._attach_open_group_stats(group)
        return group

    def _mark_cash_secured_operator_cancelled(self, parent: TradeGroup) -> dict[str, Any]:
        parent.cash_secured_status = "skipped"
        parent.cash_secured_reason = "operator_cancelled"
        return {
            "action": "cash_secured_skipped",
            "group_id": parent.group_id,
            "reason": "operator_cancelled",
        }

    def _cash_secured_submission_is_ioc(self, parent: TradeGroup) -> bool:
        return str(parent.cash_secured_reason or "").lower() == "ioc_pending"

    def _mark_cash_secured_ioc_submitted(
        self,
        parent: TradeGroup,
        candidate,
        preview: dict[str, Any],
    ) -> None:
        parent.cash_secured_status = "submitted"
        parent.cash_secured_reason = "ioc_pending"
        parent.cash_secured_instrument_name = candidate.short_leg.instrument_name
        limit = preview.get("limit_price")
        parent.cash_secured_limit_price = to_decimal(limit) if limit else Decimal("0")
        parent.cash_secured_order_id = ""

    def _clear_cash_secured_ioc_submitted(
        self,
        parent: TradeGroup,
        *,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        if parent.cash_secured_group_id:
            parent.cash_secured_status = "entered"
        else:
            parent.cash_secured_status = "skipped"
        parent.cash_secured_reason = "ioc_unfilled"
        parent.cash_secured_order_id = ""
        parent.cash_secured_instrument_name = ""
        parent.cash_secured_limit_price = Decimal("0")
        return {
            "action": "cash_secured_unfilled",
            "group_id": parent.group_id,
            "reason": "ioc_unfilled",
            "order_id": order_id or None,
        }

    def _reconcile_parked_cash_secured(
        self,
        context: RuntimeContext,
        parent: TradeGroup,
        *,
        live: bool,
    ) -> dict[str, Any]:
        resting = {
            "action": "cash_secured_resting",
            "group_id": parent.group_id,
            "order_id": parent.cash_secured_order_id,
            "instrument_name": parent.cash_secured_instrument_name,
            "limit_price": (
                format_decimal(parent.cash_secured_limit_price, 4) if parent.cash_secured_limit_price > 0 else None
            ),
        }
        if not live:
            return resting
        is_ioc = self._cash_secured_submission_is_ioc(parent)
        order_id = str(parent.cash_secured_order_id or "").strip()
        if not order_id:
            if is_ioc:
                return {
                    "action": "cash_secured_submitted",
                    "group_id": parent.group_id,
                    "reason": "ioc_in_flight",
                    "instrument_name": parent.cash_secured_instrument_name or None,
                }
            return self._mark_cash_secured_operator_cancelled(parent)
        try:
            state = self.client.get_order_state(order_id)
        except Exception:
            LOGGER.info("cash_secured park missing order=%s group=%s", order_id, parent.group_id)
            if is_ioc:
                return self._clear_cash_secured_ioc_submitted(parent, order_id=order_id or None)
            return self._mark_cash_secured_operator_cancelled(parent)
        filled = self._response_filled_amount(state)
        order_state = str(self._response_order(state).get("order_state") or "").lower()
        if filled <= 0 and order_state in {"open", "untriggered", "new", ""}:
            return resting
        if filled <= 0:
            if is_ioc:
                return self._clear_cash_secured_ioc_submitted(parent, order_id=order_id or None)
            return self._mark_cash_secured_operator_cancelled(parent)
        trades = self._order_trades(state)
        candidate = self._cash_secured_candidate_for_parked(context, parent, filled)
        if candidate is None:
            parent.cash_secured_status = "skipped"
            parent.cash_secured_reason = "filled_but_candidate_failed"
            return {
                "action": "cash_secured_skipped",
                "group_id": parent.group_id,
                "reason": parent.cash_secured_reason,
                "filled_amount": format_decimal(filled, 8),
            }
        group = self._open_cash_secured_group_from_fill(
            context,
            parent,
            candidate,
            filled_amount=filled,
            responses=[state],
            trades=trades,
        )
        self._link_cash_secured_child(context, parent, group, reason="parked_mid_filled")
        return {
            "action": "cash_secured_entered",
            "source_group_id": parent.group_id,
            "group_id": group.group_id,
            "group": group,
            "candidate": candidate.to_dict(),
            "live": True,
        }

    def _cash_secured_candidate_for_parked(
        self,
        context: RuntimeContext,
        parent: TradeGroup,
        quantity: Decimal,
    ):
        name = str(parent.cash_secured_instrument_name or "").strip()
        if not name:
            return None
        try:
            instrument = self._find_instrument(context, name)
        except KeyError:
            markets = self._load_linear_usdc_puts(parent.currency)
            instrument = next((item for item in markets if item.instrument_name == name), None)
            if instrument is None:
                return None
        try:
            book = self._get_orderbook(name, context.orderbook_cache)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("cash_secured: orderbook unavailable for %s (group=%s): %s", name, parent.group_id, exc)
            return None
        usdc = context.summaries.get("USDC")
        if usdc is None:
            return None
        regime = context.regime_by_currency.get(parent.currency, RiskRegime.NORMAL)
        candidate, _fail = self.strategy.refresh_cash_secured_put_candidate(
            instrument=instrument,
            book=book,
            regime=regime,
            summary_equity=usdc.equity,
            summary_maintenance_margin=usdc.maintenance_margin,
            collateral_currency="USDC",
            currency=parent.currency,
            quantity=quantity,
            existing_im_for_expiry=Decimal("0"),
        )
        return candidate

    def _execute_itm_cash_secured_entry(
        self,
        context: RuntimeContext,
        parent: TradeGroup,
        candidate,
        *,
        live: bool,
    ) -> dict[str, Any]:
        preview = self._cash_secured_entry_preview(candidate)
        payload: dict[str, Any] = {
            "action": "cash_secured_preview" if not live else "cash_secured_entered",
            "source_group_id": parent.group_id,
            "candidate": candidate.to_dict(),
            "live": live,
            **preview,
        }
        if not live:
            return payload

        child_id = self._next_group_id(context.state)
        # First put keeps the parent label (existing fills / tests). Rolls use the new child id.
        label_id = child_id if str(parent.cash_secured_group_id or "").strip() else parent.group_id
        labels = self._cash_secured_labels(candidate.currency, label_id)
        request = self._entry_naked_short_request(
            candidate,
            labels["short"],
            quantity=candidate.quantity,
            aggressive=True,
        )
        self._mark_cash_secured_ioc_submitted(parent, candidate, preview)
        self.state_store.save(context.state)
        response = self._place_entry_order(context, "sell", request)
        filled = self._response_filled_amount(response)
        trades = self._order_trades(response)
        order = self._response_order(response)
        order_id = str(order.get("order_id") or "").strip()
        parent.cash_secured_order_id = order_id
        if filled > 0:
            group = self._open_cash_secured_group_from_fill(
                context,
                parent,
                candidate,
                filled_amount=filled,
                responses=[response],
                trades=trades,
                group_id=child_id,
            )
            self._link_cash_secured_child(
                context,
                parent,
                group,
                reason="csp_otm_roll" if parent.cash_secured_group_id else "itm_sold_to_usdc",
            )
            payload["action"] = "cash_secured_entered"
            payload["group_id"] = group.group_id
            payload["order_id"] = order_id or None
            payload["filled_amount"] = format_decimal(filled, 8)
            return payload

        unfilled = self._clear_cash_secured_ioc_submitted(parent, order_id=order_id or None)
        self.state_store.save(context.state)
        payload.update(unfilled)
        LOGGER.info(
            "cash_secured IOC bid unfilled group=%s instrument=%s price=%s",
            parent.group_id,
            request.get("instrument_name"),
            request.get("price"),
        )
        return payload

    def _pending_cash_secured_cover_restore_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        if not self._is_covered_call_strategy():
            return []
        if not self.config.covered_call_itm_to_cash_secured_enabled:
            return []
        from ..cash_secured_ops import (
            cash_secured_cover_restore_order_label,
            cash_secured_cover_unrestored,
        )
        from ..spot_restore_ops import (
            _spot_restore_order_is_open,
            apply_spot_restore_quote_spent,
            mark_spot_restore_operator_cancelled,
            place_spot_restore_resting_limit_buy,
            record_spot_restore_lifetime_spent,
            spot_restore_operator_cancelled,
        )
        from ..wallet_ops import _align_spot_limit_price, _lookup_spot_instrument, spot_buy_quote_spent_from_trades

        actions: list[dict[str, Any]] = []
        # Refresh account summaries at most once per cycle. The cycle-start
        # snapshot can be stale after an ITM self-assign buy-to-close spent USDC
        # earlier in this same manage pass, so live mode re-reads once here; the
        # per-group loop then tracks USDC it commits itself instead of re-fetching.
        summaries: dict[str, AccountSummary] = {}
        summaries_fresh = not live
        if live:
            try:
                summaries = self._account_summaries_by_currency()
                summaries_fresh = True
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "csp cover restore: account summaries refresh failed; "
                    "no new restore orders will be placed this cycle: %s",
                    exc,
                )
        else:
            summaries = context.summaries
        if summaries:
            context.summaries.update(summaries)
        # USDC already committed by restore orders placed earlier in this loop.
        usdc_committed = Decimal("0")

        for group in context.state.groups:
            if not group.is_cash_secured_group() or str(group.status or "").lower() != "closed":
                continue
            restore_reason = str(group.spot_restore_reason or "")
            if not (
                restore_reason.startswith("cash_secured_itm_assignment")
                or restore_reason.startswith("cash_secured_self_assign")
            ):
                continue
            if spot_restore_operator_cancelled(group):
                if not live:
                    actions.append(
                        {
                            "action": "cash_secured_cover_restore_skipped",
                            "group_id": group.group_id,
                            "reason": "operator_cancelled",
                        }
                    )
                continue
            status = str(group.spot_restore_status or "").lower()
            if status == "filled":
                continue
            if status in {"submitted", "pending"} and group.spot_restore_order_id:
                if _spot_restore_order_is_open(self.client, group.spot_restore_order_id):
                    actions.append(
                        {
                            "action": "cash_secured_cover_restore_resting",
                            "group_id": group.group_id,
                            "order_id": group.spot_restore_order_id,
                            "instrument_name": group.spot_restore_instrument_name,
                        }
                    )
                    continue
                filled = self._cash_secured_cover_restore_filled(group)
                if filled > 0:
                    actions.append(
                        {
                            "action": "cash_secured_cover_restore",
                            "group_id": group.group_id,
                            "instrument_name": group.spot_restore_instrument_name,
                            "filled_native": format_decimal(filled, 8),
                            "order_id": group.spot_restore_order_id,
                        }
                    )
                    self._telegram_alert(
                        "CSP ITM → cover restored",
                        body=(
                            f"group={group.group_id} {group.spot_restore_instrument_name} "
                            f"qty={format_decimal(filled, 8)}"
                        ),
                        event_key=f"csp_restore:{self._journal_scope_key()}:{group.group_id}",
                        level="info",
                    )
                    continue
                mark_spot_restore_operator_cancelled(group)
                actions.append(
                    {
                        "action": "cash_secured_cover_restore_skipped",
                        "group_id": group.group_id,
                        "reason": "operator_cancelled",
                    }
                )
                continue
            if status != "pending":
                continue

            unrestored = cash_secured_cover_unrestored(group)
            instrument_name = str(group.spot_restore_instrument_name or "").strip() or (
                f"{group.currency.upper()}_USDC"
            )
            usdc = context.summaries.get("USDC")
            usdc_free = Decimal("0")
            if usdc is not None:
                usdc_free = max(usdc.available_funds, usdc.available_withdrawal_funds, Decimal("0"))
            usdc_free = max(usdc_free - usdc_committed, Decimal("0"))
            try:
                book = self._get_orderbook(instrument_name, context.orderbook_cache)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "csp cover restore: orderbook unavailable for %s (group=%s): %s",
                    instrument_name,
                    group.group_id,
                    exc,
                )
                book = None
            mid = Decimal("0")
            if book is not None:
                bid, ask = book.best_bid_price, book.best_ask_price
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / Decimal("2")
                elif ask > 0:
                    mid = ask
                elif bid > 0:
                    mid = bid
                elif book.index_price > 0:
                    mid = book.index_price
            if mid <= 0 and group.close_index_usd and group.close_index_usd > 0:
                mid = group.close_index_usd
            if unrestored <= 0:
                if not live:
                    actions.append(
                        {
                            "action": "cash_secured_cover_restore_skipped",
                            "group_id": group.group_id,
                            "reason": "already_restored",
                        }
                    )
                continue
            if live and not summaries_fresh:
                # A restore buy sizes itself from free USDC; without a fresh
                # balance we must not place one. Stay pending, retry next cycle.
                group.spot_restore_status = "pending"
                actions.append(
                    {
                        "action": "cash_secured_cover_restore_skipped",
                        "group_id": group.group_id,
                        "reason": "usdc_balance_unavailable",
                        "unrestored": format_decimal(unrestored, 8),
                    }
                )
                continue
            if mid <= 0 or usdc_free <= 0:
                group.spot_restore_status = "pending"
                actions.append(
                    {
                        "action": "cash_secured_cover_restore_skipped",
                        "group_id": group.group_id,
                        "reason": "usdc_or_price_unavailable",
                        "unrestored": format_decimal(unrestored, 8),
                        "usdc_free": format_decimal(usdc_free, 4),
                    }
                )
                continue
            try:
                spot = _lookup_spot_instrument(self.client, instrument_name, group.currency.upper())
                limit_px = _align_spot_limit_price(mid, spot)
                # Leave a small buffer for fees / mark moves so Deribit does not
                # reject with not_enough_funds_in_currency.
                affordable = (usdc_free * Decimal("0.995")) / limit_px if limit_px > 0 else Decimal("0")
                qty = align_option_order_amount(
                    min(unrestored, affordable),
                    spot.contract_size,
                    spot.min_trade_amount,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "csp cover restore: spot instrument lookup/alignment failed for %s (group=%s); "
                    "using mid price and unaligned qty: %s",
                    instrument_name,
                    group.group_id,
                    exc,
                )
                limit_px = mid
                qty = unrestored
            if qty <= 0:
                group.spot_restore_status = "pending"
                actions.append(
                    {
                        "action": "cash_secured_cover_restore_skipped",
                        "group_id": group.group_id,
                        "reason": "usdc_short_for_min_size",
                        "unrestored": format_decimal(unrestored, 8),
                        "usdc_free": format_decimal(usdc_free, 4),
                    }
                )
                continue
            preview = {
                "action": "cash_secured_cover_restore_preview" if not live else "cash_secured_cover_restore_submitted",
                "group_id": group.group_id,
                "instrument_name": instrument_name,
                "quantity": format_decimal(qty, 8),
                "limit_price": format_decimal(limit_px, 4),
                "park_mid": True,
            }
            if not live:
                actions.append(preview)
                continue
            label = cash_secured_cover_restore_order_label(group, self.config.order_label_prefix)
            try:
                result = place_spot_restore_resting_limit_buy(
                    self.client,
                    instrument_name=instrument_name,
                    amount=qty,
                    price=limit_px,
                    label=label,
                )
            except Exception as exc:
                # Keep pending and retry next cycle — never crash the run loop on
                # transient USDC shortfalls after CSP self-assign / assignment.
                from ..exceptions import ExchangeError

                group.spot_restore_status = "pending"
                reason = "exchange_error"
                msg = str(exc)
                if isinstance(exc, ExchangeError) and "not_enough_funds" in msg:
                    reason = "not_enough_funds"
                else:
                    LOGGER.exception("csp cover restore place failed group=%s", group.group_id)
                actions.append(
                    {
                        "action": "cash_secured_cover_restore_skipped",
                        "group_id": group.group_id,
                        "reason": reason,
                        "detail": msg[:240],
                        "usdc_free": format_decimal(usdc_free, 4),
                        "quantity": format_decimal(qty, 8),
                    }
                )
                if reason == "not_enough_funds":
                    self._telegram_alert(
                        "CSP cover restore waiting on USDC",
                        body=(
                            f"group={group.group_id} {instrument_name} "
                            f"need≈{format_decimal(qty * limit_px, 2)} free={format_decimal(usdc_free, 2)}"
                        ),
                        event_key=f"csp_restore_usdc:{self._journal_scope_key()}:{group.group_id}",
                        level="warning",
                    )
                continue
            filled_native = to_decimal(result.get("filled_native"))
            trades = list(result.get("trades") or [])
            if filled_native > 0:
                spent = spot_buy_quote_spent_from_trades(trades, quote_currency="USDC")
                usdc_committed += spent if spent > 0 else filled_native * limit_px
                group.spot_restore_status = "filled"
                group.spot_restore_amount = (group.spot_restore_amount or Decimal("0")) + filled_native
                group.spot_restore_instrument_name = instrument_name
                group.spot_restore_order_id = str(result.get("order_id") or "")
                group.spot_restore_reason = "cash_secured_itm_assignment"
                if spent > 0:
                    group.spot_restore_quote_spent = (group.spot_restore_quote_spent or Decimal("0")) + spent
                    record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
                else:
                    apply_spot_restore_quote_spent(group, trades)
                preview["action"] = "cash_secured_cover_restore"
                preview["filled_native"] = format_decimal(filled_native, 8)
                preview["order_id"] = result.get("order_id")
                self._telegram_alert(
                    "CSP ITM → cover restored",
                    body=f"group={group.group_id} {instrument_name} qty={format_decimal(filled_native, 8)}",
                    event_key=f"csp_restore:{self._journal_scope_key()}:{group.group_id}",
                    level="info",
                )
                actions.append(preview)
                continue
            if result.get("parked"):
                usdc_committed += qty * limit_px
                group.spot_restore_status = "submitted"
                group.spot_restore_instrument_name = instrument_name
                group.spot_restore_order_id = str(result.get("order_id") or "")
                group.spot_restore_reason = "cash_secured_itm_assignment"
                preview["action"] = "cash_secured_cover_restore_submitted"
                preview["order_id"] = result.get("order_id")
                self._telegram_alert(
                    "CSP ITM → cover buy parked mid",
                    body=(
                        f"group={group.group_id} {instrument_name} "
                        f"limit={format_decimal(limit_px, 4)} qty={format_decimal(qty, 8)}"
                    ),
                    event_key=f"csp_restore_park:{self._journal_scope_key()}:{group.group_id}",
                    level="info",
                )
                actions.append(preview)
                continue
            preview["action"] = "cash_secured_cover_restore_skipped"
            preview["reason"] = str(result.get("reason") or "unfilled")
            actions.append(preview)
        return actions

    def _cash_secured_cover_restore_filled(self, group: TradeGroup) -> Decimal:
        from ..spot_restore_ops import record_spot_restore_lifetime_spent
        from ..wallet_ops import spot_buy_quote_spent_from_trades

        order_id = str(group.spot_restore_order_id or "").strip()
        if not order_id:
            return Decimal("0")
        try:
            state = self.client.get_order_state(order_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "csp cover restore: order state unavailable order=%s group=%s; treating as unfilled this cycle: %s",
                order_id,
                group.group_id,
                exc,
            )
            return Decimal("0")
        filled = self._response_filled_amount(state)
        if filled <= 0:
            return Decimal("0")
        trades = self._order_trades(state)
        spent = spot_buy_quote_spent_from_trades(trades, quote_currency="USDC")
        group.spot_restore_status = "filled"
        group.spot_restore_amount = max(group.spot_restore_amount, filled)
        group.spot_restore_reason = "cash_secured_itm_assignment"
        if spent > 0:
            group.spot_restore_quote_spent = max(group.spot_restore_quote_spent, spent)
            record_spot_restore_lifetime_spent(group, group.spot_restore_quote_spent)
        return filled

    def _maybe_schedule_profit_sweep(self, group: TradeGroup, *, reason: str, live: bool) -> None:
        """Queue a post-close spot sale of realized premium profit to USDT (not collateral)."""
        if not live:
            return
        if not self.config.covered_call_profit_sweep_enabled:
            return
        if self.config.option_strategy != "covered_call":
            return
        allowed_reasons = INCOME_EXIT_REASONS | {
            "covered_call_settlement_exit",
            "covered_call_robust_spot_exit",
            "reconciled_external",
            "reconciled_expiry",
        }
        if reason not in allowed_reasons:
            return
        if not self._is_covered_call_group(group):
            return
        # Legacy journals that folded premium into ITM spot exit must not sweep again.
        # New path: ITM sells cover−settle only; after exit is filled, sweep remaining premium.
        spot_status = str(group.spot_exit_status or "").lower()
        if spot_status in {"pending", "submitted", "filled"}:
            from ..spot_restore_ops import itm_spot_exit_premium_folded

            if itm_spot_exit_premium_folded(group):
                return
            if spot_status != "filled":
                return
        native = self._coin_profit_native_for_sweep(group)
        if native is None:
            return
        status = str(group.profit_sweep_status or "").lower()
        if status == "filled":
            unswept = self._unswept_profit_native_for_sweep(group)
            if unswept is None or unswept <= 0:
                return
            native = self._coin_profit_native_for_sweep(group) or Decimal("0")
            already = max(native - unswept, Decimal("0"))
            group.profit_sweep_status = "pending"
            group.profit_sweep_reason = reason
            if already > 0:
                group.profit_sweep_amount = already
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
        from ..profit_sweep_ops import ProfitSweepTradesUnavailable

        trade_cache = None
        if live:
            from ..profit_sweep_ops import (
                ProfitSweepTradeCache,
                heal_reconciled_proceeds_drift,
                reschedule_failed_profit_sweeps,
                reschedule_ledger_only_profit_sweeps,
                schedule_remaining_closed_profit_sweeps,
            )
            from ..trade_journal_backfill import (
                repair_manual_swap_proceeds_in_groups,
                repair_unlabeled_profit_sweeps_in_groups,
            )

            trade_cache = ProfitSweepTradeCache(self.client)
            repair_manual_swap_proceeds_in_groups(context.state.groups)
            repair_unlabeled_profit_sweeps_in_groups(
                context.state.groups,
                self.client,
                self.config.order_label_prefix,
            )
            self._reconcile_profit_sweeps_from_exchange(context, trade_cache=trade_cache)
            reschedule_ledger_only_profit_sweeps(self, context.state.groups, trade_cache=trade_cache)
            reschedule_failed_profit_sweeps(self, context.state.groups, trade_cache=trade_cache)
            schedule_remaining_closed_profit_sweeps(self, context.state.groups, trade_cache=trade_cache)
            # ITM cover−settle exits: queue remaining premium for Profit swap.
            from ..spot_exit_ops import spot_exit_realized_usdt
            from ..spot_restore_ops import group_has_itm_spot_exit_fills, itm_spot_exit_premium_folded

            for group in context.state.groups:
                if group.status != "closed" or not self._is_covered_call_group(group):
                    continue
                if not group_has_itm_spot_exit_fills(group):
                    continue
                if str(group.spot_exit_status or "").lower() != "filled":
                    continue
                if itm_spot_exit_premium_folded(group):
                    continue
                if spot_exit_realized_usdt(group) <= 0:
                    continue
                self._maybe_schedule_profit_sweep(
                    group,
                    reason=str(group.spot_exit_reason or "covered_call_settlement_exit"),
                    live=True,
                )
        actions: list[dict[str, Any]] = []
        unavailable = trade_cache.unavailable_currencies() if trade_cache is not None else {}
        for group in context.state.groups:
            if (
                group.status == "closed"
                and self._is_covered_call_group(group)
                and group.profit_sweep_status == "pending"
            ):
                if group.currency.upper() in unavailable:
                    # Exchange fills for this currency could not be loaded, so the
                    # pending status could not be reconciled against a real fill.
                    # Placing a sweep now risks selling premium twice.
                    LOGGER.warning(
                        "profit_sweep: group=%s stays pending; %s fills unavailable this cycle: %s",
                        group.group_id,
                        group.currency.upper(),
                        unavailable[group.currency.upper()],
                    )
                    actions.append(
                        {
                            "action": "covered_call_profit_sweep_skipped",
                            "group_id": group.group_id,
                            "reason": "exchange_trades_unavailable",
                            "profit_sweep_status": group.profit_sweep_status or None,
                            "live": live,
                        }
                    )
                    continue
                actions.append(self._execute_covered_call_profit_sweep(context, group, live=live))
        from ..profit_sweep_ops import _run_dust_pool_sweeps_guarded

        actions.extend(_run_dust_pool_sweeps_guarded(self, context, live=live, trade_cache=trade_cache))
        if live:
            from ..profit_sweep_dust import reconcile_dust_sweep_from_exchange
            from ..profit_sweep_ops import heal_reconciled_proceeds_drift

            heal_reconciled_proceeds_drift(self, context.state.groups)
            self._reconcile_profit_sweep_quote_proceeds(context)
            if unavailable:
                LOGGER.warning(
                    "profit_sweep: dust reconcile skipped; exchange fills unavailable for %s",
                    ", ".join(sorted(unavailable)),
                )
            else:
                try:
                    reconcile_dust_sweep_from_exchange(self, context.state.groups, trade_cache=trade_cache)
                except ProfitSweepTradesUnavailable as exc:
                    LOGGER.warning("profit_sweep: dust reconcile aborted (exchange fills unavailable): %s", exc)
        return actions

    def _reconcile_profit_sweeps_from_exchange(
        self,
        context: RuntimeContext,
        *,
        trade_cache: Any | None = None,
    ) -> None:
        from ..trade_journal_backfill import reconcile_profit_sweep_from_exchange

        for group in context.state.groups:
            reconcile_profit_sweep_from_exchange(
                group,
                client=self.client,
                order_label_prefix=self.config.order_label_prefix,
                trade_cache=trade_cache,
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
        return f"{currency.upper()}_USDT"

    def _unswept_profit_native_for_sweep(self, group: TradeGroup) -> Decimal | None:
        """Coin profit not yet sold to USDT (supports partial prior sweeps)."""
        from ..profit_sweep_ops import remaining_spot_profit_native

        native_cap = self._coin_profit_native_for_sweep(group)
        if native_cap is None or native_cap <= 0:
            return None
        status = str(group.profit_sweep_status or "").lower()
        if status == "filled":
            rem = remaining_spot_profit_native(group)
            return rem if rem > 0 else None
        swept = group.profit_sweep_amount if group.profit_sweep_amount > 0 else Decimal("0")
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

    def _covered_call_realized_loss_reserve(
        self,
        state: StrategyState,
        currency: str,
    ) -> Decimal:
        """Closed coin losses to retain as BTC so cover equity is not chronically short."""
        ccy = currency.upper()
        total = Decimal("0")
        for group in state.groups:
            if group.status != "closed" or not self._is_covered_call_group(group):
                continue
            if group.currency.upper() != ccy or not group.is_coin_collateral():
                continue
            native = group.realized_pnl_collateral_native
            if native is not None and native < 0:
                total += abs(native)
        return total

    def _covered_call_principal_reserve(self, currency: str) -> Decimal:
        ccy = currency.upper()
        if ccy == "BTC":
            return self.config.collateral_spot_btc
        if ccy == "ETH":
            return self.config.collateral_spot_eth
        return Decimal("0")

    def _profit_sweep_sellable_native_cap(
        self,
        context: RuntimeContext,
        currency: str,
        *,
        live: bool,
    ) -> Decimal:
        """Native free to sell without touching open cover or agreed inventory."""
        summaries = self._account_summaries_by_currency() if live else context.summaries
        free = self._available_covered_call_quantity_from_summaries(
            context.state,
            summaries,
            currency,
        )
        if free <= 0:
            return Decimal("0")
        reserved = self._reserved_covered_call_quantity(context.state, currency)
        principal = self._covered_call_principal_reserve(currency)
        extra_floor = max(Decimal("0"), principal - reserved)
        loss_reserve = self._covered_call_realized_loss_reserve(context.state, currency)
        if extra_floor > 0:
            return max(free - extra_floor, Decimal("0"))
        return max(free - loss_reserve, Decimal("0"))

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
            # Keep pending so the cycle retries when free collateral / dust pool opens up.
            # Do not mark skipped: wallet-locked cover is temporary, not a terminal skip.
            if live and str(group.profit_sweep_status or "").lower() != "pending":
                group.profit_sweep_status = "pending"
            return {
                "action": "covered_call_profit_sweep_skipped",
                "group_id": group.group_id,
                "reason": "amount_below_min_or_unavailable",
                "instrument_name": instrument_name,
                "amount": format_decimal(amount, 8),
                "profit_sweep_status": group.profit_sweep_status or None,
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

    @staticmethod
    def _csp_premium_swap_instrument(currency: str) -> str:
        return f"{currency.upper()}_USDC"

    def _pending_csp_premium_swap_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """Swap each entered CSP's net premium into native spot when target=spot."""
        if not self._is_covered_call_strategy():
            return []
        if not self.config.covered_call_itm_to_cash_secured_enabled:
            return []
        from ..csp_premium_swap_ops import (
            csp_premium_swap_ready,
            csp_premium_swap_target_is_spot,
            schedule_csp_premium_swap,
        )

        if not csp_premium_swap_target_is_spot(self.config.covered_call_csp_premium_target):
            return []

        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if not group.is_cash_secured_group():
                continue
            status = str(group.csp_premium_swap_status or "").lower()
            if status in {"filled", "skipped"}:
                continue
            ready, reason = csp_premium_swap_ready(group)
            if not ready:
                if live and reason == "no_realized_premium":
                    group.csp_premium_swap_status = "skipped"
                    group.csp_premium_swap_reason = "no_realized_premium"
                    actions.append(
                        {
                            "action": "csp_premium_swap_skipped",
                            "group_id": group.group_id,
                            "reason": reason,
                            "csp_premium_swap_status": "skipped",
                        }
                    )
                elif not live and reason in {"no_premium", "no_realized_premium", "not_closed_yet"}:
                    actions.append(
                        {
                            "action": "csp_premium_swap_skipped",
                            "group_id": group.group_id,
                            "reason": reason,
                        }
                    )
                continue
            if live and status not in {"pending", "submitted"}:
                schedule_csp_premium_swap(group, reason="csp_premium_to_spot_after_close")
            actions.append(self._execute_csp_premium_swap(context, group, live=live))
        return actions

    def _execute_csp_premium_swap(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> dict[str, Any]:
        from ..csp_premium_swap_ops import (
            apply_csp_premium_swap_fill,
            csp_premium_net_usdc,
            csp_premium_swap_base_filled_from_trades,
            csp_premium_swap_order_label,
            csp_premium_swap_remaining_usdc,
            csp_premium_swap_spent_usdc,
            is_csp_premium_swap_below_min_notional,
            mark_csp_premium_swap_dust_complete,
        )
        from ..wallet_ops import spot_buy_quote_spent_from_trades

        premium_usdc = csp_premium_net_usdc(group)
        remaining_usdc = csp_premium_swap_remaining_usdc(group)
        instrument_name = self._csp_premium_swap_instrument(group.currency)
        payload: dict[str, Any] = {
            "action": "csp_premium_swap" if live else "csp_premium_swap_preview",
            "group_id": group.group_id,
            "reason": group.csp_premium_swap_reason or "csp_premium_to_spot_after_close",
            "instrument_name": instrument_name,
            "amount_usdc": format_decimal(remaining_usdc, 4),
            "premium_usdc": format_decimal(premium_usdc, 4),
            "realized_usdc": format_decimal(premium_usdc, 4),
            "already_spent_usdc": format_decimal(csp_premium_swap_spent_usdc(group), 4),
            "order_type": self.config.covered_call_spot_order_type,
            "live": live,
        }
        if premium_usdc <= 0:
            payload["action"] = "csp_premium_swap_skipped"
            payload["reason"] = "no_premium"
            return payload
        if remaining_usdc <= 0:
            group.csp_premium_swap_status = "filled"
            payload["action"] = "csp_premium_swap_skipped"
            payload["reason"] = "premium_already_swapped"
            payload["csp_premium_swap_status"] = group.csp_premium_swap_status
            return payload
        if not live:
            return payload

        # If a prior attempt left an order id, credit any exchange fills first so a
        # timed-out-but-filled buy cannot be retried for the full premium again.
        prior_order_id = str(group.csp_premium_swap_order_id or "").strip()
        if prior_order_id and csp_premium_swap_spent_usdc(group) <= 0:
            try:
                prior_trades = list(self.client.get_user_trades_by_order(prior_order_id) or [])
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "csp premium swap: prior order %s trades unavailable (group=%s); prior fills not credited: %s",
                    prior_order_id,
                    group.group_id,
                    exc,
                )
                prior_trades = []
            prior_native = csp_premium_swap_base_filled_from_trades(prior_trades)
            prior_spent = spot_buy_quote_spent_from_trades(prior_trades, quote_currency="USDC")
            if prior_spent > 0 or prior_native > 0:
                remaining_usdc = apply_csp_premium_swap_fill(group, spent_usdc=prior_spent, native_bought=prior_native)
                payload["already_spent_usdc"] = format_decimal(csp_premium_swap_spent_usdc(group), 4)
                payload["amount_usdc"] = format_decimal(remaining_usdc, 4)
                if remaining_usdc <= 0:
                    payload["action"] = "csp_premium_swap_skipped"
                    payload["reason"] = "reconciled_prior_order"
                    payload["csp_premium_swap_status"] = group.csp_premium_swap_status
                    return payload

        # Never dip into the put's reserved assignment margin: cap the spend to the
        # USDC book's free funds (Deribit already excludes the short put's IM).
        usdc = context.summaries.get("USDC") or self._account_summaries_by_currency().get("USDC")
        usdc_free = Decimal("0")
        if usdc is not None:
            usdc_free = max(usdc.available_funds, usdc.available_withdrawal_funds, Decimal("0"))
        spend = min(remaining_usdc, usdc_free)
        if spend <= 0:
            # Keep pending so a later cycle retries when USDC frees up.
            if str(group.csp_premium_swap_status or "").lower() != "pending":
                group.csp_premium_swap_status = "pending"
            payload["action"] = "csp_premium_swap_skipped"
            payload["reason"] = "usdc_unavailable"
            payload["csp_premium_swap_status"] = group.csp_premium_swap_status
            return payload

        group.csp_premium_swap_status = "submitted"
        group.csp_premium_swap_instrument_name = instrument_name
        label = csp_premium_swap_order_label(self.config.order_label_prefix, group)
        try:
            from ..wallet_ops import trade_spot

            result = trade_spot(
                self.config,
                self.client,
                from_currency="USDC",
                to_currency=group.currency,
                amount=format_decimal(spend, 4),
                instrument_name=instrument_name,
                order_type=self.config.covered_call_spot_order_type,
                live=True,
                label=label,
            )
        except Exception as exc:
            if is_csp_premium_swap_below_min_notional(exc):
                # Remainder cannot buy one min lot — keep leftover USDC, stop retrying.
                mark_csp_premium_swap_dust_complete(group, reason=str(exc))
                LOGGER.info(
                    "csp_premium_swap dust_below_min group=%s remaining_usdc=%s",
                    group.group_id,
                    format_decimal(remaining_usdc, 4),
                )
                payload["action"] = "csp_premium_swap_dust_omitted"
                payload["reason"] = str(exc)
                payload["csp_premium_swap_status"] = group.csp_premium_swap_status
                return payload
            group.csp_premium_swap_status = "failed"
            group.csp_premium_swap_reason = f"csp_premium_to_spot: {exc}"
            LOGGER.exception("csp_premium_swap failed group=%s", group.group_id)
            payload["action"] = "csp_premium_swap_failed"
            payload["reason"] = str(exc)
            payload["csp_premium_swap_status"] = group.csp_premium_swap_status
            return payload

        if result.get("action") == "trade_spot_skipped":
            skip_reason = result.get("reason") or "skipped"
            if is_csp_premium_swap_below_min_notional(skip_reason):
                mark_csp_premium_swap_dust_complete(group, reason=str(skip_reason))
                payload["action"] = "csp_premium_swap_dust_omitted"
                payload["reason"] = skip_reason
                payload["csp_premium_swap_status"] = group.csp_premium_swap_status
                return payload
            group.csp_premium_swap_status = "pending"
            payload["action"] = "csp_premium_swap_skipped"
            payload["reason"] = skip_reason
            payload["csp_premium_swap_status"] = group.csp_premium_swap_status
            return payload

        response = result.get("response")
        trades = response.get("trades") if isinstance(response, dict) else []
        order_id = result.get("order_id")
        if order_id:
            group.csp_premium_swap_order_id = str(order_id)
        order_state = str(result.get("order_state") or "").lower()
        native = csp_premium_swap_base_filled_from_trades(trades)
        spent = spot_buy_quote_spent_from_trades(trades or [], quote_currency="USDC")
        if order_state in {"cancelled", "rejected"} and native <= 0 and spent <= 0:
            group.csp_premium_swap_status = "failed"
        elif spent > 0 or native > 0:
            apply_csp_premium_swap_fill(group, spent_usdc=spent, native_bought=native)
        elif order_state == "filled" or (bool(order_id) and order_state in {"", "open", "filled"}):
            # Some responses omit trade legs; credit the requested quote spend once.
            apply_csp_premium_swap_fill(group, spent_usdc=spend, native_bought=Decimal("0"))
        else:
            # Exchange accepted the order but returned no fills yet — keep pending
            # and reconcile via order_id next cycle instead of re-buying premium.
            group.csp_premium_swap_status = "pending"
        payload["csp_premium_swap_status"] = group.csp_premium_swap_status
        payload["csp_premium_swap_order_id"] = group.csp_premium_swap_order_id or None
        payload["native_bought"] = format_decimal(native, 8)
        payload["usdc_spent"] = format_decimal(spent if spent > 0 else spend, 4)
        payload["remaining_usdc"] = format_decimal(csp_premium_swap_remaining_usdc(group), 4)
        payload["response"] = response
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
        from ..spot_exit_ops import pending_spot_exit_remaining_native

        reserved = self._reserved_covered_call_quantity(state, ccy)
        pending_exit = pending_spot_exit_remaining_native(state.groups, ccy)
        return max(summary.equity - reserved - pending_exit, Decimal("0"))

    def _covered_call_spot_exit_blocks_entry(self, state: StrategyState, currency: str) -> bool:
        from ..spot_exit_ops import spot_exit_blocks_new_covered_call

        if not self._is_covered_call_strategy():
            return False
        return spot_exit_blocks_new_covered_call(state.groups, currency)

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
        # CSP is held to assignment and must not pin portfolio cooldown.
        if not any(
            not self._is_covered_call_group(group) and not group.is_cash_secured_group()
            for group in self._open_groups(state)
        ):
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
            if book == "USDT":
                state.cooldown_until_ms_by_book.pop(book, None)
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
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("covered_call itm: no index for %s: %s", group.short_instrument_name, exc)
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

    def _cash_secured_put_itm(self, group: TradeGroup, context: RuntimeContext) -> bool:
        return self._cash_secured_put_itm_from_cache(group, context.orderbook_cache)

    def _manage_cash_secured_group(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """Wheel CSP: OTM may active-roll; ITM may self-assign. The two paths are exclusive."""
        from ..cash_secured_ops import (
            cash_secured_self_assign_liquidity_ok,
            cash_secured_self_assign_ready,
        )

        itm = self._cash_secured_put_itm(group, context)
        group.itm_defense_streak = group.itm_defense_streak + 1 if itm else 0
        if not itm:
            return self._maybe_cash_secured_active_roll(context, group, live=live)
        if not self.config.covered_call_itm_to_cash_secured_enabled:
            return []
        if not self.config.covered_call_csp_self_assign_enabled:
            return []

        index_price = self._currency_index_price(group.currency, context.orderbook_cache)
        if index_price <= 0:
            try:
                index_price = self._get_orderbook(group.short_instrument_name, context.orderbook_cache).index_price
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("cash_secured self-assign: no index for %s: %s", group.short_instrument_name, exc)
                index_price = Decimal("0")
        ready, gate_reason = cash_secured_self_assign_ready(
            index_price=index_price,
            strike=group.short_strike,
            quantity=group.quantity,
            current_debit=group.current_debit,
            dte_days=group.dte_days,
            itm_buffer_pct=self.config.covered_call_itm_buffer_pct,
            max_dte=self.config.covered_call_csp_self_assign_max_dte,
            max_tv_pct=self.config.covered_call_csp_self_assign_max_tv_pct,
        )
        if not ready:
            return []

        try:
            book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(
                "cash_secured self-assign: orderbook unavailable for %s (group=%s): %s",
                group.short_instrument_name,
                group.group_id,
                exc,
            )
            return []
        liquid, liq_reason = cash_secured_self_assign_liquidity_ok(
            spread_ratio=book.spread_ratio,
            best_bid_price=book.best_bid_price,
            best_ask_price=book.best_ask_price,
            best_ask_amount=book.best_ask_amount,
            quantity=group.quantity,
            max_spread_ratio=self.config.covered_call_csp_self_assign_max_spread_ratio,
        )
        if not liquid:
            return []

        # Optimistic post-close USDC: free now + cash-secured notional − put buyback.
        # Spot cover costs ~index×qty; if even this cannot fund it, hold to expiry.
        usdc = context.summaries.get("USDC")
        usdc_free = Decimal("0")
        if usdc is not None:
            usdc_free = max(usdc.available_funds, usdc.available_withdrawal_funds, Decimal("0"))
        put_cost = max(group.current_debit, Decimal("0"))
        freed = max(group.short_strike * group.quantity, Decimal("0"))
        spot_need = index_price * group.quantity * Decimal("1.01")
        if index_price > 0 and usdc_free + freed < put_cost + spot_need:
            return []

        confirm = self.config.covered_call_csp_self_assign_confirm_cycles
        if confirm is None:
            confirm = self.config.covered_call_itm_confirm_cycles
        if confirm is None:
            confirm = self.config.defense_confirm_cycles
        if group.itm_defense_streak < max(int(confirm or 1), 1):
            return []

        actions = self._close_group(context, group, reason="csp_self_assign", live=live)
        if not live:
            actions.append(
                {
                    "action": "cash_secured_self_assign_preview",
                    "group_id": group.group_id,
                    "gate": gate_reason,
                    "liquidity": liq_reason,
                    "spread_ratio": format_decimal(book.spread_ratio, 4),
                    "index_price": format_decimal(index_price, 4),
                    "strike": format_decimal(group.short_strike, 4),
                    "dte_days": format_decimal(group.dte_days, 4),
                    "current_debit": format_decimal(group.current_debit, 8),
                }
            )
            return actions
        if group.status != "closed":
            return actions

        group.spot_restore_status = "pending"
        group.spot_restore_instrument_name = f"{group.currency.upper()}_USDC"
        group.spot_restore_reason = "cash_secured_self_assign"
        self._telegram_alert(
            "CSP self-assign → restoring cover",
            body=(
                f"group={group.group_id} {group.short_instrument_name} "
                f"gate={gate_reason} spread={format_decimal(book.spread_ratio, 4)} "
                f"index={format_decimal(index_price, 2)}"
            ),
            event_key=f"csp_self_assign:{self._journal_scope_key()}:{group.group_id}",
            level="info",
        )
        actions.append(
            {
                "action": "cash_secured_self_assign",
                "group_id": group.group_id,
                "gate": gate_reason,
                "liquidity": liq_reason,
                "spot_restore_status": "pending",
            }
        )
        return actions

    def _maybe_cash_secured_active_roll(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """OTM CSP: buy back before expiry and sell a higher daily-yield put.

        Same or earlier expiry is allowed. Gate order (first failure wins):
        disabled → dte_too_short / dte_out_of_window → tv_too_thin →
        illiquid_close → illiquid_replacement → daily_yield_not_higher.
        ITM never enters this method.
        """
        from ..cash_secured_ops import (
            cash_secured_active_roll_daily_usdc,
            cash_secured_active_roll_dte_reason,
            cash_secured_active_roll_fee_edge,
            cash_secured_active_roll_tv_ratio,
            cash_secured_roll_blocked,
            cash_secured_self_assign_liquidity_ok,
            cash_secured_strike_bounds,
            cash_secured_target_native,
        )
        from ..fees import option_trade_fee_usdc
        from ..spot_restore_ops import covered_call_cover_native

        def _payload(**extra: Any) -> dict[str, Any]:
            row: dict[str, Any] = {
                "action": "cash_secured_active_roll",
                "group_id": group.group_id,
                "would_place": False,
                "close_instrument": group.short_instrument_name,
            }
            row.update(extra)
            return row

        if not self.config.covered_call_itm_to_cash_secured_enabled:
            return []
        if not self.config.covered_call_csp_active_roll_enabled:
            return []

        dte_why = cash_secured_active_roll_dte_reason(
            dte_days=group.dte_days,
            min_dte=Decimal(self.config.covered_call_csp_active_roll_min_dte),
            max_dte=Decimal(self.config.covered_call_csp_active_roll_max_dte),
        )
        if dte_why:
            return [_payload(reason=dte_why)]

        try:
            close_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(
                "cash_secured active roll: orderbook unavailable for %s (group=%s): %s",
                group.short_instrument_name,
                group.group_id,
                exc,
            )
            return [_payload(reason="illiquid_close")]

        index_price = self._currency_index_price(group.currency, context.orderbook_cache)
        if index_price <= 0:
            index_price = close_book.index_price
        close_ask = close_book.best_ask_price
        if close_ask <= 0 or group.quantity <= 0:
            return [_payload(reason="illiquid_close")]
        close_debit = close_ask * group.quantity
        tv_ratio = cash_secured_active_roll_tv_ratio(
            index_price=index_price,
            strike=group.short_strike,
            quantity=group.quantity,
            current_debit=close_debit,
            entry_credit=group.entry_credit,
        )
        if tv_ratio < self.config.covered_call_csp_active_roll_min_tv_ratio:
            return [_payload(reason="tv_too_thin", tv_ratio=format_decimal(tv_ratio, 4))]

        liquid, liq_why = cash_secured_self_assign_liquidity_ok(
            spread_ratio=close_book.spread_ratio,
            best_bid_price=close_book.best_bid_price,
            best_ask_price=close_book.best_ask_price,
            best_ask_amount=close_book.best_ask_amount,
            quantity=group.quantity,
            max_spread_ratio=self.config.covered_call_csp_self_assign_max_spread_ratio,
        )
        if not liquid:
            return [_payload(reason="illiquid_close", liquidity=liq_why)]

        parent_id = str(group.cash_secured_from_group_id or "").strip()
        parent = next((item for item in context.state.groups if str(item.group_id) == parent_id), None)
        if parent is None:
            return [_payload(reason="parent_missing")]
        blocked, block_why = cash_secured_roll_blocked(parent, context.state.groups)
        if blocked:
            return [_payload(reason=block_why)]

        if context.regime_by_currency.get(group.currency, RiskRegime.NORMAL) is RiskRegime.CRISIS:
            return []
        if self._cash_secured_blocked_by_hard_derisk(context):
            return []

        min_strike, max_strike = cash_secured_strike_bounds(
            parent.short_strike,
            self.config.covered_call_csp_strike_floor_pct,
        )

        usdc = context.summaries.get("USDC")
        usdc_free = Decimal("0")
        usdc_equity = Decimal("0")
        usdc_mm = Decimal("0")
        if usdc is not None:
            usdc_free = max(usdc.available_funds, usdc.available_withdrawal_funds, Decimal("0"))
            usdc_equity = usdc.equity
            usdc_mm = usdc.maintenance_margin
        close_fee = option_trade_fee_usdc(
            index_price=index_price,
            premium=close_ask,
            quantity=group.quantity,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
            base_currency=group.currency,
            quote_currency="USDC",
            settlement_currency="USDC",
            fee_discount_rate=self._option_fee_discount_rate_at(),
        )
        freed = max(group.short_strike * group.quantity, Decimal("0"))
        usdc_available = usdc_free + freed - close_debit - close_fee
        if usdc_available < 0:
            usdc_available = Decimal("0")
        if usdc_equity < usdc_available:
            usdc_equity = usdc_available

        sold = cash_secured_target_native(parent, context.state.groups)
        if sold <= 0:
            return [_payload(reason="cover_restored")]
        cover = covered_call_cover_native(parent)
        qty_cap = group.quantity
        if cover > 0:
            qty_cap = min(qty_cap, cover)

        ranked = self._rank_itm_cash_secured_candidates(
            context,
            parent,
            sold_native=sold,
            usdc_available=usdc_available,
            min_strike=min_strike,
            max_strike=max_strike,
            summary_equity=usdc_equity,
            summary_maintenance_margin=usdc_mm,
            quantity_cap=qty_cap,
            exclude_instrument=group.short_instrument_name,
        )
        if not ranked:
            return [_payload(reason="illiquid_replacement")]

        picked = self._pick_csp_daily_yield_candidate(
            context,
            ranked,
            hold_credit=close_debit,
            hold_dte=group.dte_days,
            close_fee=close_fee,
            index_price=index_price,
            amortize_close_fee=True,
        )
        hold_daily = cash_secured_active_roll_daily_usdc(credit=close_debit, dte_days=group.dte_days)
        if picked is None:
            return [
                _payload(
                    reason="daily_yield_not_higher",
                    hold_daily=format_decimal(hold_daily, 8),
                    tv_ratio=format_decimal(tv_ratio, 4),
                )
            ]
        candidate, numbers = picked
        new_credit = Decimal(str(numbers["new_credit"]))
        open_fee = Decimal(str(numbers["open_fee"]))
        net, _edge_ok = cash_secured_active_roll_fee_edge(
            new_credit=new_credit,
            close_debit=close_debit,
            close_fee=close_fee,
            open_fee=open_fee,
            min_net_usdc=self.config.covered_call_csp_active_roll_min_net_usdc,
        )
        numbers["net_edge"] = format_decimal(net, 8)
        numbers["tv_ratio"] = format_decimal(tv_ratio, 4)

        if not live:
            actions = self._close_group(context, group, reason="csp_active_roll", live=False)
            actions.append(_payload(reason="", would_place=True, **numbers))
            return actions

        actions = self._close_group(context, group, reason="csp_active_roll", live=True)
        if group.status != "closed":
            actions.append(_payload(reason="close_incomplete", would_place=True, **numbers))
            return actions

        parent.cash_secured_reason = "active_roll_entry_pending"
        parent.cash_secured_instrument_name = candidate.short_leg.instrument_name
        entry = self._execute_itm_cash_secured_entry(context, parent, candidate, live=True)
        actions.append(entry)
        if entry.get("action") == "cash_secured_entered":
            parent.cash_secured_reason = "csp_active_roll"
            self._telegram_alert(
                "CSP active roll filled",
                body=(
                    f"closed={group.group_id} {group.short_instrument_name} "
                    f"→ {candidate.short_leg.instrument_name} qty={format_decimal(candidate.quantity, 4)}"
                ),
                event_key=f"csp_active_roll:{self._journal_scope_key()}:{group.group_id}",
                level="info",
            )
            actions.append(_payload(reason="", would_place=True, **numbers))
            return actions

        LOGGER.warning(
            "cash_secured active roll: close filled but entry failed group=%s replacement=%s",
            group.group_id,
            candidate.short_leg.instrument_name,
        )
        self._telegram_alert(
            "CSP active roll: entry failed after close",
            body=(
                f"closed={group.group_id} {group.short_instrument_name} "
                f"replacement={candidate.short_leg.instrument_name} "
                f"USDC parked; next cycle retries entry only"
            ),
            event_key=f"csp_active_roll_entry_fail:{self._journal_scope_key()}:{group.group_id}",
            level="warning",
        )
        actions.append(_payload(reason="entry_unfilled", would_place=True, **numbers))
        return actions

    def _cash_secured_put_itm_from_cache(
        self,
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
        close_index_usd: Decimal | None = None,
    ) -> bool:
        from ..cash_secured_ops import cash_secured_put_is_itm

        index_price = close_index_usd if close_index_usd and close_index_usd > 0 else Decimal("0")
        if index_price <= 0:
            index_price = self._currency_index_price(group.currency, orderbook_cache)
        if index_price <= 0 and group.close_index_usd > 0:
            index_price = group.close_index_usd
        return cash_secured_put_is_itm(
            index_price=index_price,
            strike=group.short_strike,
            buffer_pct=self.config.covered_call_itm_buffer_pct,
        )

    def _covered_call_spot_instrument(self, currency: str) -> str:
        quote = "USDC" if self.config.covered_call_itm_to_cash_secured_enabled else "USDT"
        return f"{currency.upper()}_{quote}"

    def _cash_secured_owns_itm_group(self, group: TradeGroup) -> bool:
        """True when the wheel (not auto-restore) owns this ITM cover sale."""
        if not self.config.covered_call_itm_to_cash_secured_enabled:
            return False
        if not self._is_covered_call_group(group):
            return False
        return True

    def _cash_secured_blocked_by_hard_derisk(self, context: RuntimeContext) -> bool:
        """Only the USDC collateral book can skip a cash-secured put.

        ITM cover sales crash the native BTC/ETH book (and USDT parking can
        look like a wipeout). Those drawdowns must not block the wheel put;
        crisis regime on the underlying is a separate skip.
        """
        by_book = context.snapshot.hard_derisk_by_book or {}
        return bool(by_book.get("USDC"))

    def _spot_min_trade_amount(self, instrument_name: str, currency: str) -> tuple[Decimal, Decimal]:
        for lookup_currency in ("USDT", "USDC", currency.upper()):
            try:
                rows = self.client.get_instruments(lookup_currency, kind="spot", expired=False)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("spot min trade amount: get_instruments(%s, spot) failed: %s", lookup_currency, exc)
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
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("covered_call: no index price via %s: %s", group.short_instrument_name, exc)
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
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("covered_call: instrument metadata unavailable for %s: %s", group.short_instrument_name, exc)
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
        from ..spot_exit_ops import spot_exit_filled_native, spot_restore_blocks_exit_remainder

        cover = group.covered_underlying_quantity if group.covered_underlying_quantity > 0 else group.quantity
        if cover <= 0:
            return {
                "amount": Decimal("0"),
                "cover": Decimal("0"),
                "premium": Decimal("0"),
                "premium_available": Decimal("0"),
                "include_premium": False,
                "settlement_loss": Decimal("0"),
                "settlement_loss_source": "none",
                "already_filled": Decimal("0"),
                "remaining_structural": Decimal("0"),
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
        premium = covered_call_spot_exit_premium_native(group)
        # ITM spot exit always sells cover−settle only. Premium stays as spot profit
        # and is shown / swept via Profit swap (never folded into the cover sale).
        include_premium = False
        already_filled = spot_exit_filled_native(group)

        structural = covered_call_spot_exit_target_native(
            cover=cover,
            premium_native=premium,
            settlement_loss=settlement_loss,
            settlement_loss_source=settlement_loss_source,
            include_premium=include_premium,
        )
        remaining_structural = max(structural - already_filled, Decimal("0"))
        # Restore-to-cover already reconstituted inventory; do not sell the
        # structural remainder (premium fold) a second time into restored cover.
        if spot_restore_blocks_exit_remainder(group):
            remaining_structural = Decimal("0")
        target = remaining_structural
        folded_premium = premium if include_premium else Decimal("0")

        summary = summaries.get(group.currency)
        if live:
            summary = self._account_summaries_by_currency().get(group.currency, summary)
        if summary is not None:
            available = max(summary.available_funds, summary.available_withdrawal_funds, summary.balance)
            if available <= 0:
                return {
                    "amount": Decimal("0"),
                    "cover": cover,
                    "premium": folded_premium,
                    "premium_available": premium,
                    "include_premium": include_premium,
                    "settlement_loss": settlement_loss,
                    "settlement_loss_source": settlement_loss_source,
                    "already_filled": already_filled,
                    "remaining_structural": remaining_structural,
                }
            target = min(target, available)

        instrument_name = self._covered_call_spot_instrument(group.currency)
        contract_size, min_trade_amount = self._spot_min_trade_amount(instrument_name, group.currency)
        aligned = align_option_order_amount(target, contract_size, min_trade_amount)
        amount = aligned if contract_size > 0 or min_trade_amount > 0 else target
        return {
            "amount": amount,
            "cover": cover,
            "premium": folded_premium,
            "premium_available": premium,
            "include_premium": include_premium,
            "settlement_loss": settlement_loss,
            "settlement_loss_source": settlement_loss_source,
            "already_filled": already_filled,
            "remaining_structural": remaining_structural,
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
