from __future__ import annotations

from decimal import Decimal

from .utils import safe_div


def short_put_maintenance_unit(*, index_price: Decimal, strike: Decimal, mark_price: Decimal) -> Decimal:
    """Deribit standard margin: MM per 1 unit (coin) for short put."""
    del index_price, strike
    return max(Decimal("0.075"), Decimal("0.075") * mark_price) + mark_price


def short_put_initial_unit(*, index_price: Decimal, strike: Decimal, mark_price: Decimal) -> Decimal:
    """Deribit standard margin: initial margin per 1 contract (coin) for short put."""
    if index_price <= 0:
        return Decimal("0")
    otm = max(index_price - strike, Decimal("0"))
    otm_frac = safe_div(otm, index_price)
    inner = max(Decimal("0.15") - otm_frac, Decimal("0.1")) + mark_price
    mm = short_put_maintenance_unit(index_price=index_price, strike=strike, mark_price=mark_price)
    return max(inner, mm)


def linear_usdc_put_premium_as_btc_fraction(*, mark_usdc: Decimal, index_price: Decimal, contract_size: Decimal) -> Decimal:
    """Map linear option premium (USDC per contract) to BTC-fraction input for standard IM curve (per README proxy)."""
    if index_price <= 0 or contract_size <= 0:
        return Decimal("0")
    premium_usd_per_unit_underlying = mark_usdc / contract_size
    return safe_div(premium_usd_per_unit_underlying, index_price)


def linear_usdc_short_put_initial_per_contract_usdc(
    *,
    index_price: Decimal,
    strike: Decimal,
    mark_usdc: Decimal,
    contract_size: Decimal,
) -> Decimal:
    mb = linear_usdc_put_premium_as_btc_fraction(mark_usdc=mark_usdc, index_price=index_price, contract_size=contract_size)
    im_btc = short_put_initial_unit(index_price=index_price, strike=strike, mark_price=mb)
    return im_btc * index_price * contract_size


def linear_usdc_short_put_mm_per_contract_usdc(
    *,
    index_price: Decimal,
    strike: Decimal,
    mark_usdc: Decimal,
    contract_size: Decimal,
) -> Decimal:
    mb = linear_usdc_put_premium_as_btc_fraction(mark_usdc=mark_usdc, index_price=index_price, contract_size=contract_size)
    mm_btc = short_put_maintenance_unit(index_price=index_price, strike=strike, mark_price=mb)
    return mm_btc * index_price * contract_size


# ---------------------------------------------------------------------------
# Short call margin (approximations of Deribit "Standard Margin").
#
# Deribit's public docs state the short-call formulas as, per 1 coin:
#   MM_call ≈ max(0.075, 0.075 + (strike - index) / index) + mark_price
#   IM_call ≈ max(0.15 - ITM_fraction, 0.1) + mark_price  (ITM_fraction = (index - strike)/index when ITM)
# We mirror the put helpers so callers can switch by option_type without pulling
# apart every formula in one go; see README_DERIBIT_SHORT_PUT.md for the refs.
# ---------------------------------------------------------------------------


def short_call_maintenance_unit(*, index_price: Decimal, strike: Decimal, mark_price: Decimal) -> Decimal:
    """MM per 1 unit (coin) for a short call.

    Call MM grows as the option moves ITM (``strike < index``) to capture the
    risk of unbounded upside. The 7.5% floor keeps MM sane on far-OTM calls.
    """
    if index_price <= 0:
        return Decimal("0.075") + mark_price
    itm_delta = safe_div(index_price - strike, index_price)
    return max(Decimal("0.075"), Decimal("0.075") + itm_delta) + mark_price


def short_call_initial_unit(*, index_price: Decimal, strike: Decimal, mark_price: Decimal) -> Decimal:
    """IM per 1 unit (coin) for a short call.

    For an OTM short call the IM add-on shrinks linearly as strike moves further
    above index; IM is floored by ``MM`` to avoid under-collateralization once ITM.
    """
    if index_price <= 0:
        return Decimal("0.10") + mark_price
    otm = max(strike - index_price, Decimal("0"))
    otm_frac = safe_div(otm, index_price)
    inner = max(Decimal("0.15") - otm_frac, Decimal("0.10")) + mark_price
    mm = short_call_maintenance_unit(index_price=index_price, strike=strike, mark_price=mark_price)
    return max(inner, mm)


def linear_usdc_short_call_initial_per_contract_usdc(
    *,
    index_price: Decimal,
    strike: Decimal,
    mark_usdc: Decimal,
    contract_size: Decimal,
) -> Decimal:
    mb = linear_usdc_put_premium_as_btc_fraction(mark_usdc=mark_usdc, index_price=index_price, contract_size=contract_size)
    im_btc = short_call_initial_unit(index_price=index_price, strike=strike, mark_price=mb)
    return im_btc * index_price * contract_size


def linear_usdc_short_call_mm_per_contract_usdc(
    *,
    index_price: Decimal,
    strike: Decimal,
    mark_usdc: Decimal,
    contract_size: Decimal,
) -> Decimal:
    mb = linear_usdc_put_premium_as_btc_fraction(mark_usdc=mark_usdc, index_price=index_price, contract_size=contract_size)
    mm_btc = short_call_maintenance_unit(index_price=index_price, strike=strike, mark_price=mb)
    return mm_btc * index_price * contract_size
