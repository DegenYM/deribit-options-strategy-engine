"""Inverse covered-call P&L: coin (native) primary, USD mark as the pair."""

from __future__ import annotations


def settlement_coin(*, expiry: float, strike: float, qty: float = 1.0) -> float:
    if expiry <= 0 or expiry <= strike or qty <= 0:
        return 0.0
    return qty * (expiry - strike) / expiry


def overlay_coin(*, expiry: float, strike: float, premium_coin: float, qty: float = 1.0) -> float:
    """Option-leg P&L in BTC/ETH: premium received minus inverse settlement."""
    return premium_coin - settlement_coin(expiry=expiry, strike=strike, qty=qty)


def overlay_usd(*, coin_pnl: float, expiry: float) -> float:
    """Mark the coin overlay at expiry spot (U-denominated)."""
    return coin_pnl * expiry


def book_usd(*, coin_pnl: float, expiry: float, entry_spot: float, qty: float = 1.0) -> float:
    """Whole package vs buying ``qty`` coins at entry: remaining coins × expiry − cost."""
    return (qty + coin_pnl) * expiry - qty * entry_spot
