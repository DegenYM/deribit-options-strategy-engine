"""Risk-regime determination (macro feeds + option-book liquidity) used by
:mod:`management`.

Split out of ``engine/management.py`` (Workstream D, roadmap-2026H2). Pure
move, no behavior change.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import OptionInstrument, OrderBookSnapshot, RiskRegime
from ..utils import format_decimal, utc_now_ms


class RegimeMixin:
    def _determine_regime_for_dashboard(self, currency: str) -> tuple[RiskRegime, list[str]]:
        """Dashboard-only regime: macro feeds + cache, no option-book liquidity scan."""
        drawdown = self._index_drawdown_24h(currency)
        dvol_ratio = self._dvol_ratio(currency)
        return self._regime_from_macro_feeds(
            currency,
            drawdown=drawdown,
            dvol_ratio=dvol_ratio,
            unavailable_default=RiskRegime.NORMAL,
            unavailable_note="dashboard:data_unavailable",
        )

    def _regime_from_macro_feeds(
        self,
        currency: str,
        *,
        drawdown: Decimal | None,
        dvol_ratio: Decimal | None,
        unavailable_default: RiskRegime,
        unavailable_note: str,
    ) -> tuple[RiskRegime, list[str]]:
        if drawdown is None or dvol_ratio is None:
            missing = []
            if drawdown is None:
                missing.append("index_chart_data")
            if dvol_ratio is None:
                missing.append("volatility_index_data")
            cached = self._last_regime_cache.get(currency)
            if cached is not None:
                cached_regime, _ = cached
                return cached_regime, [
                    f"{unavailable_note}({','.join(missing)}); using cached regime={cached_regime.value}",
                ]
            return unavailable_default, [
                f"{unavailable_note}({','.join(missing)}); defaulting to {unavailable_default.value}",
            ]

        if drawdown <= -self.config.index_drawdown_crisis_pct:
            regime = RiskRegime.CRISIS
            detail = [
                f"index_24h_drawdown <= -index_drawdown_crisis_pct "
                f"({format_decimal(drawdown, 8)} <= -{format_decimal(self.config.index_drawdown_crisis_pct, 6)})",
            ]
        elif dvol_ratio > self.config.dvol_crisis_multiplier:
            regime = RiskRegime.CRISIS
            detail = [
                f"dvol_ratio > dvol_crisis_multiplier "
                f"({format_decimal(dvol_ratio, 6)} > {format_decimal(self.config.dvol_crisis_multiplier, 6)})",
            ]
        elif drawdown <= -self.config.index_drawdown_elevated_pct or dvol_ratio > self.config.dvol_elevated_multiplier:
            regime = RiskRegime.ELEVATED
            detail = [
                f"elevated: drawdown={format_decimal(drawdown, 8)} "
                f"dvol_ratio={format_decimal(dvol_ratio, 6)} "
                f"(thresholds -elevated {format_decimal(self.config.index_drawdown_elevated_pct, 6)} / {format_decimal(self.config.dvol_elevated_multiplier, 6)})",
            ]
        else:
            regime = RiskRegime.NORMAL
            detail = ["market_conditions_normal"]

        self._last_regime_cache[currency] = (regime, utc_now_ms())
        return regime, detail

    def _determine_regime_with_detail(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> tuple[RiskRegime, list[str]]:
        if not markets:
            return RiskRegime.CRISIS, ["no_option_markets_loaded_for_currency"]
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        ok, liq_notes = self.strategy.core_regime_liquidity_detail(currency, markets, loader)
        if not ok:
            return RiskRegime.CRISIS, ["core_entry_liquidity_check_failed", *liq_notes]

        drawdown = self._index_drawdown_24h(currency)
        dvol_ratio = self._dvol_ratio(currency)
        return self._regime_from_macro_feeds(
            currency,
            drawdown=drawdown,
            dvol_ratio=dvol_ratio,
            unavailable_default=RiskRegime.ELEVATED,
            unavailable_note="data_unavailable",
        )

    def _determine_regime(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> RiskRegime:
        regime, _ = self._determine_regime_with_detail(currency, markets=markets, orderbook_cache=orderbook_cache)
        return regime
