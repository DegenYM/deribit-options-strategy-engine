from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any

from .book import Book
from .config import BotConfig
from .fees import (
    annualized_return,
    inverse_option_fee_native_per_contract,
    net_apr_inverse_short_per_contract,
    net_apr_linear_usdc_short_call_per_contract,
    net_apr_linear_usdc_short_put_per_contract,
    option_trade_fee_native,
    premium_value_usdc,
)
from .margin import (
    linear_usdc_short_call_initial_per_contract_usdc,
    linear_usdc_short_call_mm_per_contract_usdc,
    linear_usdc_short_put_initial_per_contract_usdc,
    linear_usdc_short_put_mm_per_contract_usdc,
    short_call_initial_unit,
    short_call_maintenance_unit,
    short_put_initial_unit,
    short_put_maintenance_unit,
)
from .models import NakedPutCandidate, OptionInstrument, OptionSide, OrderBookSnapshot, RiskRegime, SpreadLeg
from .utils import ceil_to_step, floor_to_step, format_decimal


class StrategySelector:
    def __init__(self, config: BotConfig):
        self.config = config

    def _screening_net_apr(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        premium_per_contract: Decimal,
        collateral_currency: str,
        option_type: str,
        net_credit_on_capital: Decimal | None = None,
        capital_base: Decimal | None = None,
    ) -> Decimal:
        """Per-contract APR for MIN_NET_APR gates (round-trip fees; not book equity)."""
        dte_days = instrument.dte_days()
        if net_credit_on_capital is not None and capital_base is not None:
            return annualized_return(
                net_credit=net_credit_on_capital,
                capital_base=capital_base,
                dte_days=dte_days,
            )
        if collateral_currency.upper() == "USDC":
            if option_type == "call":
                return net_apr_linear_usdc_short_call_per_contract(
                    premium_per_contract=premium_per_contract,
                    index_price=book.index_price,
                    dte_days=dte_days,
                    fee_rate=self.config.option_fee_rate,
                    fee_cap_rate=self.config.option_fee_cap_rate,
                )
            return net_apr_linear_usdc_short_put_per_contract(
                premium_per_contract=premium_per_contract,
                strike=instrument.strike,
                dte_days=dte_days,
                index_price=book.index_price,
                fee_rate=self.config.option_fee_rate,
                fee_cap_rate=self.config.option_fee_cap_rate,
            )
        return net_apr_inverse_short_per_contract(
            premium_per_contract=premium_per_contract,
            contract_size=instrument.contract_size,
            dte_days=dte_days,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
        )

    def put_delta_min(self, currency: str) -> Decimal:
        return self.config.put_delta_bounds(currency)[0]

    def put_delta_max(self, currency: str) -> Decimal:
        return self.config.put_delta_bounds(currency)[1]

    def put_otm_min(self, currency: str) -> Decimal:
        return self.config.put_otm_bounds(currency)[0]

    def put_otm_max(self, currency: str) -> Decimal:
        return self.config.put_otm_bounds(currency)[1]

    @staticmethod
    def _floor_price(value: Decimal, instrument: OptionInstrument) -> Decimal:
        return floor_to_step(value, instrument.tick_size_for_price(value))

    @staticmethod
    def _ceil_price(value: Decimal, instrument: OptionInstrument) -> Decimal:
        return ceil_to_step(value, instrument.tick_size_for_price(value))

    def sell_limit_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        ask = book.best_ask_price
        if ask <= 0:
            return Decimal("0")
        return ask

    def sell_mid_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        bid = book.best_bid_price
        ask = book.best_ask_price
        if ask <= 0:
            return Decimal("0")
        if bid <= 0:
            return ask
        midpoint = (bid + ask) / Decimal("2")
        step = instrument.tick_size_for_price(midpoint if midpoint > 0 else ask)
        if ask - bid <= step:
            return ask
        min_passive = self._ceil_price(bid + step, instrument)
        price = self._floor_price(midpoint, instrument)
        if price < min_passive:
            price = min_passive
        if price > ask:
            price = ask
        return price

    def sell_taker_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        bid = book.best_bid_price
        if bid <= 0:
            return Decimal("0")
        return bid

    def buy_limit_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        bid = book.best_bid_price
        return bid if bid > 0 else Decimal("0")

    def buy_mid_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        ask = book.best_ask_price
        bid = book.best_bid_price
        if bid <= 0:
            return Decimal("0")
        if ask <= 0:
            return bid
        midpoint = (bid + ask) / Decimal("2")
        step = instrument.tick_size_for_price(midpoint if midpoint > 0 else ask)
        if ask - bid <= step:
            return bid
        max_passive = self._floor_price(ask - step, instrument)
        price = self._ceil_price(midpoint, instrument)
        if price > max_passive:
            price = max_passive
        if price < bid:
            price = bid
        return price

    def buy_taker_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        ask = book.best_ask_price
        if ask <= 0:
            return Decimal("0")
        return ask

    def close_buy_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        ask = book.best_ask_price
        if ask <= 0:
            return instrument.tick_size
        return max(ask, self._ceil_price(ask * (Decimal("1") + self.config.exit_buffer_ratio), instrument))

    def close_sell_price(self, instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        bid = book.best_bid_price
        minimum_price = instrument.tick_size_for_price(bid if bid > 0 else instrument.tick_size)
        if minimum_price <= 0:
            minimum_price = instrument.tick_size
        if bid <= 0:
            return minimum_price
        return max(
            minimum_price,
            self._floor_price(bid * (Decimal("1") - self.config.exit_buffer_ratio), instrument),
        )

    @staticmethod
    def _put_otm_ratio(instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        if book.index_price <= 0:
            return Decimal("0")
        return Decimal("1") - (instrument.strike / book.index_price)

    @staticmethod
    def _call_otm_ratio(instrument: OptionInstrument, book: OrderBookSnapshot) -> Decimal:
        if book.index_price <= 0:
            return Decimal("0")
        return (instrument.strike / book.index_price) - Decimal("1")

    @staticmethod
    def _is_otm_call(instrument: OptionInstrument, book: OrderBookSnapshot) -> bool:
        return book.index_price > 0 and instrument.strike > book.index_price

    @staticmethod
    def _otm_ratio_from_strike(
        instrument: OptionInstrument,
        *,
        index_price: Decimal,
        option_type: str,
    ) -> Decimal:
        if index_price <= 0 or instrument.strike <= 0:
            return Decimal("0")
        if option_type == "call":
            return (instrument.strike / index_price) - Decimal("1")
        return Decimal("1") - (instrument.strike / index_price)

    def _passes_strike_otm_prefilter(
        self,
        instrument: OptionInstrument,
        *,
        currency: str,
        index_price: Decimal,
        option_type: str,
    ) -> bool:
        """Cheap strike/index screen before ``get_order_book`` during scan."""
        if index_price <= 0 or instrument.strike <= 0:
            return True
        otm = self._otm_ratio_from_strike(instrument, index_price=index_price, option_type=option_type)
        if option_type == "call":
            omin, omax = self.config.call_otm_bounds(currency)
        else:
            omin, omax = self.config.put_otm_bounds(currency)
        slack = Decimal("0.005")
        return (omin - slack) <= otm <= (omax + slack)

    @staticmethod
    def _covered_call_scan_pairs(
        calls: list[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        index_price: Decimal | None = None,
        currency: str = "",
        prefilter: Callable[[OptionInstrument], bool] | None = None,
    ) -> list[tuple[OptionInstrument, OrderBookSnapshot]]:
        """OTM calls first (ascending strike) so scan examples show relevant strikes above spot."""
        scoped = calls
        if prefilter is not None:
            scoped = [inst for inst in scoped if prefilter(inst)]
        elif index_price is not None and index_price > 0 and currency:
            scoped = [inst for inst in scoped if inst.strike > index_price]
        pairs = [(inst, orderbook_loader(inst.instrument_name)) for inst in scoped]
        otm = sorted(
            (pair for pair in pairs if StrategySelector._is_otm_call(*pair)),
            key=lambda pair: pair[0].strike,
        )
        itm = sorted(
            (pair for pair in pairs if not StrategySelector._is_otm_call(*pair)),
            key=lambda pair: pair[0].strike,
        )
        return otm + itm

    @staticmethod
    def _otm_ratio(instrument: OptionInstrument, book: OrderBookSnapshot, option_type: str) -> Decimal:
        if option_type == "call":
            return StrategySelector._call_otm_ratio(instrument, book)
        return StrategySelector._put_otm_ratio(instrument, book)

    def _instrument_active(self, instrument: OptionInstrument) -> bool:
        s = (instrument.instrument_state or "").lower()
        return s in {"open", "active", ""}

    def _common_book_rejection_reason(
        self,
        currency: str,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
    ) -> str | None:
        """Gates shared by both short-put and short-call entry.

        Currency / active / index / bid / ask / cross / liquidity checks live
        here. Caller then layers on side-specific delta, option_type, and OTM
        checks. Keeping these in one place lets scan telemetry report
        consistent reason labels for both sides and avoids the prior
        duplication between the two rejection helpers.
        """
        if instrument.base_currency.upper() != currency.upper():
            return "wrong_base_currency"
        if not self._instrument_active(instrument):
            return "instrument_not_active"
        if book.index_price <= 0:
            return "index_price<=0"
        if book.best_bid_price <= 0:
            return "best_bid<=0"
        if book.best_bid_amount <= 0:
            return "bid_size<=0"
        if book.best_ask_price <= 0:
            return "best_ask<=0"
        if book.best_ask_price < book.best_bid_price:
            return "crossed_book"
        min_oi, max_spread, min_notional = self.config.liquidity_gates(
            instrument.instrument_type, instrument.base_currency
        )
        if book.open_interest < min_oi:
            return "open_interest_below_min"
        if book.book_notional_usdc < min_notional:
            return "book_notional_below_min"
        if book.spread_ratio > max_spread:
            return "spread_ratio_above_max"
        return None

    def _naked_short_put_rejection_reason(
        self,
        currency: str,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
    ) -> str | None:
        if instrument.option_type != OptionSide.PUT.value:
            return "not_put"
        common = self._common_book_rejection_reason(currency, instrument, book)
        if common is not None:
            return common
        abs_delta = abs(book.delta)
        dmin, dmax = self.config.put_delta_bounds(currency)
        if not (dmin <= abs_delta <= dmax):
            return "delta_out_of_range"
        otm = self._put_otm_ratio(instrument, book)
        omin, omax = self.config.put_otm_bounds(currency)
        if not (omin <= otm <= omax):
            return "otm_out_of_range"
        return None

    def _is_valid_naked_short_put(self, currency: str, instrument: OptionInstrument, book: OrderBookSnapshot) -> bool:
        return self._naked_short_put_rejection_reason(currency, instrument, book) is None

    def _naked_short_call_rejection_reason(
        self,
        currency: str,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
    ) -> str | None:
        if instrument.option_type != OptionSide.CALL.value:
            return "not_call"
        common = self._common_book_rejection_reason(currency, instrument, book)
        if common is not None:
            return common
        abs_delta = abs(book.delta)
        dmin, dmax = self.config.call_delta_bounds(currency)
        if not (dmin <= abs_delta <= dmax):
            return "delta_out_of_range"
        otm = self._call_otm_ratio(instrument, book)
        omin, omax = self.config.call_otm_bounds(currency)
        if not (omin <= otm <= omax):
            return "otm_out_of_range"
        return None

    def _is_valid_naked_short_call(self, currency: str, instrument: OptionInstrument, book: OrderBookSnapshot) -> bool:
        return self._naked_short_call_rejection_reason(currency, instrument, book) is None

    def _naked_short_option_rejection_reason(
        self,
        currency: str,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        *,
        option_type: str,
    ) -> str | None:
        if option_type == "call":
            return self._naked_short_call_rejection_reason(currency, instrument, book)
        return self._naked_short_put_rejection_reason(currency, instrument, book)

    def _core_option_side_liquidity_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        option_type: str,
    ) -> tuple[bool, list[str]]:
        side = option_type.lower()
        if side not in {"put", "call"}:
            return False, [f"unsupported_regime_liquidity_side={side}"]
        option_value = OptionSide.PUT.value if side == "put" else OptionSide.CALL.value
        is_valid = self._is_valid_naked_short_put if side == "put" else self._is_valid_naked_short_call
        instruments = [
            item
            for item in markets
            if item.option_type == option_value
            and self._instrument_active(item)
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
            and (side == "put" or item.strike > 0)
        ]
        expiries = sorted({item.expiration_timestamp_ms for item in instruments})
        required = self.config.min_liquid_expiries_required
        if len(expiries) < required:
            return False, [
                f"{side}: expiries_in_dte_window({self.config.entry_dte_min}-{self.config.entry_dte_max}d): "
                f"count={len(expiries)} < min_liquid_expiries_required={required}",
            ]
        liquid_expiries = 0
        dry_notes: list[str] = []
        for expiry in expiries:
            if any(
                is_valid(currency, item, orderbook_loader(item.instrument_name))
                for item in instruments
                if item.expiration_timestamp_ms == expiry
            ):
                liquid_expiries += 1
            else:
                dry_notes.append(f"expiry={expiry}: no_valid_{side}")
        if liquid_expiries < required:
            notes = [f"{side}: liquid_expiries={liquid_expiries} < required={required}"]
            notes.extend(dry_notes[:5])
            return False, notes
        return True, []

    def _core_bull_put_spread_liquidity_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
    ) -> tuple[bool, list[str]]:
        markets_tuple = tuple(markets)
        puts = [
            item
            for item in markets_tuple
            if item.option_type == OptionSide.PUT.value
            and self._instrument_active(item)
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        expiries = sorted({item.expiration_timestamp_ms for item in puts})
        required = self.config.min_liquid_expiries_required
        if len(expiries) < required:
            return False, [
                f"bull_put_spread: expiries_in_dte_window({self.config.entry_dte_min}-{self.config.entry_dte_max}d): "
                f"count={len(expiries)} < min_liquid_expiries_required={required}",
            ]
        liquid_expiries = 0
        dry_notes: list[str] = []
        for expiry in expiries:
            spread_ok = False
            for item in puts:
                if item.expiration_timestamp_ms != expiry:
                    continue
                book = orderbook_loader(item.instrument_name)
                if self._naked_short_put_rejection_reason(currency, item, book) is not None:
                    continue
                short_leg = self._short_put_spread_leg(
                    instrument=item,
                    book=book,
                    quantity=item.min_trade_amount,
                    entry_price=book.best_bid_price,
                    target_price=book.best_bid_price,
                )
                if self._long_put_candidates_for_short(
                    currency=currency,
                    markets=markets_tuple,
                    orderbook_loader=orderbook_loader,
                    short_leg=short_leg,
                ):
                    spread_ok = True
                    break
            if spread_ok:
                liquid_expiries += 1
            else:
                dry_notes.append(f"expiry={expiry}: no_valid_bull_put_spread_pair")
        if liquid_expiries < required:
            notes = [f"bull_put_spread: liquid_expiries={liquid_expiries} < required={required}"]
            notes.extend(dry_notes[:5])
            return False, notes
        return True, []

    def core_regime_liquidity_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
    ) -> tuple[bool, list[str]]:
        """Strategy-aware liquidity probe used before macro regime escalation."""
        if self.config.option_strategy == "bull_put_spread":
            return self._core_bull_put_spread_liquidity_detail(currency, markets, orderbook_loader)

        sides = self.config.regime_entry_option_sides()
        if not sides:
            return False, ["no_regime_liquidity_sides_configured"]

        failures: list[str] = []
        for side in sides:
            ok, notes = self._core_option_side_liquidity_detail(
                currency,
                markets,
                orderbook_loader,
                side,
            )
            if ok:
                return True, []
            failures.extend(notes)
        return False, failures

    def core_naked_liquidity_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
    ) -> tuple[bool, list[str]]:
        return self.core_regime_liquidity_detail(currency, markets, orderbook_loader)

    def naked_put_sort_key(self, candidate: NakedPutCandidate) -> tuple:
        option_type = candidate.option_type or "put"
        pdmin, pdmax = self.config.preferred_delta_bounds(candidate.currency, option_type)
        pomin, pomax = self.config.preferred_otm_bounds(candidate.currency, option_type)
        target_delta = (pdmin + pdmax) / Decimal("2")
        target_otm = (pomin + pomax) / Decimal("2")
        in_band = candidate.in_target_apr_band
        preferred_delta = pdmin <= abs(candidate.short_leg.delta) <= pdmax
        otm = candidate._otm_ratio()
        preferred_otm = pomin <= otm <= pomax
        return (
            0 if in_band else 1,
            0 if preferred_delta else 1,
            0 if preferred_otm else 1,
            abs(abs(candidate.short_leg.delta) - target_delta),
            abs(otm - target_otm),
            -candidate.margin_efficiency,
            self.short_spread_ratio_or_zero(candidate),
            -candidate.screening_bid * candidate.quantity,
            -candidate.net_apr,
        )

    def take_top_scan_candidates(
        self,
        candidates: list[NakedPutCandidate],
        *,
        limit: int,
    ) -> list[NakedPutCandidate]:
        """Return up to ``limit`` scan rows sorted by ``naked_put_sort_key``.

        Puts and calls are ranked together by the same sort key. When the
        scanner runs in ``SHORT_OPTION_SIDE=both`` mode the rank is decided by
        the underlying score (APR band, preferred delta/OTM, margin
        efficiency, etc.) regardless of option side — calls and puts are not
        artificially balanced.
        """
        if limit <= 0 or not candidates:
            return []
        return sorted(candidates, key=self.naked_put_sort_key)[:limit]

    @staticmethod
    def short_spread_ratio_or_zero(candidate: NakedPutCandidate) -> Decimal:
        bid = candidate.short_leg.best_bid_price
        ask = candidate.short_leg.best_ask_price
        mid = (bid + ask) / Decimal("2")
        if mid <= 0 or ask < bid:
            return Decimal("1")
        return (ask - bid) / mid

    def _option_fee_native(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        premium: Decimal,
        quantity: Decimal,
    ) -> Decimal:
        return option_trade_fee_native(
            index_price=book.index_price,
            premium=premium,
            quantity=quantity,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
            quote_currency=instrument.quote_currency,
            settlement_currency=instrument.settlement_currency,
        )

    @staticmethod
    def _premium_usdc_for_leg(
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        premium: Decimal,
        quantity: Decimal,
    ) -> Decimal:
        return premium_value_usdc(
            index_price=book.index_price,
            premium=premium,
            quantity=quantity,
            base_currency=instrument.base_currency,
            quote_currency=instrument.quote_currency,
            settlement_currency=instrument.settlement_currency,
        )

    def _short_put_spread_leg(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        quantity: Decimal,
        entry_price: Decimal,
        target_price: Decimal,
    ) -> SpreadLeg:
        return SpreadLeg(
            instrument_name=instrument.instrument_name,
            strike=instrument.strike,
            quantity=quantity,
            min_trade_amount=instrument.min_trade_amount,
            contract_size=instrument.contract_size,
            entry_price=entry_price,
            target_price=target_price,
            best_bid_price=book.best_bid_price,
            best_ask_price=book.best_ask_price,
            delta=book.delta,
            tick_size=instrument.tick_size,
            tick_size_steps=instrument.tick_size_steps,
            expiration_timestamp_ms=instrument.expiration_timestamp_ms,
            index_price=book.index_price,
            quote_currency=instrument.quote_currency,
            settlement_currency=instrument.settlement_currency,
            instrument_type=instrument.instrument_type,
        )

    def refresh_naked_put_candidate(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        if quantity <= 0:
            return None, "quantity<=0"
        rej = self._naked_short_put_rejection_reason(currency, instrument, book)
        if rej is not None:
            return None, f"rejection:{rej}"
        cand, breason = self._try_build_naked_put_for_quantity(
            instrument=instrument,
            book=book,
            regime=regime,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            collateral_currency=collateral_currency,
            currency=currency,
            quantity=quantity,
            existing_im_for_expiry=existing_im_for_expiry,
            relax_apr=False,
        )
        if cand is None:
            return None, breason or "build_failed"
        return cand, None

    def refresh_naked_call_candidate(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        if quantity <= 0:
            return None, "quantity<=0"
        rej = self._naked_short_call_rejection_reason(currency, instrument, book)
        if rej is not None:
            return None, f"rejection:{rej}"
        cand, breason = self._try_build_naked_call_for_quantity(
            instrument=instrument,
            book=book,
            regime=regime,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            collateral_currency=collateral_currency,
            currency=currency,
            quantity=quantity,
            existing_im_for_expiry=existing_im_for_expiry,
            relax_apr=False,
        )
        if cand is None:
            return None, breason or "build_failed"
        return cand, None

    def refresh_covered_call_candidate(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        summary_equity: Decimal,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        if quantity <= 0:
            return None, "quantity<=0"
        rej = self._naked_short_call_rejection_reason(currency, instrument, book)
        if rej is not None:
            return None, f"rejection:{rej}"
        cand, breason = self._try_build_covered_call_for_quantity(
            instrument=instrument,
            book=book,
            regime=regime,
            collateral_currency=collateral_currency,
            currency=currency,
            quantity=quantity,
            summary_equity=summary_equity,
        )
        if cand is None:
            return None, breason or "build_failed"
        return cand, None

    def refresh_naked_candidate(
        self,
        *,
        option_type: str,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        """Dispatch re-check to the correct side builder based on ``option_type``.

        The naked execution loop (scan → place → re-check for re-price) must
        rebuild the candidate against a fresh book before every retry. Using
        the put-only refresh for a call candidate trips the ``not_put`` guard
        in ``_naked_short_put_rejection_reason`` and silently aborts entry
        with ``candidate_failed_recheck`` — callers should route through this
        dispatcher so both sides re-validate against their own gates.

        Returns ``(candidate, None)`` on success, or ``(None, detail)`` where
        ``detail`` names the first failing gate (e.g. ``rejection:spread_ratio_above_max``,
        ``net_apr_below_min``).
        """
        side = (option_type or "put").lower()
        if side == "call":
            return self.refresh_naked_call_candidate(
                instrument=instrument,
                book=book,
                regime=regime,
                summary_equity=summary_equity,
                summary_maintenance_margin=summary_maintenance_margin,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                existing_im_for_expiry=existing_im_for_expiry,
            )
        return self.refresh_naked_put_candidate(
            instrument=instrument,
            book=book,
            regime=regime,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            collateral_currency=collateral_currency,
            currency=currency,
            quantity=quantity,
            existing_im_for_expiry=existing_im_for_expiry,
        )

    def _try_build_naked_put_for_quantity(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
        relax_apr: bool,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        screening_bid = book.best_bid_price
        mark = book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
        if mark <= 0:
            return None, "mark<=0"
        usdc_collateral = collateral_currency.upper() == "USDC"
        if usdc_collateral:
            fee_1 = option_trade_fee_native(
                index_price=book.index_price,
                premium=screening_bid,
                quantity=Decimal("1"),
                fee_rate=self.config.option_fee_rate,
                fee_cap_rate=self.config.option_fee_cap_rate,
                quote_currency="USDC",
                settlement_currency="USDC",
            )
        else:
            fee_1 = inverse_option_fee_native_per_contract(
                premium=screening_bid,
                fee_rate=self.config.option_fee_rate,
                fee_cap_rate=self.config.option_fee_cap_rate,
            )
        net_prem = screening_bid * quantity
        fee_native = fee_1 * quantity
        net_credit = net_prem - fee_native
        if net_credit <= 0:
            return None, "net_credit<=0"
        if summary_equity <= 0:
            return None, "summary_equity<=0"
        net_apr = self._screening_net_apr(
            instrument=instrument,
            book=book,
            premium_per_contract=screening_bid,
            collateral_currency=collateral_currency,
            option_type="put",
        )
        if not relax_apr and net_apr < self.config.min_net_apr:
            return None, "net_apr_below_min"
        if usdc_collateral:
            im_1 = linear_usdc_short_put_initial_per_contract_usdc(
                index_price=book.index_price,
                strike=instrument.strike,
                mark_usdc=mark,
                contract_size=instrument.contract_size,
            )
            mm_1 = linear_usdc_short_put_mm_per_contract_usdc(
                index_price=book.index_price,
                strike=instrument.strike,
                mark_usdc=mark,
                contract_size=instrument.contract_size,
            )
            im_total = im_1 * quantity
            mm_total = mm_1 * quantity
        else:
            im_u = short_put_initial_unit(index_price=book.index_price, strike=instrument.strike, mark_price=mark)
            mm_u = short_put_maintenance_unit(index_price=book.index_price, strike=instrument.strike, mark_price=mark)
            im_total = im_u * quantity
            mm_total = mm_u * quantity
        per_leg_cap = self.config.per_leg_im_cap(currency)
        if im_total > summary_equity * per_leg_cap:
            return None, "per_leg_im_cap"
        exp_cap = self.config.expiry_im_cap(currency)
        if existing_im_for_expiry + im_total > summary_equity * exp_cap:
            return None, "expiry_im_cap"
        hard_mm = self.config.hard_mm_utilization(currency)
        if summary_maintenance_margin + mm_total > summary_equity * hard_mm:
            return None, "hard_mm_utilization"
        margin_efficiency = (net_prem - fee_native) / im_total if im_total > 0 else Decimal("0")
        instrument_for_price = instrument
        target_price = self.sell_mid_price(instrument_for_price, book)
        short_leg = self._short_put_spread_leg(
            instrument=instrument,
            book=book,
            quantity=quantity,
            entry_price=self.sell_taker_price(instrument_for_price, book),
            target_price=target_price,
        )
        pdmin, pdmax = self.config.preferred_put_delta_bounds(currency)
        pomin, pomax = self.config.preferred_put_otm_bounds(currency)
        otm = self._put_otm_ratio(instrument, book)
        preferred_delta = pdmin <= abs(book.delta) <= pdmax
        preferred_otm = pomin <= otm <= pomax
        in_band = self.config.target_net_apr_min <= net_apr <= self.config.target_net_apr_max
        return (
            NakedPutCandidate(
                currency=currency,
                collateral_currency=collateral_currency,
                quantity=quantity,
                dte_days=instrument.dte_days(),
                short_leg=short_leg,
                screening_bid=screening_bid,
                screening_mark=mark,
                target_limit_price=target_price,
                net_premium_native=net_prem - fee_native,
                fee_native=fee_native,
                net_apr=net_apr,
                margin_efficiency=margin_efficiency,
                estimated_im_total=im_total,
                estimated_mm_total=mm_total,
                regime=regime,
                preferred_delta=preferred_delta,
                preferred_otm=preferred_otm,
                in_target_apr_band=in_band,
            ),
            None,
        )

    def _build_naked_put_for_quantity(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
        relax_apr: bool,
    ) -> NakedPutCandidate | None:
        cand, _reason = self._try_build_naked_put_for_quantity(
            instrument=instrument,
            book=book,
            regime=regime,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            collateral_currency=collateral_currency,
            currency=currency,
            quantity=quantity,
            existing_im_for_expiry=existing_im_for_expiry,
            relax_apr=relax_apr,
        )
        return cand

    def naked_put_scan_rejection_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        existing_im_by_expiry: dict[int, Decimal],
        index_price: Decimal | None = None,
    ) -> dict[str, Any]:
        """Aggregate why naked puts are rejected (liquidity screen + sizing + build gates); for scan JSON."""
        puts = [
            item
            for item in markets
            if item.option_type == OptionSide.PUT.value
            and self._instrument_active(item)
            and item.strike > 0
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        liquidity_counts: Counter[str] = Counter()
        after_counts: Counter[str] = Counter()
        examples: list[str] = []
        max_examples = 10
        buildable = 0
        buildable_names: list[str] = []
        usdc_collateral = collateral_currency.upper() == "USDC"

        ref_index = index_price if index_price is not None else Decimal("0")
        for inst in puts:
            if ref_index > 0 and not self._passes_strike_otm_prefilter(
                inst, currency=currency, index_price=ref_index, option_type="put"
            ):
                liquidity_counts["otm_out_of_range"] += 1
                continue
            book = orderbook_loader(inst.instrument_name)
            liq = self._naked_short_put_rejection_reason(currency, inst, book)
            if liq is not None:
                liquidity_counts[liq] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [liquidity] {liq}")
                continue

            mark = (
                book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
            )
            if mark <= 0 or book.index_price <= 0:
                after_counts["mark_or_index_invalid"] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [post] mark_or_index_invalid")
                continue

            if usdc_collateral:
                im_1 = linear_usdc_short_put_initial_per_contract_usdc(
                    index_price=book.index_price,
                    strike=inst.strike,
                    mark_usdc=mark,
                    contract_size=inst.contract_size,
                )
                mm_1 = linear_usdc_short_put_mm_per_contract_usdc(
                    index_price=book.index_price,
                    strike=inst.strike,
                    mark_usdc=mark,
                    contract_size=inst.contract_size,
                )
            else:
                im_1 = short_put_initial_unit(index_price=book.index_price, strike=inst.strike, mark_price=mark)
                mm_1 = short_put_maintenance_unit(index_price=book.index_price, strike=inst.strike, mark_price=mark)
            if im_1 <= 0:
                after_counts["im_per_contract_nonpositive"] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [post] im_per_contract_nonpositive")
                continue

            per_leg_cap = self.config.per_leg_im_cap(currency)
            exp_cap = self.config.expiry_im_cap(currency)
            hard_mm = self.config.hard_mm_utilization(currency)
            max_by_im_leg = floor_to_step((summary_equity * per_leg_cap) / im_1, inst.min_trade_amount)
            exp_key = inst.expiration_timestamp_ms
            existing_im = existing_im_by_expiry.get(exp_key, Decimal("0"))
            max_by_expiry = floor_to_step((summary_equity * exp_cap - existing_im) / im_1, inst.min_trade_amount)
            max_by_mm = floor_to_step(
                (summary_equity * hard_mm - summary_maintenance_margin) / mm_1, inst.min_trade_amount
            )
            max_by_liquidity = floor_to_step(book.best_bid_amount, inst.min_trade_amount)
            quantity = min(max_by_im_leg, max_by_expiry, max_by_mm, max_by_liquidity)
            if quantity < inst.min_trade_amount:
                after_counts["quantity_below_min_trade"] += 1
                if len(examples) < max_examples:
                    caps = (
                        ("im_leg", max_by_im_leg),
                        ("exp", max_by_expiry),
                        ("mm", max_by_mm),
                        ("liq", max_by_liquidity),
                    )
                    binding = min(caps, key=lambda kv: kv[1])[0]
                    examples.append(
                        f"{inst.instrument_name} [post] quantity_below_min_trade "
                        f"bind={binding} q={format_decimal(quantity, 8)} "
                        f"im_leg={format_decimal(max_by_im_leg, 8)} "
                        f"exp={format_decimal(max_by_expiry, 8)} "
                        f"mm={format_decimal(max_by_mm, 8)} "
                        f"liq={format_decimal(max_by_liquidity, 8)} "
                        f"min={format_decimal(inst.min_trade_amount, 8)} "
                        f"eq={format_decimal(summary_equity, 8)}"
                    )
                continue

            cand, breason = self._try_build_naked_put_for_quantity(
                instrument=inst,
                book=book,
                regime=regime,
                summary_equity=summary_equity,
                summary_maintenance_margin=summary_maintenance_margin,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                existing_im_for_expiry=existing_im,
                relax_apr=False,
            )
            if cand is not None:
                buildable += 1
                buildable_names.append(inst.instrument_name)
            elif breason is not None:
                after_counts[breason] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [build] {breason}")

        distinct_expiries = len({p.expiration_timestamp_ms for p in puts})
        note = (
            "目前是 crisis 風控狀態，實盤不會產生可下單候選；下方統計只用來看 orderbook 資料與篩選門檻卡在哪裡。"
            if regime is RiskRegime.CRISIS
            else None
        )
        return {
            "currency": currency,
            "regime": regime.value,
            "puts_in_dte_window": len(puts),
            "distinct_expiries_in_dte_window": distinct_expiries,
            "liquidity_rejections": {k: liquidity_counts[k] for k, _ in liquidity_counts.most_common(16)},
            "after_liquidity_rejections": {k: after_counts[k] for k, _ in after_counts.most_common(16)},
            "instruments_passing_all_build_gates": buildable,
            "instrument_names_passing_all_build_gates": buildable_names,
            "example_messages": examples,
            "note": note,
        }

    def naked_call_scan_rejection_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        existing_im_by_expiry: dict[int, Decimal],
        index_price: Decimal | None = None,
    ) -> dict[str, Any]:
        """Aggregate why naked calls are rejected; mirrors naked_put_scan_rejection_detail."""
        calls = [
            item
            for item in markets
            if item.option_type == OptionSide.CALL.value
            and self._instrument_active(item)
            and item.strike > 0
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        liquidity_counts: Counter[str] = Counter()
        after_counts: Counter[str] = Counter()
        examples: list[str] = []
        max_examples = 10
        buildable = 0
        buildable_names: list[str] = []
        usdc_collateral = collateral_currency.upper() == "USDC"

        ref_index = index_price if index_price is not None else Decimal("0")
        for inst in calls:
            if ref_index > 0 and not self._passes_strike_otm_prefilter(
                inst, currency=currency, index_price=ref_index, option_type="call"
            ):
                liquidity_counts["otm_out_of_range"] += 1
                continue
            book = orderbook_loader(inst.instrument_name)
            liq = self._naked_short_call_rejection_reason(currency, inst, book)
            if liq is not None:
                liquidity_counts[liq] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [liquidity] {liq}")
                continue

            mark = (
                book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
            )
            if mark <= 0 or book.index_price <= 0:
                after_counts["mark_or_index_invalid"] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [post] mark_or_index_invalid")
                continue

            im_1, mm_1 = self._short_call_unit_margin(
                instrument=inst, book=book, mark=mark, collateral_currency=collateral_currency
            )
            if im_1 <= 0:
                after_counts["im_per_contract_nonpositive"] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [post] im_per_contract_nonpositive")
                continue

            per_leg_cap = self.config.per_leg_im_cap(currency, option_type="call")
            exp_cap = self.config.expiry_im_cap(currency)
            hard_mm = self.config.hard_mm_utilization(currency)
            max_by_im_leg = floor_to_step((summary_equity * per_leg_cap) / im_1, inst.min_trade_amount)
            exp_key = inst.expiration_timestamp_ms
            existing_im = existing_im_by_expiry.get(exp_key, Decimal("0"))
            max_by_expiry = floor_to_step((summary_equity * exp_cap - existing_im) / im_1, inst.min_trade_amount)
            max_by_mm = floor_to_step(
                (summary_equity * hard_mm - summary_maintenance_margin) / mm_1, inst.min_trade_amount
            )
            max_by_liquidity = floor_to_step(book.best_bid_amount, inst.min_trade_amount)
            quantity = min(max_by_im_leg, max_by_expiry, max_by_mm, max_by_liquidity)
            if quantity < inst.min_trade_amount:
                after_counts["quantity_below_min_trade"] += 1
                if len(examples) < max_examples:
                    caps = (
                        ("im_leg", max_by_im_leg),
                        ("exp", max_by_expiry),
                        ("mm", max_by_mm),
                        ("liq", max_by_liquidity),
                    )
                    binding = min(caps, key=lambda kv: kv[1])[0]
                    examples.append(
                        f"{inst.instrument_name} [post] quantity_below_min_trade "
                        f"bind={binding} q={format_decimal(quantity, 8)} "
                        f"im_leg={format_decimal(max_by_im_leg, 8)} "
                        f"exp={format_decimal(max_by_expiry, 8)} "
                        f"mm={format_decimal(max_by_mm, 8)} "
                        f"liq={format_decimal(max_by_liquidity, 8)} "
                        f"min={format_decimal(inst.min_trade_amount, 8)} "
                        f"eq={format_decimal(summary_equity, 8)}"
                    )
                continue

            cand, breason = self._try_build_naked_call_for_quantity(
                instrument=inst,
                book=book,
                regime=regime,
                summary_equity=summary_equity,
                summary_maintenance_margin=summary_maintenance_margin,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                existing_im_for_expiry=existing_im,
                relax_apr=False,
            )
            if cand is not None:
                buildable += 1
                buildable_names.append(inst.instrument_name)
            elif breason is not None:
                after_counts[breason] += 1
                if len(examples) < max_examples:
                    examples.append(f"{inst.instrument_name} [build] {breason}")

        distinct_expiries = len({c.expiration_timestamp_ms for c in calls})
        note = (
            "目前是 crisis 風控狀態，實盤不會產生可下單候選；下方統計只用來看 orderbook 資料與篩選門檻卡在哪裡。"
            if regime is RiskRegime.CRISIS
            else None
        )
        return {
            "currency": currency,
            "regime": regime.value,
            "calls_in_dte_window": len(calls),
            "distinct_expiries_in_dte_window": distinct_expiries,
            "liquidity_rejections": {k: liquidity_counts[k] for k, _ in liquidity_counts.most_common(16)},
            "after_liquidity_rejections": {k: after_counts[k] for k, _ in after_counts.most_common(16)},
            "instruments_passing_all_build_gates": buildable,
            "instrument_names_passing_all_build_gates": buildable_names,
            "example_messages": examples,
            "note": note,
        }

    def build_naked_short_call_candidates(
        self,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        existing_im_by_expiry: dict[int, Decimal],
        index_price: Decimal | None = None,
    ) -> list[NakedPutCandidate]:
        """Build short-call candidates (same NakedPutCandidate class with option_type=call)."""
        if regime is RiskRegime.CRISIS:
            return []
        if summary_equity <= 0:
            return []
        calls = [
            item
            for item in markets
            if item.option_type == OptionSide.CALL.value
            and self._instrument_active(item)
            and item.strike > 0
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        candidates: list[NakedPutCandidate] = []
        ref_index = index_price if index_price is not None else Decimal("0")
        for inst in calls:
            if ref_index > 0 and not self._passes_strike_otm_prefilter(
                inst, currency=currency, index_price=ref_index, option_type="call"
            ):
                continue
            book = orderbook_loader(inst.instrument_name)
            if self._naked_short_call_rejection_reason(currency, inst, book) is not None:
                continue
            mark = (
                book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
            )
            if mark <= 0 or book.index_price <= 0:
                continue
            im_1, mm_1 = self._short_call_unit_margin(
                instrument=inst, book=book, mark=mark, collateral_currency=collateral_currency
            )
            if im_1 <= 0:
                continue
            per_leg_cap = self.config.per_leg_im_cap(currency, option_type="call")
            exp_cap = self.config.expiry_im_cap(currency)
            hard_mm = self.config.hard_mm_utilization(currency)
            max_by_im_leg = floor_to_step((summary_equity * per_leg_cap) / im_1, inst.min_trade_amount)
            exp_key = inst.expiration_timestamp_ms
            existing_im = existing_im_by_expiry.get(exp_key, Decimal("0"))
            max_by_expiry = floor_to_step((summary_equity * exp_cap - existing_im) / im_1, inst.min_trade_amount)
            max_by_mm = floor_to_step(
                (summary_equity * hard_mm - summary_maintenance_margin) / mm_1, inst.min_trade_amount
            )
            max_by_liquidity = floor_to_step(book.best_bid_amount, inst.min_trade_amount)
            quantity = min(max_by_im_leg, max_by_expiry, max_by_mm, max_by_liquidity)
            if quantity < inst.min_trade_amount:
                continue
            cand, _reason = self._try_build_naked_call_for_quantity(
                instrument=inst,
                book=book,
                regime=regime,
                summary_equity=summary_equity,
                summary_maintenance_margin=summary_maintenance_margin,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                existing_im_for_expiry=existing_im,
                relax_apr=False,
            )
            if cand is not None:
                candidates.append(cand)
        return sorted(candidates, key=self.naked_put_sort_key)

    def _long_put_candidates_for_short(
        self,
        *,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        short_leg: SpreadLeg,
    ) -> list[tuple[OptionInstrument, OrderBookSnapshot]]:
        dmin = self.config.bull_put_long_delta_min
        dmax = self.config.bull_put_long_delta_max
        target = (dmin + dmax) / Decimal("2")
        matches: list[tuple[OptionInstrument, OrderBookSnapshot]] = []
        for inst in markets:
            if inst.option_type != OptionSide.PUT.value:
                continue
            if not self._instrument_active(inst):
                continue
            if inst.base_currency.upper() != currency.upper():
                continue
            if inst.expiration_timestamp_ms != short_leg.expiration_timestamp_ms:
                continue
            if inst.strike <= 0 or inst.strike >= short_leg.strike:
                continue
            book = orderbook_loader(inst.instrument_name)
            if inst.base_currency.upper() != currency.upper():
                continue
            if book.index_price <= 0:
                continue
            min_oi, _max_spread, min_notional = self.config.liquidity_gates(inst.instrument_type, inst.base_currency)
            if book.open_interest < min_oi or book.book_notional_usdc < min_notional:
                continue
            abs_delta = abs(book.delta)
            if not (dmin <= abs_delta <= dmax):
                continue
            if book.best_ask_price <= 0 or book.best_ask_amount < inst.min_trade_amount:
                continue
            matches.append((inst, book))
        return sorted(matches, key=lambda item: (abs(abs(item[1].delta) - target), item[1].best_ask_price))

    def _try_build_bull_put_spread_for_quantity(
        self,
        *,
        short_candidate: NakedPutCandidate,
        short_instrument: OptionInstrument,
        short_book: OrderBookSnapshot,
        long_instrument: OptionInstrument,
        long_book: OrderBookSnapshot,
        quantity: Decimal,
        summary_equity: Decimal,
        existing_im_for_expiry: Decimal,
    ) -> NakedPutCandidate | None:
        if quantity <= 0:
            return None
        short_price = short_book.best_bid_price
        long_price = long_book.best_ask_price
        if short_price <= 0 or long_price <= 0:
            return None
        short_fee = self._option_fee_native(
            instrument=short_instrument,
            book=short_book,
            premium=short_price,
            quantity=quantity,
        )
        long_fee = self._option_fee_native(
            instrument=long_instrument,
            book=long_book,
            premium=long_price,
            quantity=quantity,
        )
        gross_credit_native = (short_price - long_price) * quantity
        fees_native = short_fee + long_fee
        net_credit_native = gross_credit_native - fees_native
        if net_credit_native <= 0:
            return None

        width_usdc = max(short_instrument.strike - long_instrument.strike, Decimal("0")) * quantity
        short_credit_usdc = self._premium_usdc_for_leg(
            instrument=short_instrument,
            book=short_book,
            premium=short_price,
            quantity=quantity,
        )
        long_debit_usdc = self._premium_usdc_for_leg(
            instrument=long_instrument,
            book=long_book,
            premium=long_price,
            quantity=quantity,
        )
        fee_usdc = (
            fees_native
            if short_candidate.collateral_currency.upper() == "USDC"
            else fees_native * short_book.index_price
        )
        net_credit_usdc = short_credit_usdc - long_debit_usdc - fee_usdc
        max_loss_usdc = width_usdc - net_credit_usdc
        if max_loss_usdc <= 0:
            return None
        max_loss_collateral = (
            max_loss_usdc
            if short_candidate.collateral_currency.upper() == "USDC"
            else max_loss_usdc / short_book.index_price
        )
        if max_loss_collateral <= 0 or summary_equity <= 0:
            return None
        per_leg_cap = self.config.per_leg_im_cap(short_candidate.currency, option_type="put")
        if max_loss_collateral > summary_equity * per_leg_cap:
            return None
        exp_cap = self.config.expiry_im_cap(short_candidate.currency)
        if existing_im_for_expiry + max_loss_collateral > summary_equity * exp_cap:
            return None

        dte = short_instrument.dte_days()
        exit_short_fee = self._option_fee_native(
            instrument=short_instrument,
            book=short_book,
            premium=short_price,
            quantity=quantity,
        )
        exit_long_fee = self._option_fee_native(
            instrument=long_instrument,
            book=long_book,
            premium=long_price,
            quantity=quantity,
        )
        round_trip_net = gross_credit_native - fees_native - exit_short_fee - exit_long_fee
        net_apr = self._screening_net_apr(
            instrument=short_instrument,
            book=short_book,
            premium_per_contract=Decimal("0"),
            collateral_currency=short_candidate.collateral_currency,
            option_type="put",
            net_credit_on_capital=round_trip_net,
            capital_base=max_loss_collateral,
        )
        if net_apr < self.config.min_net_apr:
            return None
        long_leg = self._short_put_spread_leg(
            instrument=long_instrument,
            book=long_book,
            quantity=quantity,
            entry_price=self.buy_taker_price(long_instrument, long_book),
            target_price=self.buy_mid_price(long_instrument, long_book),
        )
        short_leg = self._short_put_spread_leg(
            instrument=short_instrument,
            book=short_book,
            quantity=quantity,
            entry_price=self.sell_taker_price(short_instrument, short_book),
            target_price=self.sell_mid_price(short_instrument, short_book),
        )
        pdmin, pdmax = self.config.preferred_put_delta_bounds(short_candidate.currency)
        pomin, pomax = self.config.preferred_put_otm_bounds(short_candidate.currency)
        otm = self._put_otm_ratio(short_instrument, short_book)
        return NakedPutCandidate(
            currency=short_candidate.currency,
            collateral_currency=short_candidate.collateral_currency,
            quantity=quantity,
            dte_days=short_instrument.dte_days(),
            short_leg=short_leg,
            screening_bid=short_price,
            screening_mark=short_book.mark_price
            if short_book.mark_price > 0
            else (short_book.best_bid_price + short_book.best_ask_price) / Decimal("2"),
            target_limit_price=short_leg.target_price,
            net_premium_native=net_credit_native,
            fee_native=fees_native,
            net_apr=net_apr,
            margin_efficiency=net_credit_native / max_loss_collateral,
            estimated_im_total=max_loss_collateral,
            estimated_mm_total=max_loss_collateral,
            regime=short_candidate.regime,
            preferred_delta=pdmin <= abs(short_book.delta) <= pdmax,
            preferred_otm=pomin <= otm <= pomax,
            in_target_apr_band=self.config.target_net_apr_min <= net_apr <= self.config.target_net_apr_max,
            option_type="put",
            strategy="bull_put_spread",
            long_leg=long_leg,
        )

    def build_bull_put_spread_candidates(
        self,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        existing_im_by_expiry: dict[int, Decimal],
    ) -> list[NakedPutCandidate]:
        markets_tuple = tuple(markets)
        short_candidates = self.build_naked_short_put_candidates(
            markets_tuple,
            orderbook_loader,
            regime=regime,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            collateral_currency=collateral_currency,
            currency=currency,
            existing_im_by_expiry=existing_im_by_expiry,
        )
        candidates: list[NakedPutCandidate] = []
        by_name = {item.instrument_name: item for item in markets_tuple}
        for short_candidate in short_candidates:
            short_instrument = by_name.get(short_candidate.short_leg.instrument_name)
            if short_instrument is None:
                continue
            short_book = orderbook_loader(short_instrument.instrument_name)
            long_matches = self._long_put_candidates_for_short(
                currency=currency,
                markets=markets_tuple,
                orderbook_loader=orderbook_loader,
                short_leg=short_candidate.short_leg,
            )
            for long_instrument, long_book in long_matches:
                quantity = min(
                    short_candidate.quantity,
                    floor_to_step(long_book.best_ask_amount, long_instrument.min_trade_amount),
                )
                quantity = floor_to_step(
                    quantity, max(short_instrument.min_trade_amount, long_instrument.min_trade_amount)
                )
                if quantity < max(short_instrument.min_trade_amount, long_instrument.min_trade_amount):
                    continue
                candidate = self._try_build_bull_put_spread_for_quantity(
                    short_candidate=short_candidate,
                    short_instrument=short_instrument,
                    short_book=short_book,
                    long_instrument=long_instrument,
                    long_book=long_book,
                    quantity=quantity,
                    summary_equity=summary_equity,
                    existing_im_for_expiry=existing_im_by_expiry.get(
                        short_instrument.expiration_timestamp_ms, Decimal("0")
                    ),
                )
                if candidate is not None:
                    candidates.append(candidate)
                    break
        return sorted(candidates, key=self.naked_put_sort_key)

    def _short_call_unit_margin(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        mark: Decimal,
        collateral_currency: str,
    ) -> tuple[Decimal, Decimal]:
        usdc_collateral = collateral_currency.upper() == "USDC"
        if usdc_collateral:
            im_1 = linear_usdc_short_call_initial_per_contract_usdc(
                index_price=book.index_price,
                strike=instrument.strike,
                mark_usdc=mark,
                contract_size=instrument.contract_size,
            )
            mm_1 = linear_usdc_short_call_mm_per_contract_usdc(
                index_price=book.index_price,
                strike=instrument.strike,
                mark_usdc=mark,
                contract_size=instrument.contract_size,
            )
        else:
            im_1 = short_call_initial_unit(index_price=book.index_price, strike=instrument.strike, mark_price=mark)
            mm_1 = short_call_maintenance_unit(index_price=book.index_price, strike=instrument.strike, mark_price=mark)
        return im_1, mm_1

    def _try_build_naked_call_for_quantity(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
        relax_apr: bool,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        if quantity <= 0:
            return None, "quantity<=0"
        screening_bid = book.best_bid_price
        mark = book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
        if mark <= 0:
            return None, "mark<=0"
        usdc_collateral = collateral_currency.upper() == "USDC"
        if usdc_collateral:
            fee_1 = option_trade_fee_native(
                index_price=book.index_price,
                premium=screening_bid,
                quantity=Decimal("1"),
                fee_rate=self.config.option_fee_rate,
                fee_cap_rate=self.config.option_fee_cap_rate,
                quote_currency="USDC",
                settlement_currency="USDC",
            )
        else:
            fee_1 = inverse_option_fee_native_per_contract(
                premium=screening_bid,
                fee_rate=self.config.option_fee_rate,
                fee_cap_rate=self.config.option_fee_cap_rate,
            )
        net_prem = screening_bid * quantity
        fee_native = fee_1 * quantity
        net_credit = net_prem - fee_native
        if net_credit <= 0:
            return None, "net_credit<=0"
        if summary_equity <= 0:
            return None, "summary_equity<=0"
        net_apr = self._screening_net_apr(
            instrument=instrument,
            book=book,
            premium_per_contract=screening_bid,
            collateral_currency=collateral_currency,
            option_type="call",
        )
        if not relax_apr and net_apr < self.config.min_net_apr:
            return None, "net_apr_below_min"
        im_1, mm_1 = self._short_call_unit_margin(
            instrument=instrument, book=book, mark=mark, collateral_currency=collateral_currency
        )
        im_total = im_1 * quantity
        mm_total = mm_1 * quantity
        per_leg_cap = self.config.per_leg_im_cap(currency, option_type="call")
        if im_total > summary_equity * per_leg_cap:
            return None, "per_leg_im_cap"
        exp_cap = self.config.expiry_im_cap(currency)
        if existing_im_for_expiry + im_total > summary_equity * exp_cap:
            return None, "expiry_im_cap"
        hard_mm = self.config.hard_mm_utilization(currency)
        if summary_maintenance_margin + mm_total > summary_equity * hard_mm:
            return None, "hard_mm_utilization"
        margin_efficiency = (net_prem - fee_native) / im_total if im_total > 0 else Decimal("0")
        target_price = self.sell_mid_price(instrument, book)
        short_leg = self._short_put_spread_leg(
            instrument=instrument,
            book=book,
            quantity=quantity,
            entry_price=self.sell_taker_price(instrument, book),
            target_price=target_price,
        )
        pdmin, pdmax = self.config.preferred_call_delta_bounds(currency)
        pomin, pomax = self.config.preferred_call_otm_bounds(currency)
        otm = self._call_otm_ratio(instrument, book)
        preferred_delta = pdmin <= abs(book.delta) <= pdmax
        preferred_otm = pomin <= otm <= pomax
        in_band = self.config.target_net_apr_min <= net_apr <= self.config.target_net_apr_max
        return (
            NakedPutCandidate(
                currency=currency,
                collateral_currency=collateral_currency,
                quantity=quantity,
                dte_days=instrument.dte_days(),
                short_leg=short_leg,
                screening_bid=screening_bid,
                screening_mark=mark,
                target_limit_price=target_price,
                net_premium_native=net_prem - fee_native,
                fee_native=fee_native,
                net_apr=net_apr,
                margin_efficiency=margin_efficiency,
                estimated_im_total=im_total,
                estimated_mm_total=mm_total,
                regime=regime,
                preferred_delta=preferred_delta,
                preferred_otm=preferred_otm,
                in_target_apr_band=in_band,
                option_type="call",
            ),
            None,
        )

    def _build_naked_call_for_quantity(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        existing_im_for_expiry: Decimal,
        relax_apr: bool,
    ) -> NakedPutCandidate | None:
        cand, _reason = self._try_build_naked_call_for_quantity(
            instrument=instrument,
            book=book,
            regime=regime,
            summary_equity=summary_equity,
            summary_maintenance_margin=summary_maintenance_margin,
            collateral_currency=collateral_currency,
            currency=currency,
            quantity=quantity,
            existing_im_for_expiry=existing_im_for_expiry,
            relax_apr=relax_apr,
        )
        return cand

    def _try_build_covered_call_for_quantity(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        regime: RiskRegime,
        collateral_currency: str,
        currency: str,
        quantity: Decimal,
        summary_equity: Decimal,
    ) -> tuple[NakedPutCandidate | None, str | None]:
        if quantity <= 0:
            return None, "quantity<=0"
        if (
            collateral_currency.upper() != currency.upper()
            or instrument.settlement_currency.upper() != currency.upper()
        ):
            return None, "not_native_cover_book"
        screening_bid = book.best_bid_price
        mark = book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
        if screening_bid <= 0 or mark <= 0:
            return None, "bid_or_mark<=0"
        fee_1 = inverse_option_fee_native_per_contract(
            premium=screening_bid,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
        )
        fee_native = fee_1 * quantity
        net_prem = screening_bid * quantity - fee_native
        if net_prem <= 0:
            return None, "net_premium<=0"
        if summary_equity <= 0:
            return None, "summary_equity<=0"
        net_apr = self._screening_net_apr(
            instrument=instrument,
            book=book,
            premium_per_contract=screening_bid,
            collateral_currency=collateral_currency,
            option_type="call",
        )
        if net_apr < self.config.min_net_apr:
            return None, "net_apr_below_min"
        short_leg = self._short_put_spread_leg(
            instrument=instrument,
            book=book,
            quantity=quantity,
            entry_price=self.sell_taker_price(instrument, book),
            target_price=self.sell_mid_price(instrument, book),
        )
        pdmin, pdmax = self.config.preferred_call_delta_bounds(currency)
        pomin, pomax = self.config.preferred_call_otm_bounds(currency)
        otm = self._call_otm_ratio(instrument, book)
        return (
            NakedPutCandidate(
                currency=currency,
                collateral_currency=collateral_currency,
                quantity=quantity,
                dte_days=instrument.dte_days(),
                short_leg=short_leg,
                screening_bid=screening_bid,
                screening_mark=mark,
                target_limit_price=short_leg.target_price,
                net_premium_native=net_prem,
                fee_native=fee_native,
                net_apr=net_apr,
                margin_efficiency=net_apr,
                estimated_im_total=Decimal("0"),
                estimated_mm_total=Decimal("0"),
                regime=regime,
                preferred_delta=pdmin <= abs(book.delta) <= pdmax,
                preferred_otm=pomin <= otm <= pomax,
                in_target_apr_band=self.config.target_net_apr_min <= net_apr <= self.config.target_net_apr_max,
                option_type="call",
                strategy="covered_call",
                covered_underlying_quantity=quantity,
            ),
            None,
        )

    def covered_call_scan_rejection_detail(
        self,
        currency: str,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        collateral_currency: str,
        available_cover_quantity: Decimal,
        summary_equity: Decimal,
        index_price: Decimal | None = None,
    ) -> dict[str, Any]:
        """Aggregate why covered-call candidates are rejected; for scan JSON diagnostics."""
        calls = [
            item
            for item in markets
            if item.option_type == OptionSide.CALL.value
            and self._instrument_active(item)
            and item.instrument_type != "linear"
            and item.strike > 0
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        liquidity_counts: Counter[str] = Counter()
        after_counts: Counter[str] = Counter()
        examples: list[str] = []
        max_examples = 10
        buildable = 0
        buildable_names: list[str] = []

        def maybe_append_example(message: str, inst: OptionInstrument, book: OrderBookSnapshot) -> None:
            if len(examples) >= max_examples or not self._is_otm_call(inst, book):
                return
            examples.append(message)

        ref_index = index_price if index_price is not None else Decimal("0")
        prefilter = (
            (
                lambda inst: self._passes_strike_otm_prefilter(
                    inst, currency=currency, index_price=ref_index, option_type="call"
                )
            )
            if ref_index > 0
            else None
        )
        for inst, book in self._covered_call_scan_pairs(
            calls,
            orderbook_loader,
            index_price=ref_index if ref_index > 0 else None,
            currency=currency,
            prefilter=prefilter,
        ):
            liq = self._naked_short_call_rejection_reason(currency, inst, book)
            if liq is not None:
                liquidity_counts[liq] += 1
                maybe_append_example(f"{inst.instrument_name} [liquidity] {liq}", inst, book)
                continue

            if (
                collateral_currency.upper() != currency.upper()
                or (inst.settlement_currency or "").upper() != currency.upper()
            ):
                after_counts["not_native_cover_book"] += 1
                maybe_append_example(f"{inst.instrument_name} [post] not_native_cover_book", inst, book)
                continue
            if available_cover_quantity <= 0:
                after_counts["available_cover_quantity<=0"] += 1
                maybe_append_example(f"{inst.instrument_name} [post] available_cover_quantity<=0", inst, book)
                continue
            if summary_equity <= 0:
                after_counts["summary_equity<=0"] += 1
                maybe_append_example(f"{inst.instrument_name} [post] summary_equity<=0", inst, book)
                continue

            max_by_cover = floor_to_step(available_cover_quantity, inst.min_trade_amount)
            max_by_liquidity = floor_to_step(book.best_bid_amount, inst.min_trade_amount)
            quantity = min(max_by_cover, max_by_liquidity)
            if quantity < inst.min_trade_amount:
                after_counts["quantity_below_min_trade"] += 1
                caps = (("cover", max_by_cover), ("liq", max_by_liquidity))
                binding = min(caps, key=lambda kv: kv[1])[0]
                maybe_append_example(
                    f"{inst.instrument_name} [post] quantity_below_min_trade "
                    f"bind={binding} q={format_decimal(quantity, 8)} "
                    f"cover={format_decimal(max_by_cover, 8)} "
                    f"liq={format_decimal(max_by_liquidity, 8)} "
                    f"min={format_decimal(inst.min_trade_amount, 8)}",
                    inst,
                    book,
                )
                continue

            cand, breason = self._try_build_covered_call_for_quantity(
                instrument=inst,
                book=book,
                regime=regime,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                summary_equity=summary_equity,
            )
            if cand is not None:
                buildable += 1
                buildable_names.append(inst.instrument_name)
            elif breason is not None:
                after_counts[breason] += 1
                maybe_append_example(f"{inst.instrument_name} [build] {breason}", inst, book)

        distinct_expiries = len({c.expiration_timestamp_ms for c in calls})
        note = (
            "目前是 crisis 風控狀態，實盤不會產生可下單候選；下方統計只用來看 orderbook 資料與篩選門檻卡在哪裡。"
            if regime is RiskRegime.CRISIS
            else None
        )
        return {
            "currency": currency,
            "regime": regime.value,
            "calls_in_dte_window": len(calls),
            "distinct_expiries_in_dte_window": distinct_expiries,
            "available_cover_quantity": str(available_cover_quantity),
            "liquidity_rejections": {k: liquidity_counts[k] for k, _ in liquidity_counts.most_common(16)},
            "after_liquidity_rejections": {k: after_counts[k] for k, _ in after_counts.most_common(16)},
            "instruments_passing_all_build_gates": buildable,
            "instrument_names_passing_all_build_gates": buildable_names,
            "example_messages": examples,
            "note": note,
        }

    def build_covered_call_candidates(
        self,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        collateral_currency: str,
        currency: str,
        available_cover_quantity: Decimal,
        summary_equity: Decimal,
        index_price: Decimal | None = None,
    ) -> list[NakedPutCandidate]:
        if regime is RiskRegime.CRISIS:
            return []
        if collateral_currency.upper() != currency.upper():
            return []
        if available_cover_quantity <= 0 or summary_equity <= 0:
            return []
        calls = [
            item
            for item in markets
            if item.option_type == OptionSide.CALL.value
            and self._instrument_active(item)
            and item.instrument_type != "linear"
            and item.strike > 0
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        candidates: list[NakedPutCandidate] = []
        ref_index = index_price if index_price is not None else Decimal("0")
        for inst in calls:
            if ref_index > 0 and not self._passes_strike_otm_prefilter(
                inst, currency=currency, index_price=ref_index, option_type="call"
            ):
                continue
            book = orderbook_loader(inst.instrument_name)
            if self._naked_short_call_rejection_reason(currency, inst, book) is not None:
                continue
            quantity = min(
                floor_to_step(available_cover_quantity, inst.min_trade_amount),
                floor_to_step(book.best_bid_amount, inst.min_trade_amount),
            )
            if quantity < inst.min_trade_amount:
                continue
            cand, _reason = self._try_build_covered_call_for_quantity(
                instrument=inst,
                book=book,
                regime=regime,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                summary_equity=summary_equity,
            )
            if cand is not None:
                candidates.append(cand)
        return sorted(candidates, key=self.naked_put_sort_key)

    def scan_for_book(
        self,
        book: Book,
        markets_by_currency: dict[str, list[OptionInstrument]],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime_by_currency: dict[str, RiskRegime],
        existing_im_by_expiry_by_currency: dict[str, dict[int, Decimal]],
    ) -> tuple[list[NakedPutCandidate], str]:
        """Scan candidates for a single book.

        Returns ``(candidates, option_type)`` where ``option_type`` is ``"put"``,
        ``"call"``, or ``"both"``. When ``short_call_fallback_only`` is true,
        calls are only scanned after puts produce no candidates. Otherwise puts
        and calls compete in the same sorted candidate list.
        """
        config = self.config

        put_candidates: list[NakedPutCandidate] = []
        if not book.enabled:
            return [], "put"

        if config.enable_short_put:
            for currency in book.underlyings:
                markets = markets_by_currency.get(currency, [])
                if not markets:
                    continue
                regime = regime_by_currency.get(currency, RiskRegime.NORMAL)
                puts = self.build_naked_short_put_candidates(
                    markets,
                    orderbook_loader,
                    regime=regime,
                    summary_equity=book.equity,
                    summary_maintenance_margin=book.maintenance_margin,
                    collateral_currency=book.collateral,
                    currency=currency,
                    existing_im_by_expiry=existing_im_by_expiry_by_currency.get(currency, {}),
                )
                put_candidates.extend(puts)

        if put_candidates and (config.short_call_fallback_only or not config.enable_short_call):
            put_candidates.sort(key=self.naked_put_sort_key)
            return put_candidates, "put"

        call_candidates: list[NakedPutCandidate] = []
        should_scan_calls = config.enable_short_call and (not config.short_call_fallback_only or not put_candidates)
        if should_scan_calls:
            for currency in book.underlyings:
                markets = markets_by_currency.get(currency, [])
                if not markets:
                    continue
                regime = regime_by_currency.get(currency, RiskRegime.NORMAL)
                calls = self.build_naked_short_call_candidates(
                    markets,
                    orderbook_loader,
                    regime=regime,
                    summary_equity=book.equity,
                    summary_maintenance_margin=book.maintenance_margin,
                    collateral_currency=book.collateral,
                    currency=currency,
                    existing_im_by_expiry=existing_im_by_expiry_by_currency.get(currency, {}),
                )
                call_candidates.extend(calls)

        if put_candidates and call_candidates:
            combined = put_candidates + call_candidates
            combined.sort(key=self.naked_put_sort_key)
            return combined, "both"
        if put_candidates:
            put_candidates.sort(key=self.naked_put_sort_key)
            return put_candidates, "put"
        if call_candidates:
            call_candidates.sort(key=self.naked_put_sort_key)
            return call_candidates, "call"
        if config.enable_short_call and not config.enable_short_put:
            return [], "call"
        return [], "put"

    def build_naked_short_put_candidates(
        self,
        markets: Iterable[OptionInstrument],
        orderbook_loader: Callable[[str], OrderBookSnapshot],
        *,
        regime: RiskRegime,
        summary_equity: Decimal,
        summary_maintenance_margin: Decimal,
        collateral_currency: str,
        currency: str,
        existing_im_by_expiry: dict[int, Decimal],
        index_price: Decimal | None = None,
    ) -> list[NakedPutCandidate]:
        if regime is RiskRegime.CRISIS:
            return []
        if summary_equity <= 0:
            return []
        puts = [
            item
            for item in markets
            if item.option_type == OptionSide.PUT.value
            and self._instrument_active(item)
            and item.strike > 0
            and self.config.entry_dte_min <= item.dte_days() <= self.config.entry_dte_max
        ]
        candidates: list[NakedPutCandidate] = []
        ref_index = index_price if index_price is not None else Decimal("0")
        for inst in puts:
            if ref_index > 0 and not self._passes_strike_otm_prefilter(
                inst, currency=currency, index_price=ref_index, option_type="put"
            ):
                continue
            book = orderbook_loader(inst.instrument_name)
            reason = self._naked_short_put_rejection_reason(currency, inst, book)
            if reason is not None:
                continue
            mark = (
                book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
            )
            if mark <= 0 or book.index_price <= 0:
                continue
            usdc_collateral = collateral_currency.upper() == "USDC"
            if usdc_collateral:
                im_1 = linear_usdc_short_put_initial_per_contract_usdc(
                    index_price=book.index_price,
                    strike=inst.strike,
                    mark_usdc=mark,
                    contract_size=inst.contract_size,
                )
                mm_1 = linear_usdc_short_put_mm_per_contract_usdc(
                    index_price=book.index_price,
                    strike=inst.strike,
                    mark_usdc=mark,
                    contract_size=inst.contract_size,
                )
            else:
                im_1 = short_put_initial_unit(index_price=book.index_price, strike=inst.strike, mark_price=mark)
                mm_1 = short_put_maintenance_unit(index_price=book.index_price, strike=inst.strike, mark_price=mark)
            if im_1 <= 0:
                continue
            per_leg_cap = self.config.per_leg_im_cap(currency)
            exp_cap = self.config.expiry_im_cap(currency)
            hard_mm = self.config.hard_mm_utilization(currency)
            max_by_im_leg = floor_to_step((summary_equity * per_leg_cap) / im_1, inst.min_trade_amount)
            exp_key = inst.expiration_timestamp_ms
            existing_im = existing_im_by_expiry.get(exp_key, Decimal("0"))
            max_by_expiry = floor_to_step((summary_equity * exp_cap - existing_im) / im_1, inst.min_trade_amount)
            max_by_mm = floor_to_step(
                (summary_equity * hard_mm - summary_maintenance_margin) / mm_1, inst.min_trade_amount
            )
            max_by_liquidity = floor_to_step(book.best_bid_amount, inst.min_trade_amount)
            quantity = min(max_by_im_leg, max_by_expiry, max_by_mm, max_by_liquidity)
            if quantity < inst.min_trade_amount:
                continue
            cand = self._build_naked_put_for_quantity(
                instrument=inst,
                book=book,
                regime=regime,
                summary_equity=summary_equity,
                summary_maintenance_margin=summary_maintenance_margin,
                collateral_currency=collateral_currency,
                currency=currency,
                quantity=quantity,
                existing_im_for_expiry=existing_im,
                relax_apr=False,
            )
            if cand is not None:
                candidates.append(cand)
        return sorted(candidates, key=self.naked_put_sort_key)
