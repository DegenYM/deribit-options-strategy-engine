from decimal import Decimal

from deribit_engine.bull_put_settlement import repair_bull_put_expiry_reconcile_pnl
from deribit_engine.models import TradeGroup
from deribit_engine.utils import utc_now_ms


def test_repair_bull_put_expiry_reconcile_pnl_caps_naked_style_loss():
    exp_ms = utc_now_ms() - 3_600_000
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="USDC",
        quantity=Decimal("1.8"),
        entry_timestamp_ms=exp_ms - 20 * 86_400_000,
        expiration_timestamp_ms=exp_ms,
        short_instrument_name="ETH_USDC-29MAY26-1900-P",
        short_strike=Decimal("1900"),
        entry_credit=Decimal("10.87"),
        original_entry_credit=Decimal("10.87"),
        max_loss=Decimal("90"),
        regime_at_entry="normal",
    )
    group.strategy = "naked_short"
    group.option_type = "put"
    group.long_instrument_name = "ETH_USDC-29MAY26-1850-P"
    group.long_strike = Decimal("1850")
    group.status = "closed"
    group.close_reason = "reconciled_expiry"
    group.closed_timestamp_ms = exp_ms + 60_000
    group.entry_index_usd = Decimal("1700")
    group.close_index_usd = Decimal("1700")
    group.realized_pnl = Decimal("-98.21")
    group.realized_close_debit = Decimal("109.08")
    assert repair_bull_put_expiry_reconcile_pnl(
        group,
        index_price_usd=Decimal("1700"),
        fee_rate=Decimal("0.0003"),
        fee_cap_rate=Decimal("0.125"),
        markets={},
    )
    assert group.realized_pnl is not None
    assert group.realized_pnl >= -group.max_loss - Decimal("2")
    assert group.realized_pnl > Decimal("-98.21")
    assert group.strategy == "bull_put_spread"
