"""Trend-adaptive selection for covered calls.

A covered call is written on coin you want to keep, so direction is not a symmetric
risk. A rising market calls the coin away: the target |delta| slides to the far edge of
the preferred band, the OTM floor lifts, and when the climb is both fast and part of an
uptrend no new call is written. A falling market makes a nearer strike richer and safer:
the target slides to the near edge, and the delta ceiling and OTM floor give a little.

Ported from Canopy (2026-09-14). Two differences here, both because this engine also runs
naked short and bull put accounts: the tilt and the pause are confined to covered calls,
and side selection (``enable_trend_side_bias``) is kept — it shares the reading, not the job.
"""

from __future__ import annotations

from decimal import Decimal

from conftest import make_config

from deribit_engine.strategy import StrategySelector
from deribit_engine.vol_metrics import TrendReading

BTC = "BTC"
#: The covered_call medium tier's BTC call bands.
MEDIUM = dict(
    btc_call_delta_min=Decimal("0.06"),
    btc_call_delta_max=Decimal("0.13"),
    btc_preferred_call_delta_min=Decimal("0.07"),
    btc_preferred_call_delta_max=Decimal("0.11"),
    btc_call_otm_min=Decimal("0.08"),
)


def _selector(tmp_path, *, signal=None, deviation=None, bull=True, **overrides) -> StrategySelector:
    """``signal`` is the clamped reading; ``deviation`` the raw one the pause reads.

    They default to agreeing (signal x 5%), which is what a real reading does below
    saturation. Pass ``deviation`` alone to test the range the clamp hides.
    """
    values = dict(
        option_strategy="covered_call",
        enable_short_call=True,
        enable_trend_adaptive_selection=True,
        trend_target_delta_strength=Decimal("1.0"),
        enable_dynamic_target_delta=False,
        **MEDIUM,
    )
    values.update(overrides)
    selector = StrategySelector(make_config(tmp_path, **values))
    if signal is not None or deviation is not None:
        sig = Decimal(signal if signal is not None else "0")
        dev = Decimal(deviation) if deviation is not None else sig * Decimal("0.05")
        selector.update_vol_entry_context(trend_by_currency={BTC: TrendReading(sig, dev, bull)})
    return selector


# --- off / no reading -----------------------------------------------------------


def test_off_leaves_selection_on_the_static_bands(tmp_path):
    up = _selector(tmp_path, signal="1", enable_trend_adaptive_selection=False)
    down = _selector(tmp_path, signal="-1", enable_trend_adaptive_selection=False)
    assert up._preferred_target_delta(BTC, "call") == down._preferred_target_delta(BTC, "call") == Decimal("0.09")
    assert up.effective_call_otm_min(BTC) == down.effective_call_otm_min(BTC) == Decimal("0.08")
    assert up.effective_delta_bounds(BTC, "call") == down.effective_delta_bounds(BTC, "call")
    assert down.trend_pause_reason_zh(BTC) is None


def test_no_reading_is_no_tilt(tmp_path):
    blind = _selector(tmp_path)
    assert blind._trend_tilt(BTC) == 0
    assert blind.effective_call_otm_min(BTC) == Decimal("0.08")


def test_a_signal_inside_the_deadband_is_noise(tmp_path):
    assert _selector(tmp_path, signal="0.01")._trend_tilt(BTC) == 0


# --- rising: stand further out ----------------------------------------------------


def test_rising_slides_to_the_far_edge_and_lifts_the_otm_floor(tmp_path):
    rising = _selector(tmp_path, signal="1")
    assert rising._preferred_target_delta(BTC, "call") == Decimal("0.07")
    assert rising.effective_call_otm_min(BTC) == Decimal("0.11")


def test_rising_never_widens_the_delta_ceiling(tmp_path):
    """The risk being managed is losing the coin; widening would add to it."""
    assert _selector(tmp_path, signal="1").effective_delta_bounds(BTC, "call") == (Decimal("0.06"), Decimal("0.13"))


# --- falling: lean in -------------------------------------------------------------


def test_falling_stretches_the_ceiling_but_never_the_floor(tmp_path):
    falling = _selector(tmp_path, signal="-1")
    assert falling.effective_delta_bounds(BTC, "call") == (Decimal("0.06"), Decimal("0.16"))
    assert falling.effective_preferred_delta_bounds(BTC, "call") == (Decimal("0.07"), Decimal("0.14"))
    assert falling._preferred_target_delta(BTC, "call") == Decimal("0.14")


def test_the_otm_floor_moves_in_proportion(tmp_path):
    """Three points off an 8% floor is a nudge; off a 5% one it would be most of the distance."""
    assert _selector(tmp_path, signal="-1").effective_call_otm_min(BTC) == Decimal("0.05")
    assert _selector(tmp_path, signal="-1", btc_call_otm_min=Decimal("0.05")).effective_call_otm_min(BTC) == Decimal("0.03125")


# --- the pause: speed and direction -------------------------------------------------


def test_the_pause_reads_past_the_clamp(tmp_path):
    """5% and 12% above the average both clamp to 1.0; the pause must still tell them apart."""
    mild = _selector(tmp_path, signal="1", deviation="0.05", trend_pause_above_pct=Decimal("0.09"))
    fierce = _selector(tmp_path, signal="1", deviation="0.12", trend_pause_above_pct=Decimal("0.09"))
    assert mild._trend_tilt(BTC) == fierce._trend_tilt(BTC) == Decimal("1")
    assert mild.trend_pause_reason_zh(BTC) is None
    assert fierce.trend_pause_reason_zh(BTC) is not None


def test_the_pause_holds_just_below_the_threshold(tmp_path):
    assert _selector(tmp_path, deviation="0.0499").trend_pause_reason_zh(BTC) is None


def test_the_pause_needs_the_uptrend_structure_too(tmp_path):
    assert _selector(tmp_path, deviation="0.09", bull=False).trend_pause_reason_zh(BTC) is None
    gate_off = _selector(tmp_path, deviation="0.09", bull=False, trend_pause_requires_bull_regime=False)
    assert gate_off.trend_pause_reason_zh(BTC) is not None


def test_the_pause_reason_states_the_reading(tmp_path):
    reason = _selector(tmp_path, deviation="0.09").trend_pause_reason_zh(BTC)
    assert reason is not None
    assert "9.0%" in reason and "20" in reason and "100" in reason


def test_zero_disables_the_pause_but_not_the_shifts(tmp_path):
    never = _selector(tmp_path, signal="1", deviation="0.20", trend_pause_above_pct=Decimal("0"))
    assert never.trend_pause_reason_zh(BTC) is None
    assert never.effective_call_otm_min(BTC) == Decimal("0.11")


def test_falling_never_pauses(tmp_path):
    assert _selector(tmp_path, signal="-1", deviation="-0.09").trend_pause_reason_zh(BTC) is None


# --- this engine runs more than covered calls ---------------------------------------


def test_other_strategies_never_tilt_or_pause_even_with_the_flag_on(tmp_path):
    """For a short put a rising market is the safe one, so the call-side reading would be backwards."""
    naked = _selector(tmp_path, signal="1", deviation="0.09", option_strategy="naked_short")
    assert naked._trend_tilt(BTC) == 0
    assert naked.trend_pause_reason_zh(BTC) is None
    assert naked.effective_call_otm_min(BTC) == Decimal("0.08")


def test_adaptive_selection_does_not_arm_side_selection(tmp_path):
    s = _selector(tmp_path, signal="1", enable_trend_side_bias=False)
    assert s._trend_side_signal(BTC) is None
    assert s._trend_tilt(BTC) == Decimal("1")


def test_a_bare_signal_still_drives_side_selection_but_cannot_pause(tmp_path):
    """Callers that predate ``TrendReading`` pass a number; it is wrapped with no regime."""
    s = _selector(tmp_path, enable_trend_side_bias=True)
    s.update_vol_entry_context(trend_by_currency={BTC: Decimal("0.8")})
    assert s._trend_side_signal(BTC) == Decimal("0.8")
    s.update_vol_entry_context(trend_by_currency={BTC: Decimal("5")})
    assert s._trend_tilt(BTC) == Decimal("1")
    assert s.trend_pause_reason_zh(BTC) is None


def test_vol_and_trend_add_up_then_clamp_once(tmp_path):
    both = _selector(tmp_path, signal="1", enable_dynamic_target_delta=True)
    both._iv_minus_rv_by_currency = {BTC: Decimal("0.20")}
    assert both._preferred_target_delta(BTC, "call") == Decimal("0.07")
