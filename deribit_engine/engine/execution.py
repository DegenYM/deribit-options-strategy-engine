from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..exceptions import AuthenticationError, ExchangeError
from ..exit_reasons import INCOME_EXIT_REASONS
from ..models import (
    HedgePlan,
    OpenOrder,
    OptionInstrument,
    OrderBookSnapshot,
    Position,
    TradeGroup,
)
from ..utils import (
    align_option_order_amount,
    ceil_to_step,
    floor_to_step,
    format_decimal,
    parse_exchange_price_band_limit,
    safe_div,
    to_decimal,
    utc_now_ms,
)
from .context import (
    _TELEGRAM_CLOSE_REASONS,
    LOGGER,
    RuntimeContext,
)


class ExecutionMixin:
    def close_positions(
        self,
        *,
        instruments: list[str] | None = None,
        list_only: bool = False,
        live: bool = False,
        order_type: str = "market",
        amount: Decimal | None = None,
    ) -> dict[str, Any]:
        """Close specific exchange positions (options or perps), without portfolio-wide panic logic."""
        normalized_order_type = str(order_type or "market").strip().lower()
        if normalized_order_type not in {"market", "limit"}:
            raise ExchangeError(f"close-position: unsupported order_type {order_type!r}")

        if not self.config.has_private_credentials:
            raise AuthenticationError("close-position requires private API credentials")

        positions = [Position.from_api(row) for row in self.client.get_positions(currency="any", kind="any")]
        open_positions = [item for item in positions if abs(item.size) > 0]

        if list_only:
            return {
                "action": "close-position",
                "live": live,
                "list_only": True,
                "positions": [self._position_close_row(item) for item in open_positions],
                "targets": [],
                "skipped": [],
            }

        requested = [str(name).strip() for name in (instruments or []) if str(name).strip()]
        if not requested:
            raise ExchangeError("close-position: pass --instrument NAME or use --list")

        by_name = {item.instrument_name: item for item in open_positions}
        targets: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        context = self._load_runtime(live=live) if live else None

        for instrument_name in requested:
            position = by_name.get(instrument_name)
            if position is None:
                skipped.append({"instrument_name": instrument_name, "reason": "no_open_position"})
                continue

            close_qty = abs(position.size)
            if amount is not None and amount > 0:
                close_qty = min(close_qty, amount)
            close_side = "buy" if position.direction == "sell" else "sell"
            is_option = position.kind == "option"
            is_perp = position.kind in {"future", "future_combo"} or "PERPETUAL" in position.instrument_name

            if not live:
                targets.append(
                    self._close_position_preview(
                        position,
                        close_qty=close_qty,
                        close_side=close_side,
                        order_type=normalized_order_type,
                        is_option=is_option,
                        is_perp=is_perp,
                    )
                )
                continue

            assert context is not None
            label = f"{self.config.order_label_prefix}-manual-close"
            if is_option:
                targets.append(
                    self._close_option_position_live(
                        context,
                        position=position,
                        close_qty=close_qty,
                        close_side=close_side,
                        label=label,
                        order_type=normalized_order_type,
                    )
                )
            elif is_perp:
                action = self._close_perp_position(position, live=True)
                if action is None:
                    skipped.append({"instrument_name": instrument_name, "reason": "zero_size"})
                else:
                    targets.append({**action, "status": "submitted"})
            else:
                response = self.client.close_position(
                    instrument_name,
                    order_type=normalized_order_type,
                )
                targets.append(
                    {
                        "instrument_name": instrument_name,
                        "kind": position.kind,
                        "close_side": close_side,
                        "amount": format_decimal(close_qty, 8),
                        "order_type": normalized_order_type,
                        "status": "submitted",
                        "method": "close_position",
                        "response": response,
                    }
                )

        return {
            "action": "close-position",
            "live": live,
            "list_only": False,
            "order_type": normalized_order_type,
            "positions": [self._position_close_row(item) for item in open_positions],
            "targets": targets,
            "skipped": skipped,
        }

    def _position_close_row(self, position: Position) -> dict[str, Any]:
        return {
            "instrument_name": position.instrument_name,
            "kind": position.kind,
            "direction": position.direction,
            "size": format_decimal(abs(position.size), 8),
            "mark_price": format_decimal(position.mark_price, 8),
            "floating_profit_loss": format_decimal(position.floating_profit_loss, 8),
        }

    def _close_position_preview(
        self,
        position: Position,
        *,
        close_qty: Decimal,
        close_side: str,
        order_type: str,
        is_option: bool,
        is_perp: bool,
    ) -> dict[str, Any]:
        if is_option:
            method = "reduce_only_limit_ioc" if order_type == "limit" else "reduce_only_market"
        elif is_perp:
            method = "close_position"
        else:
            method = "close_position"
        return {
            "instrument_name": position.instrument_name,
            "kind": position.kind,
            "direction": position.direction,
            "size": format_decimal(abs(position.size), 8),
            "close_side": close_side,
            "amount": format_decimal(close_qty, 8),
            "order_type": order_type,
            "status": "preview",
            "method": method,
        }

    def _close_option_position_live(
        self,
        context: RuntimeContext,
        *,
        position: Position,
        close_qty: Decimal,
        close_side: str,
        label: str,
        order_type: str,
    ) -> dict[str, Any]:
        instrument_name = position.instrument_name
        if order_type == "limit":
            instrument = self._find_instrument(context, instrument_name)
            book = self._get_orderbook(instrument_name, context.orderbook_cache)
            initial_price = (
                self.strategy.close_buy_price(instrument, book)
                if close_side == "buy"
                else self.strategy.close_sell_price(instrument, book)
            )
            result = self._close_leg_with_retry(
                context,
                instrument_name=instrument_name,
                quantity=close_qty,
                direction=close_side,
                label=label,
                initial_price=initial_price,
            )
            if result["unfilled"] <= 0:
                status = "filled"
            elif result["filled"] > 0:
                status = "partial"
            else:
                status = "failed"
            return {
                "instrument_name": instrument_name,
                "kind": position.kind,
                "close_side": close_side,
                "amount": format_decimal(close_qty, 8),
                "order_type": order_type,
                "status": status,
                "method": "reduce_only_limit_ioc",
                "filled": format_decimal(result["filled"], 8),
                "unfilled": format_decimal(result["unfilled"], 8),
                "responses": result["responses"],
            }

        instrument = self._find_instrument(context, instrument_name)
        requested = align_option_order_amount(close_qty, instrument.contract_size, instrument.min_trade_amount)
        capacity = self._option_reduce_only_capacity(
            instrument_name,
            close_side,
            option_positions=context.option_positions,
        )
        order_amount = align_option_order_amount(
            min(requested, capacity),
            instrument.contract_size,
            instrument.min_trade_amount,
        )
        if order_amount <= 0:
            return {
                "instrument_name": instrument_name,
                "kind": position.kind,
                "close_side": close_side,
                "amount": format_decimal(close_qty, 8),
                "order_type": order_type,
                "status": "failed",
                "method": "reduce_only_market",
                "reason": "zero_reduce_only_capacity",
            }
        place_fn = self.client.place_buy_order if close_side == "buy" else self.client.place_sell_order
        response = place_fn(
            instrument_name=instrument_name,
            amount=order_amount,
            label=label,
            order_type="market",
            reduce_only=True,
        )
        filled = self._response_filled_amount(response)
        if filled >= order_amount:
            status = "filled"
        elif filled > 0:
            status = "partial"
        else:
            status = "failed"
        return {
            "instrument_name": instrument_name,
            "kind": position.kind,
            "close_side": close_side,
            "amount": format_decimal(order_amount, 8),
            "order_type": order_type,
            "status": status,
            "method": "reduce_only_market",
            "response": response,
        }

    def panic_close(self, *, live: bool = False) -> dict[str, Any]:
        context = self._load_runtime(live=live)
        actions: list[dict[str, Any]] = []
        for order in context.open_orders:
            if live:
                response = self.client.cancel_order(order.order_id)
                actions.append({"action": "cancel_order", "order_id": order.order_id, "response": response})
            else:
                actions.append({"action": "cancel_order_preview", "order_id": order.order_id})

        for group in list(self._open_groups(context.state)):
            actions.extend(self._close_group(context, group, reason="panic_close", live=live))

        for position in context.future_positions:
            if "PERPETUAL" not in position.instrument_name:
                continue
            preview = self._close_perp_position(position, live=live)
            if preview is not None:
                actions.append(preview)

        if live:
            context.state.groups = [group for group in context.state.groups if group.status == "closed"]
            cooldown_until = utc_now_ms() + (self.config.cooldown_hours * 3600 * 1000)
            context.state.cooldown_until_ms = cooldown_until
            # Panic-close is a portfolio-wide halt: stamp every enabled book's
            # cooldown so post-panic re-entries are blocked evenly across books.
            for book in context.snapshot.equity_by_book:
                context.state.cooldown_until_ms_by_book[book] = cooldown_until
            open_count = sum(1 for action in actions if action.get("action") == "close_group")
            self._telegram_alert(
                "Panic close executed",
                body=f"closed_groups={open_count} cancelled_orders={sum(1 for a in actions if a.get('action') == 'cancel_order')}",
                event_key=f"panic_close:{self._journal_scope_key()}",
                level="critical",
            )
        self.state_store.save(context.state)
        return {"action": "panic_close", "live": live, "actions": actions}

    def cancel(self, order_id: str) -> dict[str, Any]:
        response = self.client.cancel_order(order_id)
        return {"action": "cancelled", "order_id": order_id, "response": response}

    def _execute_covered_call_spot_exit(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        reason: str,
        live: bool,
    ) -> dict[str, Any]:
        if group.spot_exit_status in {"submitted", "filled"}:
            return {
                "action": "covered_call_spot_exit_skipped",
                "group_id": group.group_id,
                "reason": f"already_{group.spot_exit_status}",
                "spot_exit_status": group.spot_exit_status,
                "spot_exit_order_id": group.spot_exit_order_id or None,
            }

        instrument_name = self._covered_call_spot_instrument(group.currency)
        spot_plan = self._plan_covered_call_spot_exit(context, group, live=live, reason=reason)
        amount = spot_plan["amount"]
        if amount <= 0:
            if live:
                group.spot_exit_status = "skipped"
                group.spot_exit_reason = "spot_amount_below_min_or_unavailable"
            return {
                "action": "covered_call_spot_exit_skipped",
                "group_id": group.group_id,
                "reason": "spot_amount_below_min_or_unavailable",
                "instrument_name": instrument_name,
                "amount": format_decimal(amount, 8),
                "live": live,
            }

        payload = {
            "action": "covered_call_spot_exit" if live else "covered_call_spot_exit_preview",
            "group_id": group.group_id,
            "reason": reason,
            "instrument_name": instrument_name,
            "amount": format_decimal(amount, 8),
            "covered_underlying_quantity": format_decimal(spot_plan["cover"], 8),
            "settlement_loss": format_decimal(spot_plan["settlement_loss"], 8),
            "settlement_loss_source": spot_plan["settlement_loss_source"],
            "order_type": self.config.covered_call_spot_order_type,
            "live": live,
        }
        if not live:
            return payload

        group.spot_exit_amount = amount
        group.spot_exit_instrument_name = instrument_name
        group.spot_exit_reason = reason
        label = f"{group.short_label or self._spread_labels(group.currency, group.group_id)['short']}-spot-exit"
        try:
            from ..wallet_ops import _lookup_spot_instrument, place_protected_spot_order

            instrument = _lookup_spot_instrument(self.client, instrument_name, group.currency)
            protected = place_protected_spot_order(
                self.client,
                instrument=instrument,
                instrument_name=instrument_name,
                direction="sell",
                amount=amount,
                label=label,
                order_type=self.config.covered_call_spot_order_type,
                max_slippage_pct=self.config.covered_call_spot_max_slippage_pct,
                live=True,
            )
        except Exception as exc:  # Do not blindly retry non-idempotent spot sells.
            group.spot_exit_reason = f"{reason}: submission_uncertain: {exc}"
            payload["spot_exit_status"] = group.spot_exit_status
            payload["error"] = str(exc)
            return payload

        if protected.get("skipped"):
            skip_reason = str(protected.get("reason") or "skipped")
            group.spot_exit_reason = f"{reason}: {skip_reason}"
            payload["reason"] = skip_reason
            payload["reference_mark_price"] = protected.get("reference_mark_price")
            payload["slippage_limit_price"] = protected.get("slippage_limit_price")
            if skip_reason != "slippage_exceeded":
                group.spot_exit_status = "skipped"
            payload["spot_exit_status"] = group.spot_exit_status or "pending"
            return payload

        group.spot_exit_status = "submitted"
        response = protected.get("response") or {}
        payload["order_type"] = protected.get("order_type", self.config.covered_call_spot_order_type)
        if protected.get("reference_mark_price"):
            payload["reference_mark_price"] = protected.get("reference_mark_price")
        if protected.get("slippage_limit_price"):
            payload["slippage_limit_price"] = protected.get("slippage_limit_price")
        order = self._response_order(response)
        order_state = str(order.get("order_state") or "").lower()
        filled = self._response_filled_amount(response)
        group.spot_exit_order_id = str(order.get("order_id") or "")
        if order_state == "filled" or filled >= amount:
            group.spot_exit_status = "filled"
        elif order_state in {"cancelled", "rejected"}:
            group.spot_exit_status = "failed"
        else:
            group.spot_exit_status = "submitted"
        payload["spot_exit_status"] = group.spot_exit_status
        payload["spot_exit_order_id"] = group.spot_exit_order_id or None
        payload["filled_amount"] = format_decimal(filled, 8)
        payload["response"] = response
        return payload

    def _close_group(
        self, context: RuntimeContext, group: TradeGroup, *, reason: str, live: bool
    ) -> list[dict[str, Any]]:
        short_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        short_instrument = self._find_instrument(context, group.short_instrument_name)
        close_buy_price = self.strategy.close_buy_price_for_exit(
            short_instrument,
            short_book,
            reason=reason,
            incomplete_streak=group.close_incomplete_streak,
        )
        close_short = {
            "instrument_name": group.short_instrument_name,
            "amount": format_decimal(group.quantity, 8),
            "price": format_decimal(close_buy_price, 8),
            "label": f"{group.short_label or self._spread_labels(group.currency, group.group_id)['short']}-close",
            "direction": "buy",
        }
        if not live:
            requests: dict[str, Any] = {"short_leg": close_short}
            if group.long_instrument_name:
                long_book = self._get_orderbook(group.long_instrument_name, context.orderbook_cache)
                long_inst = self._find_instrument(context, group.long_instrument_name)
                requests["long_leg"] = {
                    "instrument_name": group.long_instrument_name,
                    "amount": format_decimal(group.quantity, 8),
                    "price": format_decimal(self.strategy.close_sell_price(long_inst, long_book), 8),
                    "label": f"{group.long_label or self._spread_labels(group.currency, group.group_id)['long']}-close",
                    "direction": "sell",
                }
            return [
                {
                    "action": "close_group_preview",
                    "reason": reason,
                    "group_id": group.group_id,
                    "requests": requests,
                }
            ]
        short_result = self._close_leg_with_retry(
            context,
            instrument_name=group.short_instrument_name,
            quantity=group.quantity,
            direction="buy",
            label=close_short["label"],
            initial_price=close_buy_price,
            reason=reason,
            incomplete_streak=group.close_incomplete_streak,
        )
        short_response = short_result["last_response"]
        if short_result["unfilled"] > 0:
            LOGGER.warning("close_group %s: short leg unfilled=%s", group.group_id, short_result["unfilled"])
            if short_result.get("resting_pending"):
                group.last_action = f"{reason}_pending"
            else:
                group.last_action = f"{reason}_incomplete"
                if short_result.get("streak_bump") or not self._income_exit_uses_resting_limit(reason):
                    group.close_incomplete_streak += 1
            action_type = "close_group_pending" if short_result.get("resting_pending") else "close_group_incomplete"
            return [
                {
                    "action": action_type,
                    "reason": reason,
                    "group_id": group.group_id,
                    "short_filled": short_result["filled"],
                    "short_unfilled": short_result["unfilled"],
                    "responses": {"short_leg": short_response, "short_attempts": short_result["responses"]},
                }
            ]
        closed_timestamp_ms = utc_now_ms()
        short_close_price = short_result["average_price"]
        short_trades = self._collect_close_trades(short_result, None).get("short_leg") or []
        realized_close_debit, realized_close_fee, close_fee_collateral = self._close_short_ledger(
            premium=short_close_price,
            quantity=group.quantity,
            index_price=short_book.index_price,
            trades=short_trades,
            instrument=short_instrument,
            collateral_currency=group.collateral_currency,
            at_timestamp_ms=closed_timestamp_ms,
        )
        group.close_fee_collateral = close_fee_collateral
        group.current_close_fee_collateral = close_fee_collateral
        long_response = None
        long_close_price_for_native: Decimal | None = None
        long_instrument_for_native: OptionInstrument | None = None
        if group.long_instrument_name:
            long_book = self._get_orderbook(group.long_instrument_name, context.orderbook_cache)
            long_inst = self._find_instrument(context, group.long_instrument_name)
            long_result = self._close_leg_with_retry(
                context,
                instrument_name=group.long_instrument_name,
                quantity=group.quantity,
                direction="sell",
                label=f"{group.long_label or self._spread_labels(group.currency, group.group_id)['long']}-close",
                initial_price=self.strategy.close_sell_price(
                    long_inst,
                    long_book,
                    max_spread_ratio=self.config.income_exit_max_spread_ratio
                    if reason in INCOME_EXIT_REASONS
                    else None,
                ),
                reason=reason,
                incomplete_streak=group.close_incomplete_streak,
            )
            long_response = long_result["last_response"]
            if long_result["unfilled"] > 0:
                LOGGER.warning("close_group %s: long leg unfilled=%s", group.group_id, long_result["unfilled"])
            long_close_price = long_result["average_price"]
            long_instrument = self._find_instrument(context, group.long_instrument_name)
            long_close_price_for_native = long_close_price
            long_instrument_for_native = long_instrument
            long_book = self._get_orderbook(group.long_instrument_name, context.orderbook_cache)
            long_trades = self._order_trades(long_response)
            usdc_linear = (
                long_instrument.quote_currency.upper() == "USDC"
                and long_instrument.settlement_currency.upper() == "USDC"
            )
            if usdc_linear or group.collateral_currency.upper() == "USDC":
                long_credit = self._premium_value_usdc(
                    premium=long_close_price,
                    quantity=long_result["filled"],
                    index_price=long_book.index_price,
                    instrument=long_instrument,
                )
                long_fee = self._option_fee_usdc(
                    premium=long_close_price,
                    quantity=long_result["filled"],
                    index_price=long_book.index_price,
                    base_currency=long_instrument.base_currency,
                    quote_currency=long_instrument.quote_currency,
                    settlement_currency=long_instrument.settlement_currency,
                )
                realized_close_debit -= max(long_credit - long_fee, Decimal("0"))
                realized_close_fee += long_fee
            else:
                long_fee_collateral = self._sum_trade_fees_native(long_trades, group.collateral_currency)
                if long_fee_collateral <= 0:
                    long_fee_collateral = self._option_fee_native(
                        premium=long_close_price,
                        quantity=long_result["filled"],
                        index_price=long_book.index_price,
                        quote_currency=long_instrument.quote_currency,
                        settlement_currency=long_instrument.settlement_currency,
                    )
                long_gross_native = long_close_price * long_result["filled"]
                long_net_native = long_gross_native - long_fee_collateral
                idx_long = long_book.index_price if long_book.index_price > 0 else short_book.index_price
                realized_close_debit -= long_net_native * idx_long if idx_long > 0 else Decimal("0")
                long_fee_usdc = long_fee_collateral * idx_long if idx_long > 0 else Decimal("0")
                realized_close_fee += long_fee_usdc
                close_fee_collateral += long_fee_collateral
                group.close_fee_collateral = close_fee_collateral
                group.current_close_fee_collateral = close_fee_collateral
            group.long_close_average_price = long_close_price
        group.short_close_average_price = short_close_price
        realized_pnl = group.entry_credit_net_usdc() - realized_close_debit
        realized_return_on_max_loss = safe_div(realized_pnl, group.max_loss)
        self._finalize_close_collateral_native(
            group,
            realized_pnl_usdc=realized_pnl,
            short_close_price=short_close_price,
            index_at_close=short_book.index_price,
            short_instrument=short_instrument,
            long_close_price=long_close_price_for_native,
            long_instrument=long_instrument_for_native,
        )
        self._mark_group_closed(
            group,
            reason=reason,
            closed_timestamp_ms=closed_timestamp_ms,
            realized_close_debit=realized_close_debit,
            realized_close_fee=realized_close_fee,
            realized_pnl=group.realized_pnl or realized_pnl,
            realized_return_on_max_loss=realized_return_on_max_loss,
            index_price_usd=short_book.index_price,
        )
        if live:
            self._maybe_schedule_profit_sweep(group, reason=reason, live=live)
        close_action = {
            "action": "close_group",
            "reason": reason,
            "group_id": group.group_id,
            "realized_close_debit": realized_close_debit,
            "realized_close_fee": realized_close_fee,
            "realized_pnl": group.realized_pnl or realized_pnl,
            "realized_return_on_max_loss": realized_return_on_max_loss,
            "realized_annualized_return": group.realized_annualized_return,
            "realized_apr_on_equity": group.realized_apr_on_equity,
            "close_book_equity": group.close_book_equity,
            "responses": {
                "short_leg": short_response,
                "short_attempts": short_result["responses"],
                "long_leg": long_response,
            },
        }
        close_action["trades"] = self._collect_close_trades(short_result, long_response)
        if live and reason in _TELEGRAM_CLOSE_REASONS:
            self._telegram_alert(
                f"Position closed ({reason})",
                body=f"group={group.group_id} instrument={group.short_instrument_name}",
                event_key=f"close:{reason}:{group.group_id}",
                level="critical" if reason in {"hard_stop", "panic_close"} else "warning",
                extra={
                    "realized_pnl": close_action.get("realized_pnl"),
                    "currency": group.currency,
                },
            )
        return [close_action]

    def _collect_close_trades(
        self,
        short_result: dict[str, Any],
        long_result: dict[str, Any] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        trades: dict[str, list[dict[str, Any]]] = {}
        if isinstance(short_result, dict):
            short_trades: list[dict[str, Any]] = []
            for response in short_result.get("responses") or []:
                if isinstance(response, dict):
                    short_trades.extend(self._order_trades(response))
            if short_trades:
                trades["short_leg"] = short_trades
        if isinstance(long_result, dict):
            long_trades: list[dict[str, Any]] = []
            for response in long_result.get("responses") or []:
                if isinstance(response, dict):
                    long_trades.extend(self._order_trades(response))
            if long_trades:
                trades["long_leg"] = long_trades
        return trades

    def _income_exit_uses_resting_limit(self, reason: str) -> bool:
        return reason in INCOME_EXIT_REASONS and self.config.income_exit_time_in_force == "good_til_cancelled"

    def _close_time_in_force(self, reason: str) -> str:
        if self._income_exit_uses_resting_limit(reason):
            return "good_til_cancelled"
        return "immediate_or_cancel"

    def _find_resting_close_order(
        self,
        context: RuntimeContext,
        *,
        label: str,
        instrument_name: str,
        direction: str,
    ) -> OpenOrder | None:
        want = direction.lower()
        for order in context.open_orders:
            if order.label != label:
                continue
            if order.instrument_name != instrument_name:
                continue
            if order.direction != want:
                continue
            if not order.reduce_only:
                continue
            if order.order_state not in {"open", "untriggered"}:
                continue
            return order
        return None

    def _await_resting_close_order(
        self,
        initial_response: dict[str, Any],
        *,
        max_wait_seconds: int,
    ) -> dict[str, Any]:
        order = self._response_order(initial_response)
        order_id = str(order.get("order_id") or "")
        state = initial_response
        if not order_id:
            return state
        waited = 0
        while waited < max_wait_seconds:
            order = self._response_order(state)
            order_state = str(order.get("order_state") or "").lower()
            if order_state in {"filled", "cancelled", "rejected"}:
                break
            step = min(self.config.order_poll_seconds, max_wait_seconds - waited)
            if step <= 0:
                break
            self.sleep_fn(step)
            waited += step
            state = self.client.get_order_state(order_id)
        return state

    def _close_leg_with_retry(
        self,
        context: RuntimeContext,
        *,
        instrument_name: str,
        quantity: Decimal,
        direction: str,
        label: str,
        initial_price: Decimal,
        reason: str = "",
        incomplete_streak: int = 0,
    ) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        total_filled = Decimal("0")
        weighted_price = Decimal("0")
        place_fn = self.client.place_buy_order if direction == "buy" else self.client.place_sell_order
        inst = self._find_instrument(context, instrument_name)
        requested_total = align_option_order_amount(quantity, inst.contract_size, inst.min_trade_amount)
        if requested_total <= 0:
            raise ExchangeError(
                f"close order amount aligned to zero for {instrument_name}; "
                f"requested quantity is below exchange minimum/step"
            )
        initial_price = self._positive_option_limit_price(inst, initial_price)
        use_resting = self._income_exit_uses_resting_limit(reason)
        close_tif = self._close_time_in_force(reason)
        ttl_ms = self.config.income_exit_order_ttl_minutes * 60 * 1000
        streak_bump = False

        if use_resting:
            resting = self._find_resting_close_order(
                context,
                label=label,
                instrument_name=instrument_name,
                direction=direction,
            )
            if resting is not None:
                state = self.client.get_order_state(resting.order_id)
                responses.append(state)
                filled = self._response_filled_amount(state)
                avg = self._response_average_price(state)
                order = self._response_order(state)
                order_state = str(order.get("order_state") or "").lower()
                if filled > 0:
                    weighted_price += avg * filled
                    total_filled += filled
                if total_filled >= requested_total:
                    average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
                    return {
                        "responses": responses,
                        "last_response": responses[-1],
                        "average_price": average_price,
                        "filled": total_filled,
                        "unfilled": Decimal("0"),
                    }
                if order_state in {"open", "untriggered"}:
                    created = resting.creation_timestamp_ms or 0
                    if created <= 0 or utc_now_ms() - created < ttl_ms:
                        average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
                        return {
                            "responses": responses,
                            "last_response": responses[-1],
                            "average_price": average_price,
                            "filled": total_filled,
                            "unfilled": requested_total - total_filled,
                            "resting_pending": True,
                        }
                    LOGGER.info(
                        "close_leg_with_retry: cancel stale resting %s on %s (label=%s)",
                        direction,
                        instrument_name,
                        label,
                    )
                    self.client.cancel_order(resting.order_id)
                    streak_bump = True
                    state = self.client.get_order_state(resting.order_id)
                    responses.append(state)
                    filled = self._response_filled_amount(state)
                    avg = self._response_average_price(state)
                    if filled > total_filled:
                        extra = filled - total_filled
                        weighted_price += avg * extra
                        total_filled = filled

        market_after = self.config.income_exit_market_after_attempts
        if (
            direction == "buy"
            and reason in INCOME_EXIT_REASONS
            and market_after > 0
            and incomplete_streak >= market_after
        ):
            LOGGER.warning(
                "close_leg_with_retry: income exit %s on %s escalating to market (streak=%s)",
                reason,
                instrument_name,
                incomplete_streak,
            )
            response = self._fallback_close_position_market(
                instrument_name,
                original_error=ExchangeError("income_exit_market_escalation"),
            )
            filled = self._response_filled_amount(response)
            avg = self._response_average_price(response)
            return {
                "responses": [response],
                "last_response": response,
                "average_price": avg,
                "filled": filled,
                "unfilled": max(requested_total - filled, Decimal("0")),
            }

        remaining_total = requested_total - total_filled
        capacity = self._option_reduce_only_capacity(
            instrument_name, direction, option_positions=context.option_positions
        )
        first_amount = align_option_order_amount(
            min(remaining_total, capacity), inst.contract_size, inst.min_trade_amount
        )
        if first_amount <= 0:
            if total_filled > 0:
                average_price = weighted_price / total_filled
                return {
                    "responses": responses,
                    "last_response": responses[-1] if responses else self._noop_option_order_response(),
                    "average_price": average_price,
                    "filled": total_filled,
                    "unfilled": remaining_total,
                }
            LOGGER.warning(
                "close_leg_with_retry: skip reduce_only %s on %s (label=%s requested=%s exchange_capacity=%s)",
                direction,
                instrument_name,
                label,
                requested_total,
                capacity,
            )
            noop = self._noop_option_order_response()
            return {
                "responses": [noop],
                "last_response": noop,
                "average_price": Decimal("0"),
                "filled": Decimal("0"),
                "unfilled": requested_total,
            }

        response = self._submit_option_close_limit(
            place_fn,
            instrument=inst,
            instrument_name=instrument_name,
            amount=first_amount,
            label=label,
            price=initial_price,
            direction=direction,
            time_in_force=close_tif,
        )
        responses.append(response)
        if use_resting:
            response = self._await_resting_close_order(
                response,
                max_wait_seconds=self.config.order_poll_seconds,
            )
            responses[-1] = response
        filled = self._response_filled_amount(response)
        avg = self._response_average_price(response)
        if filled > 0:
            weighted_price += avg * filled
            total_filled += filled

        remaining = requested_total - total_filled
        if remaining > 0:
            context.orderbook_cache.pop(instrument_name, None)
            retry_book = self._get_orderbook(instrument_name, context.orderbook_cache)
            instrument = self._find_instrument(context, instrument_name)
            remaining = align_option_order_amount(remaining, instrument.contract_size, instrument.min_trade_amount)
            if remaining <= 0:
                average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
                return {
                    "responses": responses,
                    "last_response": responses[-1],
                    "average_price": average_price,
                    "filled": total_filled,
                    "unfilled": requested_total - total_filled,
                }
            # Retry capacity: the first leg may have partially filled which
            # reduces our position; re-poll positions once to reflect that
            # rather than using the stale manage-loop snapshot.
            retry_capacity = self._option_reduce_only_capacity(instrument_name, direction)
            retry_amount = align_option_order_amount(
                min(remaining, retry_capacity), instrument.contract_size, instrument.min_trade_amount
            )
            if retry_amount <= 0:
                average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
                return {
                    "responses": responses,
                    "last_response": responses[-1],
                    "average_price": average_price,
                    "filled": total_filled,
                    "unfilled": requested_total - total_filled,
                }
            if direction == "buy":
                retry_price = (
                    self.strategy.close_buy_price_for_exit(
                        instrument,
                        retry_book,
                        reason=reason,
                        incomplete_streak=max(incomplete_streak, 1),
                    )
                    if reason
                    else self.strategy.close_buy_price(instrument, retry_book)
                )
            else:
                spread_ratio = self.config.income_exit_max_spread_ratio if reason in INCOME_EXIT_REASONS else None
                retry_price = self.strategy.close_sell_price(instrument, retry_book, max_spread_ratio=spread_ratio)
            retry_price = self._positive_option_limit_price(instrument, retry_price)
            response = self._submit_option_close_limit(
                place_fn,
                instrument=instrument,
                instrument_name=instrument_name,
                amount=retry_amount,
                label=label,
                price=retry_price,
                direction=direction,
                time_in_force=close_tif,
            )
            responses.append(response)
            filled = self._response_filled_amount(response)
            avg = self._response_average_price(response)
            if filled > 0:
                weighted_price += avg * filled
                total_filled += filled

        average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
        unfilled = requested_total - total_filled
        result: dict[str, Any] = {
            "responses": responses,
            "last_response": responses[-1],
            "average_price": average_price,
            "filled": total_filled,
            "unfilled": unfilled,
        }
        if use_resting and unfilled > 0:
            order = self._response_order(responses[-1])
            order_state = str(order.get("order_state") or "").lower()
            if order_state in {"open", "untriggered"}:
                result["resting_pending"] = True
        if streak_bump and unfilled > 0:
            result["streak_bump"] = True
        return result

    def _positive_option_limit_price(self, instrument: OptionInstrument, price: Decimal) -> Decimal:
        if price > 0:
            return price
        tick = instrument.tick_size_for_price(instrument.tick_size) if instrument.tick_size > 0 else Decimal("0")
        if tick > 0:
            return tick
        return Decimal("0.0001")

    def _submit_option_close_limit(
        self,
        place_fn: Any,
        *,
        instrument: OptionInstrument,
        instrument_name: str,
        amount: Decimal,
        label: str,
        price: Decimal,
        direction: str,
        time_in_force: str = "immediate_or_cancel",
    ) -> dict[str, Any]:
        order_kwargs = {
            "instrument_name": instrument_name,
            "amount": amount,
            "label": label,
            "order_type": "limit",
            "price": price,
            "time_in_force": time_in_force,
            "reduce_only": True,
        }
        try:
            return place_fn(**order_kwargs)
        except ExchangeError as exc:
            limit = parse_exchange_price_band_limit(str(exc))
            if limit is None:
                return self._fallback_close_position_market(instrument_name, original_error=exc)
            if direction == "buy":
                clamped = floor_to_step(limit, instrument.tick_size_for_price(limit))
            else:
                clamped = ceil_to_step(limit, instrument.tick_size_for_price(limit))
            if clamped <= 0 or clamped == price:
                return self._fallback_close_position_market(instrument_name, original_error=exc)
            LOGGER.warning(
                "option close %s on %s clamped from %s to exchange limit %s (%s)",
                direction,
                instrument_name,
                price,
                clamped,
                exc,
            )
            try:
                return place_fn(**{**order_kwargs, "price": clamped})
            except ExchangeError as retry_exc:
                return self._fallback_close_position_market(instrument_name, original_error=retry_exc)

    def _fallback_close_position_market(
        self,
        instrument_name: str,
        *,
        original_error: Exception,
    ) -> dict[str, Any]:
        LOGGER.warning(
            "option close limit rejected for %s; falling back to close_position market (%s)",
            instrument_name,
            original_error,
        )
        return self.client.close_position(instrument_name, order_type="market")

    def _option_reduce_only_capacity(
        self,
        instrument_name: str,
        order_direction: str,
        *,
        option_positions: list[Position] | None = None,
    ) -> Decimal:
        """Option contracts exchange will allow to close with a reduce_only order in this direction.

        When ``option_positions`` is provided the caller (typically via
        ``RuntimeContext.option_positions``) reuses a single ``get_positions``
        snapshot for a whole manage pass instead of paying one REST roundtrip
        per group. Falls back to fetching positions on demand when the caller
        cannot supply them (e.g. close-leg retries after a partial fill).
        """
        want = order_direction.lower()
        if option_positions is not None:
            for pos in option_positions:
                if pos.instrument_name != instrument_name:
                    continue
                if pos.kind != "option":
                    continue
                size = abs(pos.size)
                if size <= 0:
                    return Decimal("0")
                if want == "sell" and pos.direction == "buy":
                    return size
                if want == "buy" and pos.direction == "sell":
                    return size
                return Decimal("0")
            return Decimal("0")

        for row in self.client.get_positions(currency="any", kind="any"):
            if str(row.get("instrument_name") or "") != instrument_name:
                continue
            if str(row.get("kind") or "").lower() != "option":
                continue
            pos_dir = str(row.get("direction") or "").lower()
            size = abs(to_decimal(row.get("size")))
            if size <= 0:
                return Decimal("0")
            if want == "sell" and pos_dir == "buy":
                return size
            if want == "buy" and pos_dir == "sell":
                return size
            return Decimal("0")
        return Decimal("0")

    @staticmethod
    def _noop_option_order_response() -> dict[str, Any]:
        return {"order": {"filled_amount": "0", "average_price": "0", "order_state": "filled"}}

    def _execute_hedge_plan(self, context: RuntimeContext, plan: HedgePlan, *, live: bool) -> dict[str, Any]:
        payload = {"action": "hedge", "plan": plan.to_dict(), "live": live}
        if not live:
            return payload
        response = self.client.place_order(
            direction=plan.side,
            instrument_name=plan.instrument_name,
            amount=plan.order_amount,
            label=self._hedge_label(plan.currency, plan.mode),
            order_type="market",
            reduce_only=plan.side == "buy" and plan.current_hedge_base < 0,
        )
        payload["response"] = response
        return payload

    def _close_perp_position(self, position: Position, *, live: bool) -> dict[str, Any] | None:
        if position.size == 0:
            return None
        if not live:
            return {
                "action": "close_perp_preview",
                "instrument_name": position.instrument_name,
                "direction": position.direction,
            }
        response = self.client.close_position(position.instrument_name, order_type="market")
        return {"action": "close_perp", "instrument_name": position.instrument_name, "response": response}

    def _option_exit_request(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        label: str,
        quantity: Decimal,
        passive: bool,
    ) -> dict[str, Any]:
        if passive:
            ask_price = book.best_ask_price
            if ask_price <= 0:
                bid_price = book.best_bid_price
                step = instrument.tick_size_for_price(bid_price) if bid_price > 0 else instrument.tick_size
                ask_price = bid_price + step if bid_price > 0 else instrument.tick_size
            return {
                "instrument_name": instrument.instrument_name,
                "amount": format_decimal(quantity, 8),
                "price": format_decimal(ask_price, 8),
                "label": label,
                "time_in_force": "good_til_cancelled",
                "post_only": True,
            }
        bid_price = book.best_bid_price if book.best_bid_price > 0 else instrument.tick_size
        return {
            "instrument_name": instrument.instrument_name,
            "amount": format_decimal(quantity, 8),
            "price": format_decimal(bid_price, 8),
            "label": label,
            "time_in_force": "immediate_or_cancel",
        }

    def _place_option_exit_order(self, context: RuntimeContext, request: dict[str, Any]) -> dict[str, Any]:
        instrument_name = str(request["instrument_name"])
        instrument = self._find_instrument(context, instrument_name)
        raw = to_decimal(request["amount"])
        capacity = self._option_reduce_only_capacity(instrument_name, "sell", option_positions=context.option_positions)
        capped = min(raw, capacity)
        amount = align_option_order_amount(capped, instrument.contract_size, instrument.min_trade_amount)
        if amount <= 0:
            LOGGER.warning(
                "skip option exit sell on %s (requested=%s aligned_cap=%s exchange_long=%s)",
                instrument_name,
                raw,
                capped,
                capacity,
            )
            return self._noop_option_order_response()
        return self.client.place_sell_order(
            instrument_name=instrument_name,
            amount=amount,
            label=request["label"],
            order_type="limit",
            price=to_decimal(request["price"]),
            time_in_force=request["time_in_force"],
            post_only=request.get("post_only"),
            reject_post_only=False if request.get("post_only") is not None else None,
            reduce_only=True,
        )
