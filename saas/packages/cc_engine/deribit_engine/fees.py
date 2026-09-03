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


def apply_trading_fee_discount(fee: Decimal, fee_discount_rate: Decimal) -> Decimal:
    """Apply Deribit account trading-fee discount (e.g. 0.10 → pay 90% of schedule)."""
    if fee <= 0 or fee_discount_rate <= 0:
        return fee
    discount = min(max(fee_discount_rate, Decimal("0")), Decimal("1"))
    return fee * (Decimal("1") - discount)


def option_trade_fee_native(
    *,
    index_price: Decimal,
    premium: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    quote_currency: str,
    settlement_currency: str,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    if quote_currency == "USDC" or settlement_currency == "USDC":
        notional_fee = index_price * quantity * fee_rate
        premium_cap = premium * quantity * fee_cap_rate
        fee = min(notional_fee, premium_cap)
    else:
        native_notional_fee = quantity * fee_rate
        native_premium_cap = premium * quantity * fee_cap_rate
        fee = min(native_notional_fee, native_premium_cap)
    return apply_trading_fee_discount(fee, fee_discount_rate)


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
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    native_fee = option_trade_fee_native(
        index_price=index_price,
        premium=premium,
        quantity=quantity,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
        fee_discount_rate=fee_discount_rate,
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
    if net_credit == 0 or capital_base <= 0 or dte_days <= 0:
        return Decimal("0")
    return (net_credit / capital_base) * (Decimal("365") / dte_days)


def inverse_option_fee_native_per_contract(
    *,
    premium: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """Deribit inverse options: min(fee_rate, fee_cap_rate * premium) per contract (coin)."""
    fee = min(fee_rate, fee_cap_rate * premium)
    return apply_trading_fee_discount(fee, fee_discount_rate)


def inverse_option_fee_native_total(
    *,
    premium: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """Total inverse option fee in collateral coin for ``quantity`` contracts."""
    if quantity <= 0 or premium <= 0:
        return Decimal("0")
    per = inverse_option_fee_native_per_contract(
        premium=premium,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        fee_discount_rate=fee_discount_rate,
    )
    return per * quantity


def infer_inverse_short_close_premium_per_contract(
    *,
    total_debit_native: Decimal,
    quantity: Decimal,
    fee_rate: Decimal = Decimal("0.0003"),
    fee_cap_rate: Decimal = Decimal("0.125"),
) -> Decimal:
    """Recover buy-back premium per contract from all-in native debit (premium + fee) × qty."""
    if quantity <= 0 or total_debit_native <= 0:
        return Decimal("0")
    per_contract_debit = total_debit_native / quantity
    px_at_fee_rate = per_contract_debit - fee_rate
    if px_at_fee_rate > 0 and fee_cap_rate * px_at_fee_rate <= fee_rate:
        return px_at_fee_rate
    denom = Decimal("1") + fee_cap_rate
    if denom <= 0:
        return Decimal("0")
    px_at_fee_cap = per_contract_debit / denom
    if px_at_fee_cap > 0 and fee_cap_rate * px_at_fee_cap <= fee_rate:
        return px_at_fee_cap
    return px_at_fee_rate if px_at_fee_rate > 0 else px_at_fee_cap


def inverse_option_round_trip_fee_per_contract(
    *,
    premium: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """Estimated entry + exit fee per inverse contract at the same premium."""
    per_leg = inverse_option_fee_native_per_contract(
        premium=premium,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        fee_discount_rate=fee_discount_rate,
    )
    return per_leg * 2


def linear_usdc_option_round_trip_fee_per_contract(
    *,
    index_price: Decimal,
    premium: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """Estimated entry + exit fee per USDC linear contract at the same premium."""
    per_leg = option_trade_fee_native(
        index_price=index_price,
        premium=premium,
        quantity=Decimal("1"),
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        quote_currency="USDC",
        settlement_currency="USDC",
        fee_discount_rate=fee_discount_rate,
    )
    return per_leg * 2


def net_apr_inverse_short_per_contract(
    *,
    premium_per_contract: Decimal,
    contract_size: Decimal,
    dte_days: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """Coin-native short option APR per contract.

    ``(bid - entry_fee - exit_fee) / contract_size / DTE * 365`` — for inverse
    options ``contract_size`` is typically 1 BTC/ETH of cover/collateral.
    """
    if dte_days <= 0:
        return Decimal("0")
    round_trip_fee = inverse_option_round_trip_fee_per_contract(
        premium=premium_per_contract,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        fee_discount_rate=fee_discount_rate,
    )
    net_per = premium_per_contract - round_trip_fee
    capital = contract_size if contract_size > 0 else Decimal("1")
    return annualized_return(net_credit=net_per, capital_base=capital, dte_days=dte_days)


def net_apr_linear_usdc_short_put_per_contract(
    *,
    premium_per_contract: Decimal,
    strike: Decimal,
    dte_days: Decimal,
    index_price: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """USDC linear short put APR per contract: net premium / strike, annualized."""
    if strike <= 0 or dte_days <= 0:
        return Decimal("0")
    round_trip_fee = linear_usdc_option_round_trip_fee_per_contract(
        index_price=index_price,
        premium=premium_per_contract,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        fee_discount_rate=fee_discount_rate,
    )
    net = premium_per_contract - round_trip_fee
    if net <= 0:
        return Decimal("0")
    return (net / strike) * (Decimal("365") / dte_days)


def net_apr_linear_usdc_short_call_per_contract(
    *,
    premium_per_contract: Decimal,
    index_price: Decimal,
    dte_days: Decimal,
    fee_rate: Decimal,
    fee_cap_rate: Decimal,
    fee_discount_rate: Decimal = Decimal("0"),
) -> Decimal:
    """USDC linear short call APR per contract: net premium / index, annualized."""
    if index_price <= 0 or dte_days <= 0:
        return Decimal("0")
    round_trip_fee = linear_usdc_option_round_trip_fee_per_contract(
        index_price=index_price,
        premium=premium_per_contract,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
        fee_discount_rate=fee_discount_rate,
    )
    net = premium_per_contract - round_trip_fee
    if net <= 0:
        return Decimal("0")
    return (net / index_price) * (Decimal("365") / dte_days)


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
