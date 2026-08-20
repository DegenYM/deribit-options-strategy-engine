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
from ..margin import (
    linear_usdc_short_call_initial_per_contract_usdc,
    linear_usdc_short_put_initial_per_contract_usdc,
    short_call_initial_unit,
    short_put_initial_unit,
)
from ..models import (
    OptionInstrument,
    OrderBookSnapshot,
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
    utc_now_ms,
)
from .context import (
    LOGGER,
    RECONCILE_EXTERNAL_CLOSE_GRACE_MS,
)


class StateReconcileMixin:
    """Reconcile in-memory state with exchange positions: adopt/sync/promote groups and estimate close debits."""

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
                group.entry_fee_collateral = group.entry_fee_collateral * ex_qty / old_q
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
        collateral = (
            "USDC"
            if short_instrument.quote_currency.upper() == "USDC"
            and short_instrument.settlement_currency.upper() == "USDC"
            else short_instrument.base_currency
        )
        if collateral == "USDC":
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
            entry_fee_collateral = Decimal("0")
        else:
            short_fee_collateral = self._option_fee_native(
                premium=short_premium,
                quantity=quantity,
                index_price=idx,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
            )
            long_fee_collateral = self._option_fee_native(
                premium=long_premium,
                quantity=quantity,
                index_price=idx,
                quote_currency=long_instrument.quote_currency,
                settlement_currency=long_instrument.settlement_currency,
            )
            entry_fee_collateral = short_fee_collateral + long_fee_collateral
            gross_native = (short_premium - long_premium) * quantity
            net_credit = (gross_native - entry_fee_collateral) * idx if idx > 0 else Decimal("0")
            entry_fee = entry_fee_collateral * idx if idx > 0 else Decimal("0")
        width_usdc = max(short_instrument.strike - long_instrument.strike, Decimal("0")) * quantity
        max_loss_usdc = max(width_usdc - net_credit, Decimal("0"))
        estimated_im_collateral = (
            max_loss_usdc if collateral == "USDC" else (max_loss_usdc / idx if idx > 0 else Decimal("0"))
        )
        return {
            "entry_credit": net_credit,
            "entry_fee": entry_fee,
            "entry_fee_collateral": entry_fee_collateral,
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
        group.entry_fee_collateral = metrics.get("entry_fee_collateral", Decimal("0"))
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
            mark = book.effective_mark
            if mark <= 0:
                continue

            qty = align_option_order_amount(open_sz, inst.contract_size, inst.min_trade_amount)
            if qty <= 0:
                continue

            premium = abs(position.average_price) if position.average_price != 0 else mark
            usdc_linear = inst.quote_currency.upper() == "USDC" and inst.settlement_currency.upper() == "USDC"
            collateral = "USDC" if usdc_linear else inst.base_currency
            if usdc_linear:
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
                entry_fee_collateral = Decimal("0")
            else:
                entry_fee_collateral = self._option_fee_native(
                    premium=premium,
                    quantity=qty,
                    index_price=idx,
                    quote_currency=inst.quote_currency,
                    settlement_currency=inst.settlement_currency,
                )
                gross_native = premium * qty
                net_native = gross_native - entry_fee_collateral
                net_credit = net_native * idx if idx > 0 else Decimal("0")
                entry_fee = entry_fee_collateral * idx if idx > 0 else Decimal("0")

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
                entry_fee_collateral=entry_fee_collateral,
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
        live: bool = False,
    ) -> tuple[StrategyState, bool]:
        closed_any = False
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
            estimated_close_debit: Decimal | None = None
            realized_pnl: Decimal | None = None
            realized_return_on_max_loss: Decimal | None = None
            close_index_usd: Decimal | None = None
            try:
                short_book = self._get_orderbook(group.short_instrument_name, orderbook_cache)
                if short_book.index_price > 0:
                    close_index_usd = short_book.index_price
                    group.close_index_usd = close_index_usd
            except Exception:
                short_book = None
                close_index_usd = None
            if group.is_coin_collateral() and short_book is not None:
                try:
                    short_instrument = self._find_or_fetch_instrument(
                        markets_by_currency or {},
                        group.short_instrument_name,
                    )
                    estimated_close_debit = self._reconcile_coin_close_ledger(
                        group,
                        short_book=short_book,
                        short_instrument=short_instrument,
                        closed_timestamp_ms=closed_timestamp_ms,
                    )
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "reconcile coin close ledger failed group=%s: %s",
                        group.group_id,
                        exc,
                    )
                    estimated_close_debit = None
            else:
                estimated_close_debit = None
                if short_book is not None:
                    try:
                        short_instrument = self._find_or_fetch_instrument(
                            markets_by_currency or {},
                            group.short_instrument_name,
                        )
                        estimated_close_debit = self._reconcile_usdc_close_debit_from_trades(
                            group,
                            short_book=short_book,
                            short_instrument=short_instrument,
                            closed_timestamp_ms=closed_timestamp_ms,
                        )
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning(
                            "reconcile usdc close trades failed group=%s: %s",
                            group.group_id,
                            exc,
                        )
                        estimated_close_debit = None
                if estimated_close_debit is None:
                    estimated_close_debit = self._estimate_reconcile_close_debit(
                        group,
                        orderbook_cache,
                        markets_by_currency=markets_by_currency,
                    )
            if estimated_close_debit is not None:
                realized_pnl = group.entry_credit_net_usdc() - estimated_close_debit
                realized_return_on_max_loss = safe_div(realized_pnl, group.max_loss)
            if (
                self.config.covered_call_spot_exit_enabled
                and not self.config.covered_call_robust_exit_enabled
                and self._is_covered_call_group(group)
                and group.spot_exit_status not in {"submitted", "filled", "pending"}
                and self._covered_call_itm_from_cache(group, orderbook_cache)
            ):
                group.spot_exit_status = "pending"
                spot_plan = self._plan_covered_call_spot_exit_fields(
                    group,
                    orderbook_cache=orderbook_cache,
                    markets_by_currency=markets_by_currency,
                    summaries={},
                    live=live,
                    reason="covered_call_settlement_exit",
                )
                group.spot_exit_amount = spot_plan["amount"]
                group.spot_exit_instrument_name = self._covered_call_spot_instrument(group.currency)
                group.spot_exit_reason = "covered_call_settlement_exit"
            journal_rows: list[dict[str, Any]] = []
            try:
                journal_rows = self._trade_journal().list_executions(
                    self._journal_scope_key(),
                    group_id=group.group_id,
                    limit=50,
                )
            except Exception:  # noqa: BLE001
                journal_rows = []
            inferred_reason: str | None = None
            if not expired and journal_rows:
                from ..trade_journal_backfill import infer_bot_income_exit_close_reason

                inferred_reason = infer_bot_income_exit_close_reason(
                    journal_rows,
                    order_label_prefix=self.config.order_label_prefix,
                )
                if inferred_reason:
                    group.enrich_fill_prices_from_journal(journal_rows)
            group.backfill_realized_pnl_collateral_native(journal_executions=journal_rows or None)
            group.backfill_realized_pnl_usdc()
            close_reason = "reconciled_expiry" if expired else (inferred_reason or "reconciled_external")
            self._mark_group_closed(
                group,
                reason=close_reason,
                closed_timestamp_ms=closed_timestamp_ms,
                realized_close_debit=group.realized_close_debit or estimated_close_debit,
                realized_close_fee=group.realized_close_fee,
                realized_pnl=group.realized_pnl or realized_pnl,
                realized_return_on_max_loss=realized_return_on_max_loss,
                index_price_usd=close_index_usd,
            )
            self._journal_reconcile_close(group, closed_timestamp_ms=closed_timestamp_ms)
            closed_any = True
            if live and inferred_reason:
                self._maybe_schedule_profit_sweep(group, reason=inferred_reason, live=live)
            if realized_pnl is not None:
                LOGGER.info("reconcile group=%s estimated_pnl=%s", group.group_id, realized_pnl)
            else:
                LOGGER.warning("reconcile group=%s could not estimate PnL", group.group_id)
        return state, closed_any

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
        if group.collateral_currency.upper() != "USDC" and not (
            short_instrument.quote_currency.upper() == "USDC" and short_instrument.settlement_currency.upper() == "USDC"
        ):
            fee_collateral = self._option_fee_native(
                premium=settle,
                quantity=group.quantity,
                index_price=index_price,
                quote_currency=short_instrument.quote_currency,
                settlement_currency=short_instrument.settlement_currency,
            )
            total_native = settle * group.quantity + fee_collateral
            return max(total_native * index_price, Decimal("0")) if index_price > 0 else Decimal("0")
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

    def _reconcile_usdc_close_debit_from_trades(
        self,
        group: TradeGroup,
        *,
        short_book: OrderBookSnapshot,
        short_instrument: OptionInstrument,
        closed_timestamp_ms: int,
    ) -> Decimal | None:
        """Prefer actual buy-to-close fills for USDC / linear books (manual exchange closes)."""
        close_premium = self._reconcile_buy_close_premium_native(
            group,
            short_book=short_book,
            closed_timestamp_ms=closed_timestamp_ms,
        )
        if close_premium <= 0:
            return None
        index_price = short_book.index_price if short_book.index_price > 0 else group.entry_index_usd
        if index_price <= 0:
            return None
        close_fee = self._option_fee_usdc(
            premium=close_premium,
            quantity=group.quantity,
            index_price=index_price,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        gross = self._premium_value_usdc(
            premium=close_premium,
            quantity=group.quantity,
            index_price=index_price,
            instrument=short_instrument,
        )
        group.short_close_average_price = close_premium
        return gross + close_fee

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
        if group.current_debit > 0 and not spread_settlement and not group.is_coin_collateral():
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
            if group.current_debit >= 0 and not group.is_coin_collateral():
                debit = group.current_debit
                if is_spread:
                    return self._cap_spread_reconcile_close_debit(group, debit)
                return debit
            return None
