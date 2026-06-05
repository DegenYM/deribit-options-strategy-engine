from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..exceptions import ExchangeError
from ..models import (
    NakedPutCandidate,
    OptionInstrument,
    OrderBookSnapshot,
    RiskRegime,
    SpreadLeg,
    TradeGroup,
)
from ..utils import (
    align_option_order_amount,
    format_decimal,
    is_post_only_reject,
    to_decimal,
    utc_now_ms,
)
from .context import (
    LOGGER,
    RuntimeContext,
)


class EntryMixin:
    def enter_best(
        self,
        *,
        currencies: tuple[str, ...] | None = None,
        live: bool = False,
    ) -> dict[str, Any]:
        context = self._load_runtime(live=live)
        candidates = self._filter_enterable_candidates(
            context,
            self._scan_candidates(context, currencies=currencies, top_n=1),
        )
        result = self._enter_best_from_candidates(context, candidates=candidates, live=live)
        if result.get("group") is not None:
            context.state.groups.append(result["group"])
        if live:
            self._persist_trade_journal_result(result)
        self.state_store.save(context.state)
        return result

    def _enter_best_from_candidates(
        self,
        context: RuntimeContext,
        *,
        candidates: list[NakedPutCandidate],
        live: bool,
    ) -> dict[str, Any]:
        if not candidates:
            return {
                "action": "no_candidate",
                "regime": context.snapshot.regime.value,
                "portfolio": context.snapshot.to_dict(),
            }

        candidate = candidates[0]
        if self._naked_candidate_matches_open_group(context.state, candidate):
            return {
                "action": "entry_skipped",
                "reason": "duplicate_open_group",
                "candidate": candidate.to_dict(),
            }
        entry_book = (candidate.collateral_currency or candidate.currency or "USDC").upper()
        if self._book_entry_cooldown_active(context.state, entry_book):
            return {
                "action": "entry_skipped",
                "reason": "entry_cooldown_active",
                "book": entry_book,
                "cooldown_minutes": self.config.entry_cooldown_minutes,
                "candidate": candidate.to_dict(),
            }
        group_id = self._next_group_id(context.state)
        labels = self._spread_labels(candidate.currency, group_id)
        if not live:
            strategy = candidate.strategy or "naked_short"
            dry_action = f"dry_run_enter_{strategy}"
            if strategy == "naked_short":
                dry_action = f"dry_run_enter_naked_{candidate.option_type}"
            requests: dict[str, Any] = {
                "short_leg": self._entry_naked_short_request(
                    candidate, labels["short"], quantity=candidate.quantity, aggressive=False
                ),
            }
            execution_mode = "naked_short_post_only"
            if strategy == "bull_put_spread" and candidate.long_leg is not None:
                requests = {
                    "long_leg": self._entry_long_buy_request(candidate, labels["long"], quantity=candidate.quantity),
                    "short_leg": requests["short_leg"],
                }
                execution_mode = "bull_put_spread_buy_protection_first"
            return {
                "action": dry_action,
                "candidate": candidate.to_dict(),
                "group_id": group_id,
                "requests": requests,
                "execution_mode": execution_mode,
            }
        if (candidate.strategy or "") == "bull_put_spread":
            return self._execute_bull_put_spread_entry(context, candidate, group_id)
        return self._execute_naked_put_entry(context, candidate, group_id)

    def _entry_naked_short_request(
        self,
        candidate: NakedPutCandidate,
        label: str,
        *,
        aggressive: bool = False,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> dict[str, Any]:
        quantity = quantity or candidate.quantity
        instrument = self._entry_leg_instrument(candidate.currency, candidate.short_leg, quantity)
        book = OrderBookSnapshot(
            instrument_name=candidate.short_leg.instrument_name,
            best_bid_price=candidate.short_leg.best_bid_price,
            best_bid_amount=quantity,
            best_ask_price=candidate.short_leg.best_ask_price,
            best_ask_amount=quantity,
            mark_price=candidate.screening_mark,
            index_price=candidate.short_leg.index_price,
            delta=candidate.short_leg.delta,
            iv=Decimal("0"),
            open_interest=Decimal("0"),
        )
        if aggressive:
            return {
                "instrument_name": candidate.short_leg.instrument_name,
                "amount": format_decimal(quantity, 8),
                "price": format_decimal(
                    candidate.short_leg.entry_price
                    if candidate.short_leg.entry_price > 0
                    else self.strategy.sell_taker_price(instrument, book),
                    8,
                ),
                "label": label,
                "time_in_force": "immediate_or_cancel",
            }
        return {
            "instrument_name": candidate.short_leg.instrument_name,
            "amount": format_decimal(quantity, 8),
            "price": format_decimal(price if price is not None else self.strategy.sell_mid_price(instrument, book), 8),
            "label": label,
            "time_in_force": "good_til_cancelled",
            "post_only": True,
            "reject_post_only": True,
        }

    def _entry_long_buy_request(
        self,
        candidate: NakedPutCandidate,
        label: str,
        *,
        quantity: Decimal,
    ) -> dict[str, Any]:
        if candidate.long_leg is None:
            raise ExchangeError("bull put spread entry requires a long_leg")
        return {
            "instrument_name": candidate.long_leg.instrument_name,
            "amount": format_decimal(quantity, 8),
            "price": format_decimal(candidate.long_leg.entry_price, 8),
            "label": label,
            "time_in_force": "immediate_or_cancel",
        }

    def _execute_repriced_naked_short(
        self,
        context: RuntimeContext,
        candidate: NakedPutCandidate,
        *,
        label: str,
        quantity: Decimal,
    ) -> dict[str, Any]:
        requests: list[dict[str, Any]] = []
        responses: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        filled_amount = Decimal("0")
        waited = 0
        latest_candidate: NakedPutCandidate | None = candidate
        locked_short = candidate.short_leg.instrument_name
        reason = "timed_out"
        while filled_amount < quantity and waited < self.config.short_entry_wait_seconds:
            remaining = quantity - filled_amount
            inst = self._find_instrument(context, locked_short)
            book = self._get_orderbook(locked_short, context.orderbook_cache)
            summ = context.summaries.get(candidate.collateral_currency)
            if summ is None:
                reason = "no_summary"
                break
            if (candidate.strategy or "") == "covered_call":
                refreshed, refresh_fail = self.strategy.refresh_covered_call_candidate(
                    instrument=inst,
                    book=book,
                    regime=context.regime_by_currency.get(candidate.currency, RiskRegime.CRISIS),
                    collateral_currency=candidate.collateral_currency,
                    currency=candidate.currency,
                    quantity=remaining,
                    summary_equity=summ.equity,
                )
            else:
                im_by = self._naked_im_by_expiry(
                    context.state,
                    candidate.collateral_currency,
                    orderbook_cache=context.orderbook_cache,
                )
                exp_im = im_by.get(inst.expiration_timestamp_ms, Decimal("0"))
                refreshed, refresh_fail = self.strategy.refresh_naked_candidate(
                    option_type=candidate.option_type,
                    instrument=inst,
                    book=book,
                    regime=context.regime_by_currency.get(candidate.currency, RiskRegime.CRISIS),
                    summary_equity=summ.equity,
                    summary_maintenance_margin=summ.maintenance_margin,
                    collateral_currency=candidate.collateral_currency,
                    currency=candidate.currency,
                    quantity=remaining,
                    existing_im_for_expiry=exp_im,
                )
            if refreshed is None or refreshed.net_apr < self.config.min_net_apr:
                reason = "candidate_failed_recheck"
                if refresh_fail:
                    reason = f"candidate_failed_recheck:{refresh_fail}"
                elif refreshed is not None:
                    reason = "candidate_failed_recheck:net_apr_below_min"
                break
            latest_candidate = refreshed
            aggressive = len(requests) > 0
            request = self._entry_naked_short_request(
                latest_candidate, label, quantity=remaining, aggressive=aggressive
            )
            window = min(self.config.order_poll_seconds, self.config.short_entry_wait_seconds - waited)
            try:
                response = self._place_entry_order(context, "sell", request)
            except ExchangeError as exc:
                if not is_post_only_reject(exc):
                    raise
                LOGGER.info(
                    "entry post_only rejected on %s price=%s; repricing (%s)",
                    locked_short,
                    request.get("price"),
                    exc,
                )
                requests.append(request)
                waited += window
                continue
            state = self._await_entry_order(response, max_wait_seconds=window)
            requests.append(request)
            responses.append(state)
            trades.extend(self._order_trades(state))
            newly_filled = self._response_filled_amount(state)
            filled_amount += newly_filled
            if filled_amount >= quantity:
                reason = "filled"
                break
            waited += window
        if filled_amount <= 0 and reason == "timed_out":
            reason = "unfilled"
        return {
            "candidate": latest_candidate,
            "filled_amount": filled_amount,
            "requests": requests,
            "responses": responses,
            "trades": trades,
            "first_request": requests[0] if requests else None,
            "last_response": responses[-1] if responses else None,
            "reason": reason,
        }

    def _execute_naked_put_entry(
        self,
        context: RuntimeContext,
        candidate: NakedPutCandidate,
        group_id: str,
    ) -> dict[str, Any]:
        labels = self._spread_labels(candidate.currency, group_id)
        execution = self._execute_repriced_naked_short(
            context,
            candidate,
            label=labels["short"],
            quantity=candidate.quantity,
        )
        short_request = execution["first_request"]
        short_state = execution["last_response"]
        if execution["filled_amount"] <= 0:
            return {
                "action": "entry_aborted_short_unfilled",
                "candidate": candidate.to_dict(),
                "requests": {"short_leg": short_request, "short_attempts": execution["requests"]},
                "responses": {"short_leg": short_state, "short_attempts": execution["responses"]},
                "reason": execution["reason"],
            }
        if execution["reason"] == "candidate_failed_recheck":
            return {
                "action": "entry_aborted_naked_disqualified",
                "candidate": candidate.to_dict(),
                "requests": {"short_leg": short_request, "short_attempts": execution["requests"]},
                "responses": {"short_leg": short_state, "short_attempts": execution["responses"]},
                "reason": execution["reason"],
            }
        kept_quantity = execution["filled_amount"]
        final_c = execution["candidate"] or candidate
        primary_short_average_price = self._filled_average_price(execution["responses"])
        short_instrument = self._find_instrument(context, final_c.short_leg.instrument_name)
        short_book = self._get_orderbook(final_c.short_leg.instrument_name, context.orderbook_cache)
        idx = short_book.index_price
        short_trades = execution["trades"]
        actual_net_credit, short_entry_fee, entry_fee_collateral = self._short_entry_ledger(
            premium=primary_short_average_price,
            quantity=kept_quantity,
            index_price=idx,
            trades=short_trades,
            instrument=short_instrument,
            collateral_currency=final_c.collateral_currency,
            at_timestamp_ms=utc_now_ms(),
        )
        im_per = final_c.estimated_im_total / final_c.quantity if final_c.quantity > 0 else Decimal("0")
        # ``im_per`` is already in the candidate's collateral-currency unit
        # (USDC for linear, BTC/ETH for inverse); store that raw figure so
        # scanner capacity math stays unit-clean. ``max_loss`` meanwhile
        # remains USDC-scale for reporting / risk thresholds.
        estimated_im_collateral = im_per * kept_quantity
        if final_c.collateral_currency.upper() == "USDC":
            max_loss_usdc = estimated_im_collateral
        else:
            max_loss_usdc = estimated_im_collateral * idx if idx > 0 else final_c.estimated_im_total

        group = TradeGroup(
            group_id=group_id,
            currency=final_c.currency,
            collateral_currency=final_c.collateral_currency,
            quantity=kept_quantity,
            entry_timestamp_ms=utc_now_ms(),
            expiration_timestamp_ms=final_c.short_leg.expiration_timestamp_ms,
            short_instrument_name=final_c.short_leg.instrument_name,
            short_strike=final_c.short_leg.strike,
            entry_credit=actual_net_credit,
            original_entry_credit=actual_net_credit,
            max_loss=max_loss_usdc,
            estimated_im_collateral=estimated_im_collateral,
            regime_at_entry=final_c.regime.value,
            entry_fee=short_entry_fee,
            entry_fee_collateral=entry_fee_collateral,
            short_entry_average_price=primary_short_average_price,
            entry_index_usd=idx,
            entry_net_apr=final_c.net_apr,
            short_label=labels["short"],
            hedge_label=labels["hedge"] if self.config.enable_perp_hedge else "",
            hedge_instrument_name=self._perp_instrument(final_c.currency) if self.config.enable_perp_hedge else "",
            option_type=final_c.option_type,
            strategy=final_c.strategy or "naked_short",
            covered_underlying_quantity=final_c.covered_underlying_quantity,
        )
        self._attach_open_group_stats(group)
        action_name = f"{group.strategy}_entered"
        if group.strategy == "naked_short":
            action_name = f"naked_{final_c.option_type}_entered"
        return {
            "action": action_name,
            "candidate": final_c.to_dict(),
            "group": group,
            "entry_fee": short_entry_fee,
            "execution_mode": "naked_short_post_only" if group.strategy == "naked_short" else group.strategy,
            "requests": {"short_leg": short_request, "short_attempts": execution["requests"]},
            "responses": {"short_leg": short_state, "short_attempts": execution["responses"]},
            "trades": {"short_leg": short_trades},
        }

    def _close_entry_long_remainder(
        self,
        context: RuntimeContext,
        *,
        instrument_name: str,
        quantity: Decimal,
        label: str,
    ) -> dict[str, Any]:
        if quantity <= 0:
            return self._noop_option_order_response()
        instrument = self._find_instrument(context, instrument_name)
        book = self._get_orderbook(instrument_name, context.orderbook_cache)
        amount = align_option_order_amount(quantity, instrument.contract_size, instrument.min_trade_amount)
        if amount <= 0:
            return self._noop_option_order_response()
        return self.client.place_sell_order(
            instrument_name=instrument_name,
            amount=amount,
            label=label,
            order_type="limit",
            price=self._positive_option_limit_price(instrument, self.strategy.close_sell_price(instrument, book)),
            time_in_force="immediate_or_cancel",
            reduce_only=True,
        )

    def _execute_bull_put_spread_entry(
        self,
        context: RuntimeContext,
        candidate: NakedPutCandidate,
        group_id: str,
    ) -> dict[str, Any]:
        if candidate.long_leg is None:
            return {"action": "entry_aborted_missing_long_leg", "candidate": candidate.to_dict()}
        labels = self._spread_labels(candidate.currency, group_id)
        long_request = self._entry_long_buy_request(candidate, labels["long"], quantity=candidate.quantity)
        long_response = self._place_entry_order(context, "buy", long_request)
        long_state = self._await_entry_order(long_response, max_wait_seconds=self.config.order_poll_seconds)
        long_filled = self._response_filled_amount(long_state)
        if long_filled <= 0:
            return {
                "action": "entry_aborted_long_unfilled",
                "candidate": candidate.to_dict(),
                "requests": {"long_leg": long_request},
                "responses": {"long_leg": long_state},
            }

        execution = self._execute_repriced_naked_short(
            context,
            candidate,
            label=labels["short"],
            quantity=min(candidate.quantity, long_filled),
        )
        short_request = execution["first_request"]
        short_state = execution["last_response"]
        short_filled = execution["filled_amount"]
        if short_filled <= 0:
            unwind = self._close_entry_long_remainder(
                context,
                instrument_name=candidate.long_leg.instrument_name,
                quantity=long_filled,
                label=f"{labels['long']}-abort",
            )
            return {
                "action": "entry_aborted_short_unfilled",
                "candidate": candidate.to_dict(),
                "requests": {
                    "long_leg": long_request,
                    "short_leg": short_request,
                    "short_attempts": execution["requests"],
                },
                "responses": {
                    "long_leg": long_state,
                    "short_leg": short_state,
                    "short_attempts": execution["responses"],
                    "long_unwind": unwind,
                },
                "reason": execution["reason"],
            }

        excess_long = long_filled - short_filled
        long_unwind = None
        if excess_long > 0:
            long_unwind = self._close_entry_long_remainder(
                context,
                instrument_name=candidate.long_leg.instrument_name,
                quantity=excess_long,
                label=f"{labels['long']}-excess",
            )

        kept_quantity = short_filled
        final_c = execution["candidate"] or candidate
        short_instrument = self._find_instrument(context, final_c.short_leg.instrument_name)
        long_instrument = self._find_instrument(context, candidate.long_leg.instrument_name)
        short_book = self._get_orderbook(final_c.short_leg.instrument_name, context.orderbook_cache)
        idx = short_book.index_price
        short_trades = execution["trades"]
        long_trades = self._order_trades(long_state)
        short_avg = self._filled_average_price(execution["responses"])
        long_avg = self._response_average_price(long_state)
        entry_ts = utc_now_ms()
        short_net, short_fee, short_fee_collateral = self._short_entry_ledger(
            premium=short_avg,
            quantity=kept_quantity,
            index_price=idx,
            trades=short_trades,
            instrument=short_instrument,
            collateral_currency=final_c.collateral_currency,
            at_timestamp_ms=entry_ts,
        )
        _, long_fee, long_fee_collateral = self._short_entry_ledger(
            premium=long_avg,
            quantity=kept_quantity,
            index_price=idx,
            trades=long_trades,
            instrument=long_instrument,
            collateral_currency=final_c.collateral_currency,
            at_timestamp_ms=entry_ts,
        )
        entry_fee_collateral = short_fee_collateral + long_fee_collateral
        if final_c.collateral_currency.upper() == "USDC":
            short_credit = self._premium_value_usdc(
                premium=short_avg,
                quantity=kept_quantity,
                index_price=idx,
                instrument=short_instrument,
            )
            long_debit = self._premium_value_usdc(
                premium=long_avg,
                quantity=kept_quantity,
                index_price=idx,
                instrument=long_instrument,
            )
            actual_net_credit = short_credit - long_debit - short_fee - long_fee
        else:
            gross_native = (short_avg - long_avg) * kept_quantity
            actual_net_credit = (gross_native - entry_fee_collateral) * idx if idx > 0 else Decimal("0")
        width_usdc = max(final_c.short_leg.strike - candidate.long_leg.strike, Decimal("0")) * kept_quantity
        max_loss_usdc = max(width_usdc - actual_net_credit, Decimal("0"))
        estimated_im_collateral = (
            max_loss_usdc
            if final_c.collateral_currency.upper() == "USDC"
            else (max_loss_usdc / idx if idx > 0 else Decimal("0"))
        )

        group = TradeGroup(
            group_id=group_id,
            currency=final_c.currency,
            collateral_currency=final_c.collateral_currency,
            quantity=kept_quantity,
            entry_timestamp_ms=utc_now_ms(),
            expiration_timestamp_ms=final_c.short_leg.expiration_timestamp_ms,
            short_instrument_name=final_c.short_leg.instrument_name,
            short_strike=final_c.short_leg.strike,
            entry_credit=actual_net_credit,
            original_entry_credit=actual_net_credit,
            max_loss=max_loss_usdc,
            estimated_im_collateral=estimated_im_collateral,
            regime_at_entry=final_c.regime.value,
            entry_fee=short_fee + long_fee,
            entry_fee_collateral=entry_fee_collateral,
            short_entry_average_price=short_avg,
            long_entry_average_price=long_avg,
            entry_index_usd=idx,
            entry_net_apr=final_c.net_apr,
            short_label=labels["short"],
            long_label=labels["long"],
            hedge_label=labels["hedge"] if self.config.enable_perp_hedge else "",
            hedge_instrument_name=self._perp_instrument(final_c.currency) if self.config.enable_perp_hedge else "",
            option_type="put",
            strategy="bull_put_spread",
            long_instrument_name=candidate.long_leg.instrument_name,
            long_strike=candidate.long_leg.strike,
        )
        self._attach_open_group_stats(group)
        responses: dict[str, Any] = {
            "long_leg": long_state,
            "short_leg": short_state,
            "short_attempts": execution["responses"],
        }
        if long_unwind is not None:
            responses["long_unwind"] = long_unwind
        return {
            "action": "bull_put_spread_entered",
            "candidate": final_c.to_dict(),
            "group": group,
            "entry_fee": short_fee + long_fee,
            "execution_mode": "bull_put_spread_buy_protection_first",
            "requests": {"long_leg": long_request, "short_leg": short_request, "short_attempts": execution["requests"]},
            "responses": responses,
            "trades": {"long_leg": long_trades, "short_leg": short_trades},
        }

    def _place_entry_order(self, context: RuntimeContext, direction: str, request: dict[str, Any]) -> dict[str, Any]:
        instrument = self._find_instrument(context, str(request["instrument_name"]))
        amount = align_option_order_amount(
            to_decimal(request["amount"]),
            instrument.contract_size,
            instrument.min_trade_amount,
        )
        if amount <= 0:
            raise ExchangeError(
                f"entry order amount aligned to zero for {request['instrument_name']}; "
                f"requested quantity is below exchange minimum/step"
            )
        if "reject_post_only" in request:
            reject_post_only = request["reject_post_only"]
        else:
            reject_post_only = False if request.get("post_only") is not None else None
        return self.client.place_order(
            direction=direction,
            instrument_name=request["instrument_name"],
            amount=amount,
            label=request["label"],
            order_type="limit",
            price=to_decimal(request["price"]),
            time_in_force=request["time_in_force"],
            post_only=request.get("post_only"),
            reject_post_only=reject_post_only,
            reduce_only=False,
        )

    def _entry_leg_instrument(self, currency: str, leg: SpreadLeg, quantity: Decimal) -> OptionInstrument:
        """Reconstruct a minimal ``OptionInstrument`` for repricing an entry leg.

        ``option_type`` must be inferred from the leg's instrument name (``-P`` /
        ``-C``) instead of being hardcoded to ``put``; otherwise short call
        re-prices would surface with the wrong type and break tick-grid lookups.
        """
        option_type = "call" if leg.instrument_name.upper().endswith("-C") else "put"
        return OptionInstrument(
            instrument_name=leg.instrument_name,
            base_currency=currency,
            quote_currency=leg.quote_currency,
            settlement_currency=leg.settlement_currency,
            instrument_type=leg.instrument_type,
            tick_size=leg.tick_size,
            tick_size_steps=leg.tick_size_steps,
            min_trade_amount=leg.min_trade_amount,
            contract_size=leg.contract_size,
            option_type=option_type,
            expiration_timestamp_ms=leg.expiration_timestamp_ms,
            strike=leg.strike,
            instrument_state="open",
        )

    def _await_entry_order(
        self, initial_response: dict[str, Any] | None, *, max_wait_seconds: int | None = None
    ) -> dict[str, Any]:
        if not isinstance(initial_response, dict):
            return {}
        order = self._response_order(initial_response)
        order_id = str(order.get("order_id") or "")
        state = initial_response
        waited = 0
        wait_limit = self.config.short_entry_wait_seconds if max_wait_seconds is None else max_wait_seconds
        while order_id and waited < wait_limit:
            order = self._response_order(state)
            order_state = str(order.get("order_state") or "").lower()
            if order_state in {"filled", "cancelled", "rejected"}:
                break
            step = min(self.config.order_poll_seconds, wait_limit - waited)
            if step <= 0:
                break
            self.sleep_fn(step)
            waited += step
            state = self.client.get_order_state(order_id)

        final_order = self._response_order(state)
        final_state = str(final_order.get("order_state") or "").lower()
        if order_id and final_state not in {"filled", "cancelled", "rejected"}:
            self.client.cancel_order(order_id)
            state = self.client.get_order_state(order_id)
        return state

    def _topup_existing_naked_groups(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """When groups are at capacity, use surplus margin to increase quantity of open naked groups."""
        if not self.config.enable_naked_topup:
            return []
        open_naked = list(self._open_groups(context.state))
        if not open_naked:
            return []

        orderbook_cache = context.orderbook_cache
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        actions: list[dict[str, Any]] = []

        for group in open_naked:
            currency = group.currency
            regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                continue
            collateral_ccy = self._group_collateral_currency(group)
            collateral_summary = context.summaries.get(collateral_ccy)
            if collateral_summary is None or collateral_summary.equity <= 0:
                continue
            try:
                inst = self._find_instrument(context, group.short_instrument_name)
            except KeyError:
                continue
            if inst.dte_days() < self.config.time_exit_dte:
                continue
            book = loader(group.short_instrument_name)
            im_by_exp = dict(
                self._naked_im_by_expiry(context.state, collateral_ccy, orderbook_cache=context.orderbook_cache)
            )
            exp_key = group.expiration_timestamp_ms
            own_im = self._group_im_in_collateral(group, orderbook_cache=context.orderbook_cache)
            if exp_key in im_by_exp:
                im_by_exp[exp_key] = max(im_by_exp[exp_key] - own_im, Decimal("0"))
            group_option_type = getattr(group, "option_type", "put") or "put"
            if group_option_type == "call":
                builder = self.strategy.build_naked_short_call_candidates
            else:
                builder = self.strategy.build_naked_short_put_candidates
            candidates = builder(
                [inst],
                loader,
                regime=regime,
                summary_equity=collateral_summary.equity,
                summary_maintenance_margin=collateral_summary.maintenance_margin,
                collateral_currency=collateral_ccy,
                currency=currency,
                existing_im_by_expiry=im_by_exp,
            )
            if not candidates:
                continue
            new_max_qty = candidates[0].quantity
            topup_qty = new_max_qty - group.quantity
            if topup_qty < inst.min_trade_amount:
                continue
            adjusted_exp_im = im_by_exp.get(exp_key, Decimal("0"))
            topup_candidate, _topup_refresh_detail = self.strategy.refresh_naked_candidate(
                option_type=group_option_type,
                instrument=inst,
                book=book,
                regime=regime,
                summary_equity=collateral_summary.equity,
                summary_maintenance_margin=collateral_summary.maintenance_margin,
                collateral_currency=collateral_ccy,
                currency=currency,
                quantity=topup_qty,
                existing_im_for_expiry=adjusted_exp_im,
            )
            if topup_candidate is None:
                continue

            if not live:
                actions.append(
                    {
                        "action": "dry_run_topup_naked",
                        "group_id": group.group_id,
                        "instrument": group.short_instrument_name,
                        "current_qty": group.quantity,
                        "topup_qty": topup_qty,
                        "new_total_qty": new_max_qty,
                        "candidate": topup_candidate.to_dict(),
                    }
                )
                continue

            execution = self._execute_repriced_naked_short(
                context,
                topup_candidate,
                label=group.short_label or f"trial_{currency}_{group.group_id}_short",
                quantity=topup_qty,
            )
            filled = execution["filled_amount"]
            if filled <= 0:
                actions.append(
                    {
                        "action": "topup_unfilled",
                        "group_id": group.group_id,
                        "instrument": group.short_instrument_name,
                        "topup_qty": topup_qty,
                        "reason": execution["reason"],
                    }
                )
                continue
            actions.append(
                {
                    "action": "topup_naked_executed",
                    "group_id": group.group_id,
                    "instrument": group.short_instrument_name,
                    "current_qty": group.quantity,
                    "topup_filled": filled,
                    "new_total_qty": group.quantity + filled,
                    "reason": execution["reason"],
                }
            )
            LOGGER.info(
                "topup group=%s instrument=%s filled=%s (was %s → %s)",
                group.group_id,
                group.short_instrument_name,
                format_decimal(filled, 8),
                format_decimal(group.quantity, 8),
                format_decimal(group.quantity + filled, 8),
            )

        return actions
