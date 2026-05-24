from decimal import Decimal

from deribit_demo.models import TradeGroup
from deribit_demo.models import TradeGroup
from deribit_demo.trade_apr import (
    entry_net_apr_from_actual_open,
    entry_net_apr_from_fill,
    opened_contract_amount_per_contract,
    opened_notional_for_position,
    realized_apr_from_close,
)


def test_covered_call_opened_notional_is_cover_qty():
    opened = opened_notional_for_position(
        strategy="covered_call",
        collateral_currency="BTC",
        option_type="call",
        quantity=Decimal("0.1"),
        contract_size=Decimal("1"),
        strike=Decimal("85000"),
        index_price_usd=Decimal("79647"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0.1"),
    )
    assert opened == Decimal("0.1")


def test_inverse_naked_opened_notional_scales_with_qty():
    opened = opened_notional_for_position(
        strategy="naked_short",
        collateral_currency="BTC",
        option_type="put",
        quantity=Decimal("0.1"),
        contract_size=Decimal("1"),
        strike=Decimal("85000"),
        index_price_usd=Decimal("79647"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0"),
    )
    assert opened == Decimal("0.1")


def test_covered_call_close_notional_uses_open_size_not_qty_squared():
    close_apr = realized_apr_from_close(
        strategy="covered_call",
        collateral_currency="BTC",
        option_type="call",
        quantity=Decimal("0.1"),
        contract_size=Decimal("1"),
        strike=Decimal("85000"),
        index_price_usd=Decimal("79647"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0.1"),
        pnl_collateral_native=Decimal("0.0006"),
        entry_timestamp_ms=1_000_000,
        closed_timestamp_ms=1_000_000 + int(Decimal("3.0007") * 86_400_000),
    )
    expected = (Decimal("0.0006") / Decimal("0.1") / Decimal("3.0007")) * Decimal("365")
    assert abs(close_apr - expected) < Decimal("0.000001")


def test_entry_apr_from_actual_open_fractional_covered_call():
    """Actual net credit / cover qty — not per-contract bid minus estimated round-trip fee."""
    entry_ms = 1_000_000
    dte = Decimal("20")
    idx = Decimal("79647")
    gross_usdc = Decimal("81.24")
    entry_fee = Decimal("2.39")
    net_usdc = gross_usdc - entry_fee
    net_native = net_usdc / idx

    group = TradeGroup(
        group_id="0099",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.1"),
        covered_underlying_quantity=Decimal("0.1"),
        entry_timestamp_ms=entry_ms,
        expiration_timestamp_ms=entry_ms + int(dte * 86_400_000),
        short_instrument_name="BTC-5JUN26-85000-C",
        short_strike=Decimal("85000"),
        entry_credit=gross_usdc,
        original_entry_credit=gross_usdc,
        max_loss=Decimal("1000"),
        regime_at_entry="normal",
        entry_fee=entry_fee,
        entry_index_usd=idx,
        short_entry_average_price=Decimal("0.0102"),
        strategy="covered_call",
        option_type="call",
    )
    apr = group.entry_net_apr_at_open(contract_size=Decimal("1"))
    expected = (net_native / Decimal("0.1")) * (Decimal("365") / dte)
    assert abs(apr - expected) < Decimal("0.000001")
    assert apr < Decimal("0.25")
    assert apr > Decimal("0.15")


def test_entry_apr_from_actual_open_usdc_put():
    net_credit = Decimal("33.61")
    apr = entry_net_apr_from_actual_open(
        strategy="naked_short",
        collateral_currency="USDC",
        option_type="put",
        quantity=Decimal("1"),
        contract_size=Decimal("0.1"),
        strike=Decimal("2100"),
        index_price_usd=Decimal("2100"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0"),
        net_credit_collateral=net_credit,
        entry_timestamp_ms=1_000_000,
        expiration_timestamp_ms=1_000_000 + 7 * 86_400_000,
    )
    expected = (net_credit / Decimal("2100")) * (Decimal("365") / Decimal("7"))
    assert abs(apr - expected) < Decimal("0.000001")


def test_screening_fill_apr_still_uses_estimated_round_trip_fees():
    """MIN_NET_APR screening keeps per-contract bid minus estimated exit+entry fees."""
    fee_rate = Decimal("0.0003")
    fee_cap = Decimal("0.125")
    entry_apr = entry_net_apr_from_fill(
        collateral_currency="USDC",
        option_type="put",
        strategy="naked_short",
        premium_per_contract=Decimal("35"),
        strike=Decimal("2100"),
        index_price_usd=Decimal("2100"),
        contract_size=Decimal("0.1"),
        entry_timestamp_ms=1_000_000,
        expiration_timestamp_ms=1_000_000 + 7 * 86_400_000,
        fee_rate=fee_rate,
        fee_cap_rate=fee_cap,
        quantity=Decimal("1"),
    )
    gross = (Decimal("35") / Decimal("2100") / Decimal("7")) * Decimal("365")
    assert entry_apr > Decimal("0")
    assert entry_apr < gross


def test_opened_contract_amount_per_contract_inverse_uses_contract_size():
    base = opened_contract_amount_per_contract(
        strategy="covered_call",
        collateral_currency="ETH",
        option_type="call",
        quantity=Decimal("0.1"),
        contract_size=Decimal("1"),
        strike=Decimal("2300"),
        index_price_usd=Decimal("2100"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0.1"),
    )
    assert base == Decimal("1")


def test_usdc_linear_call_entry_apr_uses_underlying_index_not_usdc_placeholder():
    """entry_index_usd=1 must not make the APR denominator qty instead of index*qty."""
    group = TradeGroup(
        group_id="0003",
        currency="ETH",
        collateral_currency="USDC",
        quantity=Decimal("1.5"),
        entry_timestamp_ms=1_000_000,
        expiration_timestamp_ms=1_000_000 + int(Decimal("11.5") * 86_400_000),
        short_instrument_name="ETH_USDC-22MAY26-2600-C",
        short_strike=Decimal("2600"),
        entry_credit=Decimal("15.751311"),
        original_entry_credit=Decimal("15.751311"),
        max_loss=Decimal("368.4735"),
        regime_at_entry="normal",
        entry_fee=Decimal("1.048689"),
        entry_index_usd=Decimal("1"),
        short_entry_average_price=Decimal("11.2"),
        strategy="naked_short",
        option_type="call",
    )
    apr = group.entry_net_apr_at_open(contract_size=Decimal("0.1"))
    expected = (Decimal("15.751311") / (Decimal("2600") * Decimal("1.5"))) * (
        Decimal("365") / Decimal("11.5")
    )
    assert abs(apr - expected) < Decimal("0.000001")
    assert apr < Decimal("0.5")
    assert apr > Decimal("0.05")


def test_realized_apr_negative_on_loss_usdc_linear_uses_strike_notional():
    close_apr = realized_apr_from_close(
        strategy="naked_short",
        collateral_currency="USDC",
        option_type="put",
        quantity=Decimal("1"),
        contract_size=Decimal("0.1"),
        strike=Decimal("3000"),
        index_price_usd=Decimal("2900"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0"),
        pnl_collateral_native=Decimal("-15"),
        entry_timestamp_ms=1_000_000,
        closed_timestamp_ms=1_000_000 + 5 * 86_400_000,
    )
    opened = opened_notional_for_position(
        strategy="naked_short",
        collateral_currency="USDC",
        option_type="put",
        quantity=Decimal("1"),
        contract_size=Decimal("0.1"),
        strike=Decimal("3000"),
        index_price_usd=Decimal("2900"),
        estimated_im_collateral=Decimal("0"),
        covered_underlying_quantity=Decimal("0"),
    )
    assert opened == Decimal("3000")
    expected = (Decimal("-15") / Decimal("3000") / Decimal("5")) * Decimal("365")
    assert abs(close_apr - expected) < Decimal("0.000001")
    assert close_apr < 0
