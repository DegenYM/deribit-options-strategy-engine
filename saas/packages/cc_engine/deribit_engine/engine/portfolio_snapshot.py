"""Portfolio-wide snapshot construction used by :mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change. Home of the ~317-line ``_build_portfolio_snapshot``
flagged in ``docs/optimization-plan-zh-TW.md`` Phase 2 notes.
"""

from __future__ import annotations

from decimal import Decimal

from ..cross_book_flow import cross_book_flow_adjustments_native
from ..entry_gates import (
    append_underlying_regime_halt_reasons_for_usdc_book,
    build_halt_new_entries_by_currency,
)
from ..models import (
    AccountSummary,
    OptionInstrument,
    OrderBookSnapshot,
    PortfolioSnapshot,
    Position,
    RiskRegime,
    StrategyState,
)
from ..utils import format_decimal, safe_div, utc_now_ms


class PortfolioSnapshotMixin:
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
