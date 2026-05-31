from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from ..models import (
    NakedPutCandidate,
    OptionInstrument,
    RiskRegime,
)
from ..utils import (
    format_decimal,
    utc_now_ms,
)
from .context import (
    _MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES,
    LOGGER,
    RuntimeContext,
)


class ScannerMixin:
    def scan(
        self,
        *,
        currencies: tuple[str, ...] | None = None,
        top_n: int | None = None,
        include_scan_diagnostics: bool | None = None,
    ) -> dict[str, Any]:
        context = self._load_runtime()
        candidates = self._scan_candidates(context, currencies=currencies, top_n=top_n)
        self.state_store.save(context.state)
        if include_scan_diagnostics is None:
            include_scan_diagnostics = not candidates
        return self._scan_payload(
            context,
            candidates,
            scan_currencies=currencies,
            include_scan_diagnostics=include_scan_diagnostics,
        )

    def _scan_payload(
        self,
        context: RuntimeContext,
        candidates: list[NakedPutCandidate],
        *,
        scan_currencies: tuple[str, ...] | None = None,
        include_scan_diagnostics: bool = True,
    ) -> dict[str, Any]:
        selected = scan_currencies or self.config.managed_currencies
        option_counts = Counter((c.option_type or "put") for c in candidates)
        is_covered_call = self.config.option_strategy == "covered_call"
        note_zh = None
        if not is_covered_call and self.config.enable_short_call and self.config.short_call_fallback_only:
            note_zh = (
                "short_call_fallback_only=true：本輪只要有 put 通過 min_net_apr，就不會掃描 short call；"
                "要看 call 請設 SHORT_OPTION_SIDE=both 或 SHORT_CALL_FALLBACK_ONLY=false。"
            )
        return {
            "env": self.config.env,
            "candidate_count": len(candidates),
            "regime": context.snapshot.regime.value,
            "portfolio": context.snapshot.to_dict(),
            "candidates": [c.to_dict() for c in candidates],
            "strategy_mode": self.config.option_strategy,
            "candidate_option_type_counts": dict(option_counts),
            "scan_policy": {
                "enable_short_put": self.config.enable_short_put,
                "enable_short_call": self.config.enable_short_call,
                "short_call_fallback_only": self.config.short_call_fallback_only,
                "put_and_call_compete_in_scan": self.config.naked_scan_put_and_call_compete,
                "note_zh": note_zh,
            },
            "entry_blockers": self._scan_entry_blockers(
                context,
                candidates,
                selected_currencies=selected,
                include_scan_diagnostics=include_scan_diagnostics,
            ),
            "scan_rejections": (
                self._covered_call_scan_rejections_payload(context, tuple(selected))
                if is_covered_call
                else self._naked_scan_rejections_payload(context, tuple(selected))
            )
            if include_scan_diagnostics
            else {},
            "scan_rejections_short_call": (
                None if is_covered_call else self._naked_scan_rejections_short_call_payload(context, tuple(selected))
            )
            if include_scan_diagnostics
            else None,
        }

    def _naked_scan_rejections_payload(
        self,
        context: RuntimeContext,
        selected_currencies: tuple[str, ...],
    ) -> dict[str, Any]:
        loader = lambda instrument_name: self._get_orderbook(instrument_name, context.orderbook_cache)
        out: dict[str, Any] = {}
        for currency in selected_currencies:
            markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(markets_by_collateral.items()):
                collateral_summary = context.summaries.get(collateral_ccy)
                equity = collateral_summary.equity if collateral_summary is not None else Decimal("0")
                mm = collateral_summary.maintenance_margin if collateral_summary is not None else Decimal("0")
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
                key = f"{currency}/{collateral_ccy}" if collateral_ccy != currency else currency
                index_price = self._currency_index_price(currency, context.orderbook_cache)
                out[key] = self.strategy.naked_put_scan_rejection_detail(
                    currency,
                    collateral_markets,
                    loader,
                    regime=regime,
                    summary_equity=equity,
                    summary_maintenance_margin=mm,
                    collateral_currency=collateral_ccy,
                    existing_im_by_expiry=im_by_exp,
                    index_price=index_price,
                )
        return out

    def _naked_scan_rejections_short_call_payload(
        self,
        context: RuntimeContext,
        selected_currencies: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Per-book short-call rejection stats (mirrors ``scan_rejections`` for puts)."""
        if not self.config.enable_short_call:
            return None
        loader = lambda instrument_name: self._get_orderbook(instrument_name, context.orderbook_cache)
        out: dict[str, Any] = {}
        for currency in selected_currencies:
            markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(markets_by_collateral.items()):
                collateral_summary = context.summaries.get(collateral_ccy)
                equity = collateral_summary.equity if collateral_summary is not None else Decimal("0")
                mm = collateral_summary.maintenance_margin if collateral_summary is not None else Decimal("0")
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
                key = f"{currency}/{collateral_ccy}" if collateral_ccy != currency else currency
                index_price = self._currency_index_price(currency, context.orderbook_cache)
                out[key] = self.strategy.naked_call_scan_rejection_detail(
                    currency,
                    collateral_markets,
                    loader,
                    regime=regime,
                    summary_equity=equity,
                    summary_maintenance_margin=mm,
                    collateral_currency=collateral_ccy,
                    existing_im_by_expiry=im_by_exp,
                    index_price=index_price,
                )
        return out

    def _covered_call_scan_rejections_payload(
        self,
        context: RuntimeContext,
        selected_currencies: tuple[str, ...],
    ) -> dict[str, Any]:
        loader = lambda instrument_name: self._get_orderbook(instrument_name, context.orderbook_cache)
        out: dict[str, Any] = {}
        for currency in selected_currencies:
            ccy = currency.upper()
            summary = context.summaries.get(ccy)
            equity = summary.equity if summary is not None else Decimal("0")
            regime = context.regime_by_currency.get(ccy, RiskRegime.CRISIS)
            available_cover = self._available_covered_call_quantity(context, ccy)
            open_group_count = self._covered_call_open_group_count(context.state, ccy)
            collateral_markets = [
                market
                for market in context.markets_by_currency.get(ccy, [])
                if (market.settlement_currency or ccy).upper() == ccy
                and market.option_type == "call"
                and market.instrument_type != "linear"
            ]
            out[ccy] = self.strategy.covered_call_scan_rejection_detail(
                ccy,
                collateral_markets,
                loader,
                regime=regime,
                collateral_currency=ccy,
                available_cover_quantity=available_cover,
                summary_equity=equity,
                index_price=self._currency_index_price(ccy, context.orderbook_cache),
                open_group_count=open_group_count,
            )
        return out

    def _scan_entry_blockers(
        self,
        context: RuntimeContext,
        candidates: list[NakedPutCandidate],
        *,
        selected_currencies: tuple[str, ...],
        include_scan_diagnostics: bool = True,
    ) -> list[str]:
        blockers: list[str] = []
        snap = context.snapshot
        if snap.halt_new_entries:
            blockers.extend(list(snap.halt_entry_reasons))
            return blockers
        active_strategies = self._active_scan_strategy_keys()
        if (
            self.config.max_concurrent_groups > 0
            and active_strategies
            and all(
                self._open_group_count_for_strategy(context.state, strategy) >= self.config.max_concurrent_groups
                for strategy in active_strategies
            )
        ):
            detail = ", ".join(
                f"{strategy} open_count={self._open_group_count_for_strategy(context.state, strategy)}"
                for strategy in active_strategies
            )
            blockers.append(
                f"max_concurrent_groups: all enabled strategies at limit "
                f"({detail}; limit={self.config.max_concurrent_groups})"
            )
            return blockers
        if candidates:
            cooled_books = [
                book
                for book in sorted({(c.collateral_currency or c.currency or "").upper() for c in candidates})
                if book and self._book_entry_cooldown_active(context.state, book)
            ]
            if cooled_books:
                blockers.append(
                    f"entry_cooldown_active: {', '.join(cooled_books)} ({self.config.entry_cooldown_minutes}m)"
                )
                return blockers
            return []
        if not include_scan_diagnostics:
            blockers: list[str] = []
            snap = context.snapshot
            if snap.halt_new_entries:
                return list(snap.halt_entry_reasons)
            for currency in selected_currencies:
                regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
                if regime is RiskRegime.CRISIS:
                    detail = snap.regime_detail_by_currency.get(currency, ())
                    blockers.append(f"{currency}: regime=crisis — {'; '.join(detail)}")
            if not blockers:
                blockers.append("no_candidates: run `./bot scan --diagnostics` for rejection detail")
            return blockers
        if self.config.option_strategy == "covered_call":
            return self._covered_call_scan_entry_blockers(
                context,
                selected_currencies=selected_currencies,
            )
        threshold = self.config.min_net_apr
        orderbook_cache = context.orderbook_cache
        for currency in selected_currencies:
            if self.config.option_strategy == "bull_put_spread":
                open_for_currency = self._open_group_count_for_currency(
                    context.state,
                    currency,
                    strategy="bull_put_spread",
                )
                if self.config.max_groups_per_currency > 0 and open_for_currency >= self.config.max_groups_per_currency:
                    blockers.append(
                        f"{currency} [bull_put_spread]: max_groups_per_currency "
                        f"(open_for_strategy_currency={open_for_currency} >= {self.config.max_groups_per_currency})"
                    )
                    continue
            regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                detail = snap.regime_detail_by_currency.get(currency, ())
                blockers.append(f"{currency}: regime=crisis — {'; '.join(detail)}")
                continue
            loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
            naked_markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                naked_markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(naked_markets_by_collateral.items()):
                collateral_summary = context.summaries.get(collateral_ccy)
                if collateral_summary is None or collateral_summary.equity <= 0:
                    blockers.append(f"{currency}/{collateral_ccy}: book equity<=0 or missing account summary")
                    continue
                ccy_ratios = snap.margin_ratios_by_currency.get(collateral_ccy)
                if ccy_ratios:
                    ccy_im, ccy_mm = ccy_ratios
                    if ccy_im >= self.config.book_im_target:
                        blockers.append(
                            f"{currency}/{collateral_ccy}: book im_ratio >= book_im_target "
                            f"({format_decimal(ccy_im, 8)} >= {format_decimal(self.config.book_im_target, 6)})"
                        )
                        continue
                    if ccy_mm >= self.config.book_mm_target:
                        blockers.append(
                            f"{currency}/{collateral_ccy}: book mm_ratio >= book_mm_target "
                            f"({format_decimal(ccy_mm, 8)} >= {format_decimal(self.config.book_mm_target, 6)})"
                        )
                        continue
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                builder_map: list[tuple[str, Callable[..., list[NakedPutCandidate]]]] = []
                if self.config.enable_short_put:
                    builder_map.append(("put", self.strategy.build_naked_short_put_candidates))
                if self.config.enable_short_call:
                    builder_map.append(("call", self.strategy.build_naked_short_call_candidates))
                if not builder_map:
                    blockers.append(
                        f"{currency}/{collateral_ccy}: no option sides enabled (enable_short_put/enable_short_call both disabled)"
                    )
                    continue
                for side, builder in builder_map:
                    strategy_key = "naked_short"
                    open_for_strategy = self._open_group_count_for_strategy(context.state, strategy_key)
                    if self.config.max_concurrent_groups > 0 and open_for_strategy >= self.config.max_concurrent_groups:
                        blockers.append(
                            f"{currency}/{collateral_ccy} [{strategy_key}]: max_concurrent_groups "
                            f"(open_for_strategy={open_for_strategy} >= {self.config.max_concurrent_groups})"
                        )
                        continue
                    open_for_strategy_currency = self._open_group_count_for_currency(
                        context.state,
                        currency,
                        strategy=strategy_key,
                    )
                    if (
                        self.config.max_groups_per_currency > 0
                        and open_for_strategy_currency >= self.config.max_groups_per_currency
                    ):
                        blockers.append(
                            f"{currency}/{collateral_ccy} [{strategy_key}]: max_groups_per_currency "
                            f"(open_for_strategy_currency={open_for_strategy_currency} >= "
                            f"{self.config.max_groups_per_currency})"
                        )
                        continue
                    raw_naked = builder(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                    )
                    if not raw_naked:
                        if side == "put":
                            detail = self.strategy.naked_put_scan_rejection_detail(
                                currency,
                                collateral_markets,
                                loader,
                                regime=regime,
                                summary_equity=collateral_summary.equity,
                                summary_maintenance_margin=collateral_summary.maintenance_margin,
                                collateral_currency=collateral_ccy,
                                existing_im_by_expiry=im_by_exp,
                            )
                            liq = detail.get("liquidity_rejections") or {}
                            post = detail.get("after_liquidity_rejections") or {}
                            prefix = f"{currency}/{collateral_ccy} [{side}]"
                            liq_line = self._format_scan_rejection_counts_inline("liquidity_rej", liq)
                            post_line = self._format_scan_rejection_counts_inline("post_liquidity_rej", post)
                            if liq_line:
                                blockers.append(f"{prefix}: {liq_line}")
                            if post_line:
                                blockers.append(f"{prefix}: {post_line}")
                            post_ex = self._post_only_scan_example_messages(detail.get("example_messages"))
                            for ex in post_ex[:_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES]:
                                blockers.append(f"{prefix}: {ex}")
                            if not liq_line and not post_line:
                                blockers.append(f"{prefix}: puts_in_dte_window={detail.get('puts_in_dte_window', 0)}")
                        else:
                            detail = self.strategy.naked_call_scan_rejection_detail(
                                currency,
                                collateral_markets,
                                loader,
                                regime=regime,
                                summary_equity=collateral_summary.equity,
                                summary_maintenance_margin=collateral_summary.maintenance_margin,
                                collateral_currency=collateral_ccy,
                                existing_im_by_expiry=im_by_exp,
                            )
                            liq = detail.get("liquidity_rejections") or {}
                            post = detail.get("after_liquidity_rejections") or {}
                            prefix = f"{currency}/{collateral_ccy} [{side}]"
                            liq_line = self._format_scan_rejection_counts_inline("liquidity_rej", liq)
                            post_line = self._format_scan_rejection_counts_inline("post_liquidity_rej", post)
                            if liq_line:
                                blockers.append(f"{prefix}: {liq_line}")
                            if post_line:
                                blockers.append(f"{prefix}: {post_line}")
                            post_ex = self._post_only_scan_example_messages(detail.get("example_messages"))
                            for ex in post_ex[:_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES]:
                                blockers.append(f"{prefix}: {ex}")
                            if not liq_line and not post_line:
                                blockers.append(f"{prefix}: calls_in_dte_window={detail.get('calls_in_dte_window', 0)}")
                        continue
                    deduped_naked = [
                        c for c in raw_naked if not self._naked_candidate_matches_open_group(context.state, c)
                    ]
                    if not deduped_naked:
                        blockers.append(f"{currency}/{collateral_ccy} [{side}]: all naked candidates already open")
                        continue
                    best_net = max(c.net_apr for c in deduped_naked)
                    below_naked = [c for c in deduped_naked if c.net_apr < threshold]
                    if len(below_naked) == len(deduped_naked):
                        blockers.append(
                            f"{currency}/{collateral_ccy} [{side}]: {len(deduped_naked)} naked candidate(s) all below min_net_apr "
                            f"({format_decimal(threshold, 4)}); best net_apr={format_decimal(best_net, 8)}"
                        )
        if not blockers:
            blockers.append("no_candidates: empty selection or all currencies skipped before diagnostics")
        return blockers

    def _covered_call_scan_entry_blockers(
        self,
        context: RuntimeContext,
        *,
        selected_currencies: tuple[str, ...],
    ) -> list[str]:
        blockers: list[str] = []
        snap = context.snapshot
        threshold = self.config.min_net_apr
        orderbook_cache = context.orderbook_cache
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        for currency in selected_currencies:
            ccy = currency.upper()
            open_for_strategy = self._open_group_count_for_strategy(context.state, "covered_call")
            if self.config.max_concurrent_groups > 0 and open_for_strategy >= self.config.max_concurrent_groups:
                blockers.append(
                    f"{ccy} [covered_call]: max_concurrent_groups "
                    f"(open_for_strategy={open_for_strategy} >= {self.config.max_concurrent_groups})"
                )
                continue
            open_for_strategy_currency = self._open_group_count_for_currency(
                context.state,
                ccy,
                strategy="covered_call",
            )
            if (
                self.config.max_groups_per_currency > 0
                and open_for_strategy_currency >= self.config.max_groups_per_currency
            ):
                blockers.append(
                    f"{ccy} [covered_call]: max_groups_per_currency "
                    f"(open_for_strategy_currency={open_for_strategy_currency} >= "
                    f"{self.config.max_groups_per_currency})"
                )
                continue
            regime = context.regime_by_currency.get(ccy, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                detail = snap.regime_detail_by_currency.get(ccy, ())
                blockers.append(f"{ccy}: regime=crisis — {'; '.join(detail)}")
                continue
            summary = context.summaries.get(ccy)
            if summary is None or summary.equity <= 0:
                blockers.append(f"{ccy}/{ccy} [covered_call]: book equity<=0 or missing account summary")
                continue
            available_cover = self._available_covered_call_quantity(context, ccy)
            open_group_count = self._covered_call_open_group_count(context.state, ccy)
            if available_cover <= 0:
                blockers.append(
                    f"{ccy}/{ccy} [covered_call]: no available {ccy} cover after existing covered_call reservations"
                )
                continue
            if not self._covered_call_book_im_mm_shielded(
                context.state,
                context.summaries,
                ccy,
                available_cover=available_cover,
            ):
                ccy_ratios = snap.margin_ratios_by_currency.get(ccy)
                if ccy_ratios:
                    ccy_im, ccy_mm = ccy_ratios
                    if ccy_im >= self.config.book_im_target:
                        blockers.append(
                            f"{ccy}/{ccy} [covered_call]: book im_ratio >= book_im_target "
                            f"({format_decimal(ccy_im, 8)} >= {format_decimal(self.config.book_im_target, 6)})"
                        )
                        continue
                    if ccy_mm >= self.config.book_mm_target:
                        blockers.append(
                            f"{ccy}/{ccy} [covered_call]: book mm_ratio >= book_mm_target "
                            f"({format_decimal(ccy_mm, 8)} >= {format_decimal(self.config.book_mm_target, 6)})"
                        )
                        continue
            collateral_markets = [
                market
                for market in context.markets_by_currency.get(ccy, [])
                if (market.settlement_currency or ccy).upper() == ccy
                and market.option_type == "call"
                and market.instrument_type != "linear"
                and self.config.entry_dte_min <= market.dte_days() <= self.config.entry_dte_max
            ]
            if not collateral_markets:
                blockers.append(f"{ccy}/{ccy} [covered_call]: no inverse call markets in entry DTE window")
                continue
            raw_candidates = self.strategy.build_covered_call_candidates(
                collateral_markets,
                loader,
                regime=regime,
                collateral_currency=ccy,
                currency=ccy,
                available_cover_quantity=available_cover,
                summary_equity=summary.equity,
                open_group_count=open_group_count,
            )
            if not raw_candidates:
                detail = self.strategy.covered_call_scan_rejection_detail(
                    ccy,
                    collateral_markets,
                    loader,
                    regime=regime,
                    collateral_currency=ccy,
                    available_cover_quantity=available_cover,
                    summary_equity=summary.equity,
                    open_group_count=open_group_count,
                )
                prefix = f"{ccy}/{ccy} [covered_call]"
                liq = detail.get("liquidity_rejections") or {}
                post = detail.get("after_liquidity_rejections") or {}
                liq_line = self._format_scan_rejection_counts_inline("liquidity_rej", liq)
                post_line = self._format_scan_rejection_counts_inline("post_liquidity_rej", post)
                if liq_line:
                    blockers.append(f"{prefix}: {liq_line}")
                if post_line:
                    blockers.append(f"{prefix}: {post_line}")
                post_ex = self._post_only_scan_example_messages(detail.get("example_messages"))
                for ex in post_ex[:_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES]:
                    blockers.append(f"{prefix}: {ex}")
                if not liq_line and not post_line:
                    blockers.append(f"{prefix}: calls_in_dte_window={detail.get('calls_in_dte_window', 0)}")
                continue
            deduped = [
                candidate
                for candidate in raw_candidates
                if not self._naked_candidate_matches_open_group(context.state, candidate)
            ]
            if not deduped:
                blockers.append(f"{ccy}/{ccy} [covered_call]: all covered_call candidates already open")
                continue
            best_net = max(candidate.net_apr for candidate in deduped)
            below_threshold = [candidate for candidate in deduped if candidate.net_apr < threshold]
            if len(below_threshold) == len(deduped):
                blockers.append(
                    f"{ccy}/{ccy} [covered_call]: {len(deduped)} candidate(s) all below min_net_apr "
                    f"({format_decimal(threshold, 4)}); best net_apr={format_decimal(best_net, 8)}"
                )
        if not blockers:
            blockers.append("no_candidates: empty selection or all currencies skipped before covered_call diagnostics")
        return blockers

    @staticmethod
    def _post_only_scan_example_messages(messages: list[str] | None) -> list[str]:
        """Strip scan `example_messages` to post-liquidity phase only (excludes [liquidity] / [build])."""
        if not messages:
            return []
        return [ex for ex in messages if " [post] " in ex]

    @staticmethod
    def _format_scan_rejection_counts_inline(title: str, counts: dict[str, Any]) -> str | None:
        """Single segment like `liquidity_rej: a=1, b=2` (sorted by count desc); None if empty."""
        if not counts:
            return None
        pairs: list[tuple[str, int]] = []
        for key, raw in counts.items():
            try:
                pairs.append((str(key), int(raw)))
            except (TypeError, ValueError):
                pairs.append((str(key), 0))
        pairs.sort(key=lambda kv: (-kv[1], kv[0]))
        max_show = 10
        head = pairs[:max_show]
        body = ", ".join(f"{k}={v}" for k, v in head)
        if len(pairs) > max_show:
            body += f", ...+{len(pairs) - max_show} more_reasons"
        return f"{title}: {body}"

    def _log_cycle_candidates(self, cycle_no: int, candidates: list[dict[str, Any]]) -> None:
        LOGGER.info("run cycle=%s candidate_count=%s", cycle_no, len(candidates))
        for rank, candidate in enumerate(candidates[:3], start=1):
            currency = candidate["currency"]
            short_instrument_name = candidate["short_instrument_name"]
            quantity = candidate["quantity"]
            max_profit_apr = candidate.get("max_profit_apr") or candidate.get("net_apr") or Decimal("0")
            net_credit = candidate.get("net_credit") or Decimal("0")
            max_loss = candidate.get("max_loss") or Decimal("0")
            LOGGER.info(
                "run cycle=%s candidate_rank=%s currency=%s short=%s qty=%s apr=%s net_credit=%s max_loss=%s",
                cycle_no,
                rank,
                currency,
                short_instrument_name,
                format_decimal(quantity, 8),
                format_decimal(max_profit_apr, 8),
                format_decimal(net_credit, 8),
                format_decimal(max_loss, 8),
            )

    def _scan_candidates(
        self,
        context: RuntimeContext,
        *,
        currencies: tuple[str, ...] | None,
        top_n: int | None,
    ) -> list[NakedPutCandidate]:
        snapshot = context.snapshot
        # Portfolio-wide kill switches (data_unavailable, open_max_loss_pct, or
        # every enabled book halted) still short-circuit the scan. Per-book
        # halts are handled inside the per-currency loop so one halted book
        # can't silently sink the others.
        global_blockers = (
            bool(snapshot.cooldown_until_ms and snapshot.cooldown_until_ms > utc_now_ms())
            or snapshot.open_max_loss_pct >= self.config.halt_open_max_loss_pct
            or any(
                any(note.startswith("data_unavailable") for note in notes)
                for notes in snapshot.regime_detail_by_currency.values()
            )
        )
        if global_blockers:
            return []
        if snapshot.halt_entries_by_book and all(snapshot.halt_entries_by_book.values()):
            return []
        selected = currencies or self.config.managed_currencies
        active_strategies = self._active_scan_strategy_keys()
        if (
            self.config.max_concurrent_groups > 0
            and active_strategies
            and all(
                self._open_group_count_for_strategy(context.state, strategy) >= self.config.max_concurrent_groups
                for strategy in active_strategies
            )
        ):
            return []

        limit = top_n or self.config.top_n
        orderbook_cache = context.orderbook_cache
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)

        candidates_n: list[NakedPutCandidate] = []
        threshold = self.config.min_net_apr
        for currency in selected:
            if self.config.option_strategy == "bull_put_spread":
                if self._strategy_at_currency_limit(context.state, "bull_put_spread", currency):
                    continue
            elif self.config.option_strategy == "covered_call":
                if self._strategy_at_currency_limit(context.state, "covered_call", currency):
                    continue
            regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                continue
            index_price = self._currency_index_price(currency, orderbook_cache)
            markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(markets_by_collateral.items()):
                # Skip candidates routed to a book that is currently halted
                # (drawdown, cooldown, or hard IM/MM breach) while still
                # evaluating other books for the same underlying.
                if snapshot.halt_entries_by_book.get(collateral_ccy):
                    continue
                if self._book_entry_cooldown_active(context.state, collateral_ccy):
                    continue
                if self._strategy_at_book_limit(context.state, collateral_ccy):
                    continue
                collateral_summary = context.summaries.get(collateral_ccy)
                if collateral_summary is None or collateral_summary.equity <= 0:
                    continue
                skip_book_im_mm = False
                if self.config.option_strategy == "covered_call":
                    available_cover = self._available_covered_call_quantity(context, currency)
                    skip_book_im_mm = self._covered_call_book_im_mm_shielded(
                        context.state,
                        context.summaries,
                        collateral_ccy,
                        available_cover=available_cover,
                    )
                ccy_ratios = context.snapshot.margin_ratios_by_currency.get(collateral_ccy)
                if ccy_ratios and not skip_book_im_mm:
                    ccy_im, ccy_mm = ccy_ratios
                    if ccy_im >= self.config.book_im_target or ccy_mm >= self.config.book_mm_target:
                        continue
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                if self.config.option_strategy == "bull_put_spread":
                    if self._strategy_at_concurrent_limit(context.state, "bull_put_spread"):
                        continue
                    for candidate in self.strategy.build_bull_put_spread_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        candidates_n.append(candidate)
                    continue
                if self.config.option_strategy == "covered_call":
                    if self._strategy_at_concurrent_limit(context.state, "covered_call"):
                        continue
                    available_cover = self._available_covered_call_quantity(context, currency)
                    open_group_count = self._covered_call_open_group_count(context.state, currency)
                    for candidate in self.strategy.build_covered_call_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        available_cover_quantity=available_cover,
                        summary_equity=collateral_summary.equity,
                        index_price=index_price,
                        open_group_count=open_group_count,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        candidates_n.append(candidate)
                    continue
                put_candidates: list[NakedPutCandidate] = []
                if (
                    self.config.enable_short_put
                    and not self._strategy_at_concurrent_limit(context.state, "naked_short")
                    and not self._strategy_at_currency_limit(context.state, "naked_short", currency)
                ):
                    for candidate in self.strategy.build_naked_short_put_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                        index_price=index_price,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        put_candidates.append(candidate)
                candidates_n.extend(put_candidates)

                scan_calls = self.config.enable_short_call and (
                    not self.config.short_call_fallback_only or not put_candidates
                )
                if (
                    scan_calls
                    and not self._strategy_at_concurrent_limit(context.state, "naked_short")
                    and not self._strategy_at_currency_limit(context.state, "naked_short", currency)
                ):
                    for candidate in self.strategy.build_naked_short_call_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                        index_price=index_price,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        candidates_n.append(candidate)
        return self.strategy.take_top_scan_candidates(
            candidates_n,
            limit=limit,
        )
