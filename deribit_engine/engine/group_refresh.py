"""Per-cycle group mark/delta refresh and delta-total aggregation used by
:mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import AccountSummary, OptionInstrument, OrderBookSnapshot, Position, StrategyState, TradeGroup
from ..utils import safe_div
from .context import LOGGER


class GroupRefreshMixin:
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
