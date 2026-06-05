"""Tests for cross-book flow inference used in drawdown gates."""

from __future__ import annotations

from decimal import Decimal

from deribit_engine.cross_book_flow import cross_book_flow_adjustments_native


def test_cross_book_flow_matches_usdt_to_btc_swap():
    """USDT spent on a BTC spot buy must not leave phantom USDT drawdown."""
    adjustments = cross_book_flow_adjustments_native(
        per_book_native_equities={
            "BTC": Decimal("0.1792586"),
            "ETH": Decimal("3.158351"),
            "USDT": Decimal("1.92231"),
        },
        per_book_native_day_start={
            "BTC": Decimal("0.17784213"),
            "ETH": Decimal("3.151109"),
            "USDT": Decimal("76.32231"),
        },
        day_net_flow_native_by_book={"BTC": Decimal("0"), "ETH": Decimal("0"), "USDT": Decimal("0")},
        day_net_flow_usdc_by_book={"BTC": Decimal("0"), "ETH": Decimal("0"), "USDT": Decimal("0")},
        index_price_by_book={"BTC": Decimal("70000"), "ETH": Decimal("3500")},
        min_match_usdc=Decimal("10"),
    )

    assert adjustments["USDT"] < Decimal("-70")
    assert adjustments["BTC"] > Decimal("0")


def test_cross_book_flow_does_not_mask_real_eth_loss():
    """Native ETH loss with no stable inflow must not be paired away."""
    adjustments = cross_book_flow_adjustments_native(
        per_book_native_equities={"ETH": Decimal("8"), "USDT": Decimal("1000")},
        per_book_native_day_start={"ETH": Decimal("10"), "USDT": Decimal("1000")},
        day_net_flow_native_by_book={"ETH": Decimal("0"), "USDT": Decimal("0")},
        day_net_flow_usdc_by_book={"ETH": Decimal("0"), "USDT": Decimal("0")},
        index_price_by_book={"ETH": Decimal("3500")},
        min_match_usdc=Decimal("10"),
    )

    assert adjustments == {}


def test_cross_book_flow_matches_btc_to_usdt_profit_sweep():
    """Selling BTC for USDT (profit sweep) must not trip BTC native drawdown."""
    adjustments = cross_book_flow_adjustments_native(
        per_book_native_equities={"BTC": Decimal("0.174"), "USDT": Decimal("150")},
        per_book_native_day_start={"BTC": Decimal("0.177"), "USDT": Decimal("50")},
        day_net_flow_native_by_book={"BTC": Decimal("0"), "USDT": Decimal("0")},
        day_net_flow_usdc_by_book={"BTC": Decimal("0"), "USDT": Decimal("0")},
        index_price_by_book={"BTC": Decimal("70000")},
        min_match_usdc=Decimal("10"),
    )

    assert adjustments["BTC"] < Decimal("0")
    assert adjustments["USDT"] > Decimal("0")
