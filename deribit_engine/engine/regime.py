"""Risk-regime determination (macro feeds + option-book liquidity) used by
:mod:`management`.

Macro feeds: 24h index drawdown, DVOL ratio, and optional 24h/48h index rally.
A fast rally is ``elevated`` (pause new entries) and never ``crisis``.
Dump / DVOL mapping can be disabled per strategy (covered call).
"""

from __future__ import annotations

from decimal import Decimal

from ..models import OptionInstrument, OrderBookSnapshot, RiskRegime
from ..utils import utc_now_ms
from ..vol_metrics import classify_macro_regime


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
        return_48h: Decimal | None = None,
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

        if return_48h is None and self.config.enable_index_rally_entry_halt:
            return_48h = self._index_return_48h(currency)

        regime, detail = classify_macro_regime(
            return_24h=drawdown,
            dvol_ratio=dvol_ratio,
            return_48h=return_48h,
            index_drawdown_elevated_pct=self.config.index_drawdown_elevated_pct,
            index_drawdown_crisis_pct=self.config.index_drawdown_crisis_pct,
            dvol_elevated_multiplier=self.config.dvol_elevated_multiplier,
            dvol_crisis_multiplier=self.config.dvol_crisis_multiplier,
            enable_index_rally_entry_halt=self.config.enable_index_rally_entry_halt,
            index_rally_24h_pct=self.config.index_rally_24h_pct,
            index_rally_48h_pct=self.config.index_rally_48h_pct,
            enable_index_dump_entry_halt=self.config.enable_index_dump_entry_halt,
        )

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
