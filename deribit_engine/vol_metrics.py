"""Volatility metrics for entry timing (IV rank, realized vol, IV-RV spread)."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from .utils import safe_div, to_decimal

ONE = Decimal("1")


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


def dvol_iv_rank_at_ts(
    dvol_series: Sequence[tuple[int, Decimal | float | str]],
    *,
    ts_ms: int,
    lookback_days: int = 252,
) -> Decimal | None:
    """IV rank using DVOL closes as IV proxy."""
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
        if iv_rank_value is None or iv_rank_value < min_iv_rank:
            return False
    if max_iv_rank > 0 and max_iv_rank < ONE:
        if iv_rank_value is not None and iv_rank_value > max_iv_rank:
            return False
    if min_iv_minus_rv > 0:
        if iv_minus_rv is None or iv_minus_rv < min_iv_minus_rv:
            return False
    return True
