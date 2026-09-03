"""Perp-hedge sizing, reconciliation and orphan/unwind logic used by :mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..models import HedgePlan, OptionInstrument, Position, TradeGroup
from ..utils import format_decimal
from .context import LOGGER, RuntimeContext


class HedgeManagementMixin:
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

    def _current_hedge_base(self, positions: list[Position], currency: str) -> Decimal:
        instrument_name = self._perp_instrument(currency)
        return sum(
            (position.signed_size_currency for position in positions if position.instrument_name == instrument_name),
            Decimal("0"),
        )
