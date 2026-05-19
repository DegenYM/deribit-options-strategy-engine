from __future__ import annotations

from decimal import Decimal

from deribit_demo.models import TradeGroup


def test_backfill_realized_pnl_usdc_prefers_native_times_index() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.69"),
        entry_index_usd=Decimal("2300"),
        close_index_usd=Decimal("2100"),
        realized_pnl=Decimal("24.23"),
        realized_pnl_collateral_native=Decimal("0.0099"),
    )
    group.backfill_realized_pnl_usdc(spot_index_usd=Decimal("2400"))
    assert group.realized_pnl == Decimal("0.0099") * Decimal("2400")
