from __future__ import annotations

from decimal import Decimal


def premium_value_native(
    *,
    premium: Decimal,
    quantity: Decimal,
) -> Decimal:
    return premium * quantity


def premium_value_usdc(
    *,
    index_price: Decimal,
    premium: Decimal,
    quantity: Decimal,
    base_currency: str,
    quote_currency: str,
    settlement_currency: str,
) -> Decimal:
    gross = premium * quantity
    if gross == 0:
        return Decimal("0")
    if quote_currency == "USDC" or settlement_currency == "USDC":
        return gross
    if settlement_currency == base_currency or quote_currency in {"", base_currency}:
        return gross * index_price
    return gross


def option_trade_fee_native(
    *,
    index_price: Decimal,
    premium: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    quote_currency: str,
    settlement_currency: str,
) -> Decimal:
    if quote_currency == "USDC" or settlement_currency == "USDC":
        notional_fee = index_price * quantity * fee_rate
        premium_cap = premium * quantity * fee_cap_rate
        return min(notional_fee, premium_cap)
    native_notional_fee = quantity * fee_rate
    native_premium_cap = premium * quantity * fee_cap_rate
    return min(native_notional_fee, native_premium_cap)


def option_trade_fee_usdc(
    *,
    index_price: Decimal,
    premium: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    base_currency: str,
    quote_currency: str,
    settlement_currency: str,
) -> Decimal:
    native_fee = option_trade_fee_native(
        index_price=index_price,
        premium=premium,
        quantity=quantity,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
    )
    if quote_currency == "USDC" or settlement_currency == "USDC":
        return native_fee
    return native_fee * index_price


def annualized_return(
    *,
    net_credit: Decimal,
    capital_base: Decimal,
    dte_days: Decimal,
) -> Decimal:
    if net_credit <= 0 or capital_base <= 0 or dte_days <= 0:
        return Decimal("0")
    return (net_credit / capital_base) * (Decimal("365") / dte_days)


def inverse_option_fee_native_per_contract(
    *,
    premium: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
) -> Decimal:
    """Deribit inverse options: min(fee_rate, fee_cap_rate * premium) per contract (coin)."""
    return min(fee_rate, fee_cap_rate * premium)


def net_apr_coin_short_put(
    *,
    premium_per_contract: Decimal,
    fee_per_contract: Decimal,
    dte_days: Decimal,
) -> Decimal:
    """README: ((premium * qty - fee) / qty) * (365 / dte) for coin-native yield."""
    if dte_days <= 0:
        return Decimal("0")
    net_per = premium_per_contract - fee_per_contract
    if net_per <= 0:
        return Decimal("0")
    return net_per * (Decimal("365") / dte_days)


def linear_usdc_short_put_apr_premium_over_strike(
    *,
    premium_per_contract: Decimal,
    strike: Decimal,
    dte_days: Decimal,
) -> Decimal:
    """USDC linear short put: APR = (權利金 / 履約價 / DTE) * 365；權利金、履約價同為報價幣（USDC）。"""
    if strike <= 0 or dte_days <= 0:
        return Decimal("0")
    return (premium_per_contract / strike) * (Decimal("365") / dte_days)


def net_apr_coin_short_call(
    *,
    premium_per_contract: Decimal,
    fee_per_contract: Decimal,
    dte_days: Decimal,
) -> Decimal:
    """Coin-native short call APR: symmetric to short put (premium & fee are in base coin).

    Note: for inverse short calls the capital locked is the notional index value,
    but APR as a yield-per-time metric uses the same premium-minus-fee basis; the
    margin utilization is controlled separately by the margin helpers.
    """
    if dte_days <= 0:
        return Decimal("0")
    net_per = premium_per_contract - fee_per_contract
    if net_per <= 0:
        return Decimal("0")
    return net_per * (Decimal("365") / dte_days)


def linear_usdc_short_call_apr_premium_over_index(
    *,
    premium_per_contract: Decimal,
    index_price: Decimal,
    dte_days: Decimal,
) -> Decimal:
    """USDC linear short call APR:

    For calls the unbounded upside means we anchor APR on the current index
    (underlying notional) rather than the strike. Formula mirrors the put form:
    ``APR = (premium / index / DTE) * 365``.
    """
    if index_price <= 0 or dte_days <= 0:
        return Decimal("0")
    return (premium_per_contract / index_price) * (Decimal("365") / dte_days)
