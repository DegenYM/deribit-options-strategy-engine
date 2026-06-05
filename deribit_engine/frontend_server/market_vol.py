"""Public market volatility metrics for the dashboard header."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..client import DeribitClient
from ..utils import format_decimal, to_decimal, utc_now_ms
from ..vol_metrics import dvol_iv_rank_from_daily_rows

_DEFAULT_CURRENCIES = ("BTC", "ETH")
_DEFAULT_LOOKBACK_DAYS = 365


def _index_return_24h(client: DeribitClient, currency: str) -> Decimal | None:
    """Return 24h index return as decimal ((end/start) - 1), or None if unavailable."""
    ccy = str(currency or "").upper()
    any_success = False
    for index_name in (f"{ccy.lower()}_usdc", f"{ccy.lower()}_usd"):
        try:
            points = client.get_index_chart_data(index_name, range_name="1d")
        except Exception:
            continue
        if not points:
            any_success = True
            continue
        if len(points) >= 2:
            start_price = to_decimal(points[0][1])
            end_price = to_decimal(points[-1][1])
            if start_price > 0:
                return (end_price / start_price) - Decimal("1")
        any_success = True
    if any_success:
        return None
    return None


def fetch_index_price_change_24h_pct(
    client: DeribitClient,
    *,
    currencies: Iterable[str] = _DEFAULT_CURRENCIES,
) -> dict[str, str | None]:
    """Return per-currency 24h index price change percent (e.g. ``-2.3``)."""
    change_pct: dict[str, str | None] = {}
    for currency in currencies:
        ccy = str(currency or "").upper()
        if not ccy:
            continue
        ret = _index_return_24h(client, ccy)
        if ret is None:
            change_pct[ccy] = None
            continue
        pct = (ret * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        change_pct[ccy] = format_decimal(pct, 1)
    return change_pct


def fetch_iv_rank_snapshot(
    client: DeribitClient,
    *,
    currencies: Iterable[str] = _DEFAULT_CURRENCIES,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Return per-currency DVOL and IV rank for dashboard display."""
    end_timestamp = utc_now_ms()
    start_timestamp = end_timestamp - (lookback_days * 24 * 3600 * 1000)
    iv_rank: dict[str, str | None] = {}
    iv_rank_pct: dict[str, str | None] = {}
    dvol: dict[str, str | None] = {}
    for currency in currencies:
        ccy = str(currency or "").upper()
        if not ccy:
            continue
        try:
            payload = client.get_volatility_index_data(
                ccy,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                resolution="1D",
            )
            rows = payload.get("data") or []
            rank = dvol_iv_rank_from_daily_rows(
                rows,
                ts_ms=end_timestamp,
                lookback_days=lookback_days,
            )
            latest = to_decimal(rows[-1][4]) if rows else None
            if rank is not None:
                iv_rank[ccy] = format_decimal(rank, 4)
                pct = (rank * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                iv_rank_pct[ccy] = format_decimal(pct, 1)
            else:
                iv_rank[ccy] = None
                iv_rank_pct[ccy] = None
            dvol[ccy] = format_decimal(latest, 2) if latest is not None and latest > 0 else None
        except Exception:
            iv_rank[ccy] = None
            iv_rank_pct[ccy] = None
            dvol[ccy] = None
    return {
        "iv_rank": iv_rank,
        "iv_rank_pct": iv_rank_pct,
        "dvol": dvol,
        "iv_rank_lookback_days": lookback_days,
    }
