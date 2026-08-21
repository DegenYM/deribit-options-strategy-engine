"""Volatility metrics for entry timing (IV rank, realized vol, IV-RV spread)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from .models import RiskRegime
from .utils import format_decimal, safe_div, to_decimal

ONE = Decimal("1")
MS_PER_HOUR = 3_600_000
INDEX_RETURN_24H_MS = 24 * MS_PER_HOUR
INDEX_RETURN_48H_MS = 48 * MS_PER_HOUR


def iv_rank(
    current_iv: Decimal,
    *,
    lookback: Sequence[Decimal],
) -> Decimal | None:
    """IV rank in [0, 1]: (current - min) / (max - min) over lookback."""
    values = [v for v in lookback if v is not None and v > 0]
    if not values or current_iv <= 0:
        return None
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return None
    return safe_div(current_iv - lo, hi - lo)


def realized_vol_annualized(
    closes: Sequence[Decimal],
    *,
    window: int = 30,
) -> Decimal | None:
    """Close-to-close annualized realized vol (252 trading days)."""
    if window < 2 or len(closes) < window + 1:
        return None
    sample = closes[-(window + 1) :]
    returns: list[Decimal] = []
    for i in range(1, len(sample)):
        prev, cur = sample[i - 1], sample[i]
        if prev <= 0 or cur <= 0:
            continue
        returns.append(cur / prev - ONE)
    if len(returns) < 2:
        return None
    mean = sum(returns) / Decimal(len(returns))
    var = sum((r - mean) ** 2 for r in returns) / Decimal(len(returns) - 1)
    if var <= 0:
        return None
    import math

    daily_std = Decimal(str(math.sqrt(float(var))))
    return daily_std * Decimal(str(math.sqrt(252.0)))


def index_chart_close_series(
    payload: Sequence[list[Any] | tuple[Any, ...]],
) -> list[tuple[int, Decimal]]:
    """Normalize ``public/get_index_chart_data`` rows to ``(ts_ms, close)``."""
    out: list[tuple[int, Decimal]] = []
    for row in payload or []:
        if not isinstance(row, list | tuple) or len(row) < 2:
            continue
        close_raw = row[4] if len(row) >= 5 else row[1]
        close = to_decimal(close_raw)
        if close > 0:
            out.append((int(row[0]), close))
    return out


def realized_vol_annualized_from_index_series(
    series: Sequence[tuple[int, Decimal | float | str]],
    *,
    end_ts_ms: int,
    window: int = 30,
) -> Decimal | None:
    """Annualized HV from ``(ts_ms, close)`` index series up to ``end_ts_ms``."""
    closes: list[Decimal] = []
    for ts, val in series:
        if int(ts) > end_ts_ms:
            break
        closes.append(to_decimal(val))
    return realized_vol_annualized(closes, window=window)


def iv_minus_rv_spread(*, iv: Decimal, rv: Decimal) -> Decimal | None:
    """Vol risk premium proxy: IV minus realized vol (both annualized decimals)."""
    if iv <= 0 or rv <= 0:
        return None
    return iv - rv


def _dvol_daily_candles(
    rows: Sequence[Sequence[Any] | tuple[Any, ...]],
    *,
    ts_ms: int,
    lookback_days: int,
) -> list[tuple[int, Decimal, Decimal, Decimal]]:
    """Parse DVOL OHLC rows into ``(ts, high, low, close)`` up to ``ts_ms``."""
    candles: list[tuple[int, Decimal, Decimal, Decimal]] = []
    for row in rows or []:
        if not isinstance(row, list | tuple) or len(row) < 5:
            continue
        ts = int(row[0])
        if ts > ts_ms:
            break
        high = to_decimal(row[2])
        low = to_decimal(row[3])
        close = to_decimal(row[4])
        if high > 0 and low > 0 and close > 0:
            candles.append((ts, high, low, close))
    if not candles or lookback_days <= 0:
        return []
    return candles[-lookback_days:]


def dvol_iv_rank_from_daily_rows(
    rows: Sequence[Sequence[Any] | tuple[Any, ...]],
    *,
    ts_ms: int,
    lookback_days: int = 365,
) -> Decimal | None:
    """IV rank from DVOL daily candles (Deribit official method).

    Uses the last ``lookback_days`` candles with ``ts <= ts_ms``:
    range = min(low) .. max(high), current = last close.
    See https://insights.deribit.com/education/iv-rank-and-iv-percentile/
    """
    window = _dvol_daily_candles(rows, ts_ms=ts_ms, lookback_days=lookback_days)
    if not window:
        return None
    current = window[-1][3]
    yr_lo = min(c[2] for c in window)
    yr_hi = max(c[1] for c in window)
    if yr_hi <= yr_lo:
        return None
    return safe_div(current - yr_lo, yr_hi - yr_lo)


def dvol_iv_percentile_from_daily_rows(
    rows: Sequence[Sequence[Any] | tuple[Any, ...]],
    *,
    ts_ms: int,
    lookback_days: int = 365,
) -> Decimal | None:
    """IV percentile in [0, 1] from DVOL daily closes (Deribit official method).

    Share of lookback daily closes that sit *below* the latest close.
    See https://insights.deribit.com/education/iv-rank-and-iv-percentile/
    """
    window = _dvol_daily_candles(rows, ts_ms=ts_ms, lookback_days=lookback_days)
    if len(window) < 2:
        return None
    current = window[-1][3]
    below = sum(1 for _ts, _high, _low, close in window if close < current)
    return safe_div(Decimal(below), Decimal(len(window)))


def dvol_iv_rank_at_ts(
    dvol_series: Sequence[tuple[int, Decimal | float | str]],
    *,
    ts_ms: int,
    lookback_days: int = 365,
) -> Decimal | None:
    """IV rank using DVOL closes only (fallback when OHLC rows are unavailable)."""
    points: list[tuple[int, Decimal]] = []
    for ts, val in dvol_series:
        if int(ts) > ts_ms:
            break
        iv = to_decimal(val)
        if iv > 0:
            points.append((int(ts), iv))
    if not points:
        return None
    current = points[-1][1]
    lookback = [iv for _ts, iv in points[-lookback_days:]]
    return iv_rank(current, lookback=lookback)


def trend_signal_vs_ma(
    closes: Sequence[Decimal],
    *,
    ma_window: int = 20,
    ref_pct: Decimal = Decimal("0.05"),
) -> Decimal | None:
    """Price-vs-MA trend signal in [-1, 1].

    +1 means spot is ``ref_pct`` or more above the simple moving average
    (bullish); -1 means at or below ``-ref_pct`` (bearish). Used to tilt naked
    short side selection: bullish -> favor short puts, bearish -> favor calls.
    """
    if ma_window < 2 or len(closes) < ma_window:
        return None
    price = closes[-1]
    if price <= 0:
        return None
    window = closes[-ma_window:]
    ma = sum(window) / Decimal(len(window))
    if ma <= 0 or ref_pct <= 0:
        return None
    deviation = (price - ma) / ma
    signal = deviation / ref_pct
    return max(Decimal("-1"), min(Decimal("1"), signal))


def trend_signal_from_index_series(
    series: Sequence[tuple[int, Decimal | float | str]],
    *,
    end_ts_ms: int,
    ma_window: int = 20,
    ref_pct: Decimal = Decimal("0.05"),
) -> Decimal | None:
    """Trend signal from ``(ts_ms, close)`` index series up to ``end_ts_ms``."""
    closes: list[Decimal] = []
    for ts, val in series:
        if int(ts) > end_ts_ms:
            break
        close = to_decimal(val)
        if close > 0:
            closes.append(close)
    return trend_signal_vs_ma(closes, ma_window=ma_window, ref_pct=ref_pct)


def index_return_over_lookback(
    series: Sequence[tuple[int, Decimal | float | str]],
    *,
    lookback_ms: int,
    min_coverage: Decimal = Decimal("0.90"),
) -> Decimal | None:
    """Return ``(end / start) - 1`` over ``lookback_ms``.

    ``start`` is the last close at or before ``end_ts - lookback_ms``. Returns
    ``None`` when history does not cover at least ``min_coverage`` of the window,
    so a 24h series is not treated as a 48h move.
    """
    points: list[tuple[int, Decimal]] = []
    for ts, val in series:
        close = to_decimal(val)
        if int(ts) > 0 and close > 0:
            points.append((int(ts), close))
    if lookback_ms <= 0 or len(points) < 2:
        return None
    end_ts, end_px = points[-1]
    target_ts = end_ts - lookback_ms
    start: tuple[int, Decimal] | None = None
    for ts, px in points:
        if ts <= target_ts:
            start = (ts, px)
        else:
            break
    if start is None:
        return None
    start_ts, start_px = start
    if start_px <= 0:
        return None
    coverage = Decimal(end_ts - start_ts)
    needed = Decimal(lookback_ms) * min_coverage
    if coverage < needed:
        return None
    return (end_px / start_px) - ONE


def classify_macro_regime(
    *,
    return_24h: Decimal,
    dvol_ratio: Decimal,
    return_48h: Decimal | None = None,
    index_drawdown_elevated_pct: Decimal,
    index_drawdown_crisis_pct: Decimal,
    dvol_elevated_multiplier: Decimal,
    dvol_crisis_multiplier: Decimal,
    enable_index_rally_entry_halt: bool = True,
    index_rally_24h_pct: Decimal = Decimal("0.05"),
    index_rally_48h_pct: Decimal = Decimal("0.07"),
) -> tuple[RiskRegime, list[str]]:
    """Map index return + DVOL ratio to ``normal`` / ``elevated`` / ``crisis``.

    Rapid upside (24h / 48h rally) is ``elevated`` only — it pauses new entries
    via the existing non-NORMAL halt, and must never become ``crisis`` (which can
    hard-derisk open winners).
    """
    if return_24h <= -index_drawdown_crisis_pct:
        return RiskRegime.CRISIS, [
            "index_24h_drawdown <= -index_drawdown_crisis_pct "
            f"({format_decimal(return_24h, 8)} <= -{format_decimal(index_drawdown_crisis_pct, 6)})",
        ]
    if dvol_ratio > dvol_crisis_multiplier:
        return RiskRegime.CRISIS, [
            "dvol_ratio > dvol_crisis_multiplier "
            f"({format_decimal(dvol_ratio, 6)} > {format_decimal(dvol_crisis_multiplier, 6)})",
        ]
    if return_24h <= -index_drawdown_elevated_pct or dvol_ratio > dvol_elevated_multiplier:
        return RiskRegime.ELEVATED, [
            f"elevated: drawdown={format_decimal(return_24h, 8)} "
            f"dvol_ratio={format_decimal(dvol_ratio, 6)} "
            f"(thresholds -elevated {format_decimal(index_drawdown_elevated_pct, 6)} / "
            f"{format_decimal(dvol_elevated_multiplier, 6)})",
        ]
    if enable_index_rally_entry_halt:
        if index_rally_24h_pct > 0 and return_24h >= index_rally_24h_pct:
            return RiskRegime.ELEVATED, [
                "index_24h_rally >= index_rally_24h_pct "
                f"({format_decimal(return_24h, 8)} >= {format_decimal(index_rally_24h_pct, 6)})",
            ]
        if return_48h is not None and index_rally_48h_pct > 0 and return_48h >= index_rally_48h_pct:
            return RiskRegime.ELEVATED, [
                "index_48h_rally >= index_rally_48h_pct "
                f"({format_decimal(return_48h, 8)} >= {format_decimal(index_rally_48h_pct, 6)})",
            ]
    return RiskRegime.NORMAL, ["market_conditions_normal"]


def passes_iv_entry_gate(
    *,
    iv_rank_value: Decimal | None,
    iv_minus_rv: Decimal | None,
    min_iv_rank: Decimal,
    max_iv_rank: Decimal,
    min_iv_minus_rv: Decimal,
    gate_enabled: bool,
) -> bool:
    """Return True when vol gates are disabled or all configured thresholds pass."""
    if not gate_enabled:
        return True
    if min_iv_rank > 0:
        if iv_rank_value is not None and iv_rank_value < min_iv_rank:
            return False
    if max_iv_rank > 0 and max_iv_rank < ONE:
        if iv_rank_value is not None and iv_rank_value > max_iv_rank:
            return False
    if min_iv_minus_rv > 0:
        if iv_minus_rv is not None and iv_minus_rv < min_iv_minus_rv:
            return False
    return True
