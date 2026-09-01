"""Risk-regime determination (macro feeds + option-book liquidity) used by
:mod:`management`.

Naked short put additionally elevates after consecutive down days so the
scanner does not sell more puts into a grind.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import OptionInstrument, OrderBookSnapshot, RiskRegime
from ..utils import format_decimal, utc_now_ms
from ..vol_metrics import (
    consecutive_down_day_count,
    daily_closes_from_index_series,
    index_chart_close_series,
)


class RegimeMixin:
    def _determine_regime_for_dashboard(self, currency: str) -> tuple[RiskRegime, list[str]]:
        """Dashboard-only regime: macro feeds + cache, no option-book liquidity scan."""
        drawdown = self._index_drawdown_24h(currency)
        dvol_ratio = self._dvol_ratio(currency)
        regime, detail = self._regime_from_macro_feeds(
            currency,
            drawdown=drawdown,
            dvol_ratio=dvol_ratio,
            unavailable_default=RiskRegime.NORMAL,
            unavailable_note="dashboard:data_unavailable",
        )
        return self._apply_naked_down_streak_regime(currency, regime, detail)

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

    def _index_daily_closes(self, currency: str) -> list[Decimal] | None:
        """UTC daily closes from the 1y index chart, or None if unavailable."""
        for index_name in (f"{currency.lower()}_usdc", f"{currency.lower()}_usd"):
            try:
                points = self.client.get_index_chart_data(index_name, range_name="1y")
            except Exception:
                continue
            daily = daily_closes_from_index_series(index_chart_close_series(points or []))
            if daily:
                return daily
        return None

    def _naked_consecutive_down_elevated(self, currency: str) -> tuple[bool, str | None]:
        """Elevate naked-put regime after consecutive down days so we skip new shorts."""
        if not self.config.naked_put_blocks_on_down_streak:
            return False, None
        daily = self._index_daily_closes(currency)
        if daily is None or len(daily) < 2:
            return False, None
        needed = self.config.naked_entry_down_streak_days
        streak = consecutive_down_day_count(daily, min_day_pct=self.config.naked_entry_down_day_pct)
        if streak < needed:
            return False, None
        return True, (
            f"naked_consecutive_down_days: streak={streak} >= {needed} "
            f"(each <= -{format_decimal(self.config.naked_entry_down_day_pct, 4)})"
        )

    def _apply_naked_down_streak_regime(
        self,
        currency: str,
        regime: RiskRegime,
        detail: list[str],
    ) -> tuple[RiskRegime, list[str]]:
        if regime is not RiskRegime.NORMAL:
            return regime, detail
        down, note = self._naked_consecutive_down_elevated(currency)
        if not down or note is None:
            return regime, detail
        elevated = RiskRegime.ELEVATED
        self._last_regime_cache[currency] = (elevated, utc_now_ms())
        return elevated, [note]

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
        regime, detail = self._regime_from_macro_feeds(
            currency,
            drawdown=drawdown,
            dvol_ratio=dvol_ratio,
            unavailable_default=RiskRegime.ELEVATED,
            unavailable_note="data_unavailable",
        )
        return self._apply_naked_down_streak_regime(currency, regime, detail)

    def _determine_regime(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> RiskRegime:
        regime, _ = self._determine_regime_with_detail(currency, markets=markets, orderbook_cache=orderbook_cache)
        return regime
