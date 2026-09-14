"""Trend readings: the average counts days, and price is measured separately.

``public/get_index_chart_data`` with a ``1y`` range returns 6-hourly prints, so an average
over the last 20 elements was a five-day average. The daily resampling helper was already
in ``vol_metrics``; the trend path never called it. The engine now reads settled daily
closes (``public/get_delivery_prices``) instead.
"""

from __future__ import annotations

from decimal import Decimal

from deribit_engine.vol_metrics import (
    TrendReading,
    trend_reading_from_daily_closes,
    trend_reading_from_index_series,
    trend_signal_from_index_series,
)

DAY_MS = 86_400_000
SIX_HOURS_MS = DAY_MS // 4


def _six_hourly(daily_closes: list[float]) -> list[tuple[int, Decimal]]:
    """Four prints per day, the last one landing on that day's close."""
    out: list[tuple[int, Decimal]] = []
    for day, close in enumerate(daily_closes):
        prev = daily_closes[day - 1] if day else close
        for bar in range(4):
            px = prev + (close - prev) * (bar + 1) / 4
            out.append((day * DAY_MS + bar * SIX_HOURS_MS, Decimal(str(round(px, 2)))))
    return out


def test_the_window_counts_days_not_bars():
    series = _six_hourly([100.0] * 20 + [110.0] * 5)
    end = series[-1][0]
    twenty = trend_reading_from_index_series(series, end_ts_ms=end, ma_window=20, regime_ma_window=100)
    assert twenty is not None
    # 20-day average = (100*15 + 110*5)/20 = 102.5 → +7.3%
    assert twenty.deviation.quantize(Decimal("0.001")) == Decimal("0.073")
    five = trend_reading_from_index_series(series, end_ts_ms=end, ma_window=5, regime_ma_window=100)
    assert five is not None and five.deviation == 0


def test_the_signal_helper_resamples_too():
    series = _six_hourly([100.0] * 20 + [110.0] * 5)
    signal = trend_signal_from_index_series(series, end_ts_ms=series[-1][0], ma_window=20)
    assert signal is not None and signal > Decimal("0.9")


def test_not_enough_history_is_no_reading():
    series = _six_hourly([100.0] * 10)
    assert trend_reading_from_index_series(series, end_ts_ms=series[-1][0], ma_window=20) is None


def test_a_short_history_never_claims_a_bull_regime():
    """False, not None: missing data must not be able to start the pause."""
    series = _six_hourly([100.0] * 30)
    reading = trend_reading_from_index_series(series, end_ts_ms=series[-1][0], ma_window=20, regime_ma_window=100)
    assert isinstance(reading, TrendReading) and reading.bull_regime is False


def test_the_regime_reads_short_ma_against_long_ma():
    for closes, expected in (([100.0 + i * 0.5 for i in range(120)], True), ([160.0 - i * 0.5 for i in range(120)], False)):
        series = _six_hourly(closes)
        reading = trend_reading_from_index_series(series, end_ts_ms=series[-1][0], ma_window=20, regime_ma_window=100)
        assert reading is not None and reading.bull_regime is expected


def test_rows_after_the_cutoff_are_ignored():
    series = _six_hourly([100.0] * 20 + [110.0] * 5)
    reading = trend_reading_from_index_series(series, end_ts_ms=series[20 * 4 - 1][0], ma_window=20)
    assert reading is not None and reading.deviation == 0


def test_the_average_and_the_price_are_separate_inputs():
    closes = [Decimal("100")] * 20
    assert trend_reading_from_daily_closes(closes, price=Decimal("100"), ma_window=20).deviation == 0
    assert trend_reading_from_daily_closes(closes, price=Decimal("110"), ma_window=20).deviation == Decimal("0.1")


def test_a_hundred_day_regime_needs_a_hundred_days():
    closes = [Decimal(str(100 + i)) for i in range(99)]
    short = trend_reading_from_daily_closes(closes, price=Decimal("200"), ma_window=20, regime_ma_window=100)
    assert short is not None and short.bull_regime is False
    closes.append(Decimal("199"))
    ok = trend_reading_from_daily_closes(closes, price=Decimal("200"), ma_window=20, regime_ma_window=100)
    assert ok is not None and ok.bull_regime is True


def test_no_price_is_no_reading():
    assert trend_reading_from_daily_closes([Decimal("100")] * 20, price=Decimal("0"), ma_window=20) is None
