"""Per-position APR helpers (screening, open, close).

Persisted / displayed entry APR (actual ledger at open)::

    APR = (actual entry credit − actual entry fee) / actual open size / DTE × 365

Candidate screening (``entry_net_apr_from_fill``) still uses per-contract bid minus
*estimated* round-trip fees for ``MIN_NET_APR`` gates.

Close (position total; ``realized_pnl`` is already net of fees paid)::

    APR = realized_premium / opened_notional / holding_days × 365

``opened_notional`` (position collateral anchor):

- Covered call: ``covered_underlying_quantity``
- USDC linear put: ``strike × quantity``; USDC call: ``index × quantity``
- Inverse naked short: ``contract_size × quantity``
- Bull put spread: ``estimated_im_collateral``
"""

from __future__ import annotations

from decimal import Decimal

from .fees import (
    annualized_return,
    net_apr_inverse_short_per_contract,
    net_apr_linear_usdc_short_call_per_contract,
    net_apr_linear_usdc_short_put_per_contract,
)

ONE = Decimal("1")


def entry_dte_days_at_open(*, entry_timestamp_ms: int, expiration_timestamp_ms: int) -> Decimal:
    if entry_timestamp_ms <= 0 or expiration_timestamp_ms <= entry_timestamp_ms:
        return Decimal("0")
    return Decimal(str(expiration_timestamp_ms - entry_timestamp_ms)) / Decimal("86400000")


def holding_days(*, entry_timestamp_ms: int, closed_timestamp_ms: int) -> Decimal:
    if closed_timestamp_ms <= entry_timestamp_ms:
        return Decimal("0")
    return Decimal(str(closed_timestamp_ms - entry_timestamp_ms)) / Decimal("86400000")


def opened_contract_amount_per_contract(
    *,
    strategy: str,
    collateral_currency: str,
    option_type: str,
    quantity: Decimal,
    contract_size: Decimal,
    strike: Decimal,
    index_price_usd: Decimal,
    estimated_im_collateral: Decimal,
    covered_underlying_quantity: Decimal,
) -> Decimal:
    """Notional denominator for one contract (premium is per-contract)."""
    qty = quantity if quantity > 0 else ONE
    book = (collateral_currency or "").upper()
    strat = (strategy or "").strip()
    cs = contract_size if contract_size > 0 else ONE

    if strat == "bull_put_spread" and estimated_im_collateral > 0:
        return estimated_im_collateral / qty

    if book == "USDC":
        if (option_type or "put").lower() == "call":
            if index_price_usd <= 0:
                return Decimal("0")
            return index_price_usd
        if strike <= 0:
            return Decimal("0")
        return strike

    return cs


def opened_notional_for_position(
    *,
    strategy: str,
    collateral_currency: str,
    option_type: str,
    quantity: Decimal,
    contract_size: Decimal,
    strike: Decimal,
    index_price_usd: Decimal,
    estimated_im_collateral: Decimal,
    covered_underlying_quantity: Decimal,
) -> Decimal:
    """Total opened notional when ``realized_premium`` is position-aggregate PnL."""
    per = opened_contract_amount_per_contract(
        strategy=strategy,
        collateral_currency=collateral_currency,
        option_type=option_type,
        quantity=quantity,
        contract_size=contract_size,
        strike=strike,
        index_price_usd=index_price_usd,
        estimated_im_collateral=estimated_im_collateral,
        covered_underlying_quantity=covered_underlying_quantity,
    )
    qty = quantity if quantity > 0 else ONE
    book = (collateral_currency or "").upper()
    strat = (strategy or "").strip()
    if strat == "covered_call":
        cover = covered_underlying_quantity if covered_underlying_quantity > 0 else qty
        return cover
    if book == "USDC":
        return per * qty
    if strat == "bull_put_spread":
        return per * qty if per > 0 else estimated_im_collateral
    return per * qty


def position_apr_capital_base(
    *,
    strategy: str,
    collateral_currency: str,
    option_type: str,
    quantity: Decimal,
    contract_size: Decimal,
    strike: Decimal,
    index_price_usd: Decimal,
    estimated_im_collateral: Decimal,
    covered_underlying_quantity: Decimal,
) -> Decimal:
    """Backward-compatible alias: position notional for close APR."""
    return opened_notional_for_position(
        strategy=strategy,
        collateral_currency=collateral_currency,
        option_type=option_type,
        quantity=quantity,
        contract_size=contract_size,
        strike=strike,
        index_price_usd=index_price_usd,
        estimated_im_collateral=estimated_im_collateral,
        covered_underlying_quantity=covered_underlying_quantity,
    )


def entry_net_apr_from_actual_open(
    *,
    strategy: str,
    collateral_currency: str,
    option_type: str,
    quantity: Decimal,
    contract_size: Decimal,
    strike: Decimal,
    index_price_usd: Decimal,
    estimated_im_collateral: Decimal,
    covered_underlying_quantity: Decimal,
    net_credit_collateral: Decimal,
    entry_timestamp_ms: int,
    expiration_timestamp_ms: int,
) -> Decimal:
    """Entry APR from actual open ledger: net credit / open size, annualized by entry DTE."""

    dte = entry_dte_days_at_open(
        entry_timestamp_ms=entry_timestamp_ms,
        expiration_timestamp_ms=expiration_timestamp_ms,
    )
    if dte <= 0 or net_credit_collateral <= 0:
        return Decimal("0")

    opened = opened_notional_for_position(
        strategy=strategy,
        collateral_currency=collateral_currency,
        option_type=option_type,
        quantity=quantity,
        contract_size=contract_size,
        strike=strike,
        index_price_usd=index_price_usd,
        estimated_im_collateral=estimated_im_collateral,
        covered_underlying_quantity=covered_underlying_quantity,
    )
    if opened <= 0:
        return Decimal("0")
    return annualized_return(
        net_credit=net_credit_collateral,
        capital_base=opened,
        dte_days=dte,
    )


def entry_net_apr_from_fill(
    *,
    collateral_currency: str,
    option_type: str,
    strategy: str,
    premium_per_contract: Decimal,
    strike: Decimal,
    index_price_usd: Decimal,
    contract_size: Decimal,
    entry_timestamp_ms: int,
    expiration_timestamp_ms: int,
    fee_rate: Decimal = Decimal("0"),
    fee_cap_rate: Decimal = Decimal("0"),
    estimated_im_collateral: Decimal = Decimal("0"),
    covered_underlying_quantity: Decimal = Decimal("0"),
    net_credit_collateral: Decimal | None = None,
    quantity: Decimal = ONE,
) -> Decimal:
    """Screening APR: per-contract bid minus *estimated* round-trip fees (not ledger fills)."""

    dte = entry_dte_days_at_open(
        entry_timestamp_ms=entry_timestamp_ms,
        expiration_timestamp_ms=expiration_timestamp_ms,
    )
    if dte <= 0:
        return Decimal("0")

    if strategy == "bull_put_spread" and net_credit_collateral is not None and estimated_im_collateral > 0:
        opened = opened_notional_for_position(
            strategy=strategy,
            collateral_currency=collateral_currency,
            option_type=option_type,
            quantity=quantity,
            contract_size=contract_size,
            strike=strike,
            index_price_usd=index_price_usd,
            estimated_im_collateral=estimated_im_collateral,
            covered_underlying_quantity=covered_underlying_quantity,
        )
        return annualized_return(
            net_credit=net_credit_collateral,
            capital_base=opened,
            dte_days=dte,
        )

    if premium_per_contract <= 0:
        return Decimal("0")

    opened = opened_contract_amount_per_contract(
        strategy=strategy,
        collateral_currency=collateral_currency,
        option_type=option_type,
        quantity=quantity,
        contract_size=contract_size,
        strike=strike,
        index_price_usd=index_price_usd,
        estimated_im_collateral=estimated_im_collateral,
        covered_underlying_quantity=covered_underlying_quantity,
    )
    if opened <= 0:
        return Decimal("0")

    book = (collateral_currency or "").upper()
    opt = (option_type or "put").lower()
    if book == "USDC" and opt == "put":
        return net_apr_linear_usdc_short_put_per_contract(
            premium_per_contract=premium_per_contract,
            strike=opened,
            dte_days=dte,
            index_price=index_price_usd,
            fee_rate=fee_rate,
            fee_cap_rate=fee_cap_rate,
        )
    if book == "USDC" and opt == "call":
        return net_apr_linear_usdc_short_call_per_contract(
            premium_per_contract=premium_per_contract,
            index_price=opened,
            dte_days=dte,
            fee_rate=fee_rate,
            fee_cap_rate=fee_cap_rate,
        )
    return net_apr_inverse_short_per_contract(
        premium_per_contract=premium_per_contract,
        contract_size=opened,
        dte_days=dte,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap_rate,
    )


def remaining_apr_for_group(
    *,
    remaining_credit: Decimal,
    capital_base: Decimal,
    dte_days: Decimal,
) -> Decimal:
    """Annualized yield of holding to expiry given remaining close cost."""
    return annualized_return(
        net_credit=remaining_credit,
        capital_base=capital_base,
        dte_days=dte_days,
    )


def realized_apr_from_close(
    *,
    strategy: str,
    collateral_currency: str,
    option_type: str,
    quantity: Decimal,
    contract_size: Decimal,
    strike: Decimal,
    index_price_usd: Decimal,
    estimated_im_collateral: Decimal,
    covered_underlying_quantity: Decimal,
    pnl_collateral_native: Decimal,
    entry_timestamp_ms: int,
    closed_timestamp_ms: int,
) -> Decimal:
    """Close APR on net realized PnL (fees already in ``pnl_collateral_native``)."""
    opened = opened_notional_for_position(
        strategy=strategy,
        collateral_currency=collateral_currency,
        option_type=option_type,
        quantity=quantity,
        contract_size=contract_size,
        strike=strike,
        index_price_usd=index_price_usd,
        estimated_im_collateral=estimated_im_collateral,
        covered_underlying_quantity=covered_underlying_quantity,
    )
    days = holding_days(entry_timestamp_ms=entry_timestamp_ms, closed_timestamp_ms=closed_timestamp_ms)
    return annualized_return(net_credit=pnl_collateral_native, capital_base=opened, dte_days=days)
