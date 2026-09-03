"""External cash-flow tracking and daily-state reset used by :mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change.
"""

from __future__ import annotations

from decimal import Decimal

from ..investor_cash_flow import cash_flow_scan_currencies, sum_external_flow_native_in_window
from ..models import AccountSummary, OrderBookSnapshot, StrategyState
from ..utils import safe_div, utc_now, utc_now_ms
from .context import LOGGER


class CashFlowMixin:
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
