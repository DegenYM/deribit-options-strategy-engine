"""Golden tests for the standard-margin formulas in ``deribit_engine.margin``.

These functions back every IM/MM gate in the engine but were previously only
exercised indirectly through strategy/engine tests. The expected values here are
hand-derived from the documented Deribit "Standard Margin" curves so a future
formula change surfaces as an explicit, reviewable diff.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from deribit_engine import margin


def D(value: str) -> Decimal:
    return Decimal(value)


# ---------------------------------------------------------------------------
# Short put — per-coin unit margins
# ---------------------------------------------------------------------------


def test_short_put_maintenance_unit_floor_and_mark():
    # mark_price small: 0.075 floor dominates, then + mark_price.
    assert margin.short_put_maintenance_unit(index_price=D("70000"), strike=D("60000"), mark_price=D("0.01")) == D(
        "0.085"
    )


def test_short_put_maintenance_unit_scales_with_large_mark():
    # mark_price > 1 makes 0.075 * mark exceed the flat 0.075 floor.
    assert margin.short_put_maintenance_unit(index_price=D("70000"), strike=D("60000"), mark_price=D("2")) == D("2.15")


def test_short_put_initial_unit_otm_uses_floor():
    # OTM put: 0.15 - otm_frac (~0.0071) < 0.1 floor → inner = 0.1 + mark.
    assert margin.short_put_initial_unit(index_price=D("70000"), strike=D("60000"), mark_price=D("0.01")) == D("0.11")


def test_short_put_initial_unit_atm_uses_full_addon():
    # ATM put: otm_frac = 0 → inner = 0.15 + mark.
    assert margin.short_put_initial_unit(index_price=D("70000"), strike=D("70000"), mark_price=D("0.05")) == D("0.20")


def test_short_put_initial_unit_zero_index_is_zero():
    assert margin.short_put_initial_unit(index_price=D("0"), strike=D("60000"), mark_price=D("0.01")) == D("0")


# ---------------------------------------------------------------------------
# Short call — per-coin unit margins
# ---------------------------------------------------------------------------


def test_short_call_maintenance_unit_otm_floor():
    assert margin.short_call_maintenance_unit(index_price=D("70000"), strike=D("80000"), mark_price=D("0.01")) == D(
        "0.085"
    )


def test_short_call_maintenance_unit_itm_grows():
    # ITM call: itm_delta = (80000-70000)/80000 = 0.125 → 0.075 + 0.125 = 0.2, + mark.
    assert margin.short_call_maintenance_unit(index_price=D("80000"), strike=D("70000"), mark_price=D("0.02")) == D(
        "0.22"
    )


def test_short_call_maintenance_unit_zero_index():
    assert margin.short_call_maintenance_unit(index_price=D("0"), strike=D("70000"), mark_price=D("0.03")) == D("0.105")


def test_short_call_initial_unit_otm_uses_floor():
    assert margin.short_call_initial_unit(index_price=D("70000"), strike=D("80000"), mark_price=D("0.01")) == D("0.11")


def test_short_call_initial_unit_itm_floored_by_mm():
    # ITM call: inner = 0.15 + 0.02 = 0.17, but MM = 0.22 dominates.
    assert margin.short_call_initial_unit(index_price=D("80000"), strike=D("70000"), mark_price=D("0.02")) == D("0.22")


def test_short_call_initial_unit_zero_index():
    assert margin.short_call_initial_unit(index_price=D("0"), strike=D("70000"), mark_price=D("0.04")) == D("0.14")


# ---------------------------------------------------------------------------
# Linear USDC mapping helpers
# ---------------------------------------------------------------------------


def test_linear_usdc_premium_as_btc_fraction():
    assert margin.linear_usdc_put_premium_as_btc_fraction(
        mark_usdc=D("700"), index_price=D("70000"), contract_size=D("1")
    ) == D("0.01")


@pytest.mark.parametrize("index_price,contract_size", [(D("0"), D("1")), (D("70000"), D("0"))])
def test_linear_usdc_premium_fraction_guards_zero(index_price, contract_size):
    assert margin.linear_usdc_put_premium_as_btc_fraction(
        mark_usdc=D("700"), index_price=index_price, contract_size=contract_size
    ) == D("0")


def test_linear_usdc_short_put_initial_per_contract_usdc():
    # mb = 700/70000 = 0.01 → im_btc = 0.11 → 0.11 * 70000 * 1 = 7700.
    out = margin.linear_usdc_short_put_initial_per_contract_usdc(
        index_price=D("70000"), strike=D("60000"), mark_usdc=D("700"), contract_size=D("1")
    )
    assert out == D("7700")


def test_linear_usdc_short_put_mm_per_contract_usdc():
    # mb = 0.01 → mm_btc = 0.085 → 0.085 * 70000 = 5950.
    out = margin.linear_usdc_short_put_mm_per_contract_usdc(
        index_price=D("70000"), strike=D("60000"), mark_usdc=D("700"), contract_size=D("1")
    )
    assert out == D("5950")


def test_linear_usdc_short_call_initial_per_contract_usdc():
    # OTM call mb = 0.01 → im_btc = 0.11 → 0.11 * 70000 = 7700.
    out = margin.linear_usdc_short_call_initial_per_contract_usdc(
        index_price=D("70000"), strike=D("80000"), mark_usdc=D("700"), contract_size=D("1")
    )
    assert out == D("7700")


def test_linear_usdc_short_call_mm_per_contract_usdc():
    out = margin.linear_usdc_short_call_mm_per_contract_usdc(
        index_price=D("70000"), strike=D("80000"), mark_usdc=D("700"), contract_size=D("1")
    )
    assert out == D("5950")


def test_linear_usdc_scales_with_contract_size():
    one = margin.linear_usdc_short_put_initial_per_contract_usdc(
        index_price=D("70000"), strike=D("60000"), mark_usdc=D("700"), contract_size=D("1")
    )
    ten = margin.linear_usdc_short_put_initial_per_contract_usdc(
        index_price=D("70000"), strike=D("60000"), mark_usdc=D("7000"), contract_size=D("10")
    )
    # Same per-unit economics, 10x contract size → 10x IM.
    assert ten == one * D("10")
