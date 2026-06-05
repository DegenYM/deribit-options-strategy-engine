from __future__ import annotations

from decimal import Decimal

from deribit_engine.fee_discount import (
    ANCHOR_FIRST_TRADE,
    ANCHOR_REGISTRATION,
    add_calendar_months_ms,
    effective_option_fee_discount_rate,
    is_fee_discount_active,
    resolve_discount_anchor_ms,
)


def test_discount_active_within_six_calendar_months_from_first_trade() -> None:
    first_ms = 1_700_000_000_000
    end_ms = add_calendar_months_ms(first_ms, 6)
    assert is_fee_discount_active(
        discount_months=6,
        anchor_timestamp_ms=first_ms,
        at_timestamp_ms=first_ms,
    )
    assert is_fee_discount_active(
        discount_months=6,
        anchor_timestamp_ms=first_ms,
        at_timestamp_ms=end_ms - 1,
    )
    assert not is_fee_discount_active(
        discount_months=6,
        anchor_timestamp_ms=first_ms,
        at_timestamp_ms=end_ms,
    )


def test_first_trade_anchor_defaults_to_at_ms_when_unknown() -> None:
    at_ms = 1_800_000_000_000
    anchor = resolve_discount_anchor_ms(
        anchor=ANCHOR_FIRST_TRADE,
        first_trade_timestamp_ms=None,
        registration_timestamp_ms=None,
        at_timestamp_ms=at_ms,
    )
    assert anchor == at_ms
    assert effective_option_fee_discount_rate(
        base_rate=Decimal("0.10"),
        discount_months=6,
        anchor=ANCHOR_FIRST_TRADE,
        first_trade_timestamp_ms=None,
        at_timestamp_ms=at_ms,
    ) == Decimal("0.10")


def test_registration_anchor_requires_explicit_timestamp() -> None:
    at_ms = 1_800_000_000_000
    assert (
        resolve_discount_anchor_ms(
            anchor=ANCHOR_REGISTRATION,
            first_trade_timestamp_ms=1_700_000_000_000,
            registration_timestamp_ms=None,
            at_timestamp_ms=at_ms,
        )
        is None
    )
    reg_ms = 1_700_000_000_000
    assert effective_option_fee_discount_rate(
        base_rate=Decimal("0.10"),
        discount_months=6,
        first_trade_timestamp_ms=1_600_000_000_000,
        anchor=ANCHOR_REGISTRATION,
        registration_timestamp_ms=reg_ms,
        at_timestamp_ms=reg_ms + 1,
    ) == Decimal("0.10")
