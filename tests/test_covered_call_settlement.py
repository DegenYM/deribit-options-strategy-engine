from decimal import Decimal

from conftest import FakeClient

from deribit_engine.covered_call_settlement import (
    covered_call_settlement_loss_from_intrinsic,
    covered_call_settlement_loss_from_transaction_log,
    covered_call_spot_exit_skips_settlement_loss,
    resolve_covered_call_settlement_loss,
)
from deribit_engine.models import TradeGroup


def _group(*, strike: str = "100000", quantity: str = "0.1") -> TradeGroup:
    return TradeGroup(
        group_id="cc-1",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal(quantity),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2_000_000,
        short_instrument_name="BTC-30APR26-100000-C",
        short_strike=Decimal(strike),
        entry_credit=Decimal("0.01"),
        original_entry_credit=Decimal("0.01"),
        max_loss=Decimal("1"),
        estimated_im_collateral=Decimal("0.1"),
        regime_at_entry="normal",
        option_type="call",
        strategy="covered_call",
        covered_underlying_quantity=Decimal(quantity),
    )


def test_covered_call_settlement_loss_intrinsic_itm():
    group = _group()
    loss = covered_call_settlement_loss_from_intrinsic(group, index_price_usd=Decimal("110000"))
    assert loss == Decimal("0.1") * Decimal("10000") / Decimal("110000")


def test_covered_call_settlement_loss_intrinsic_otm_is_zero():
    group = _group()
    loss = covered_call_settlement_loss_from_intrinsic(group, index_price_usd=Decimal("90000"))
    assert loss == Decimal("0")


def test_covered_call_settlement_loss_from_transaction_log_sums_outflows():
    client = FakeClient()
    client.transaction_log = {
        "BTC": [
            {
                "timestamp": 2_000_000,
                "type": "settlement",
                "instrument_name": "BTC-30APR26-100000-C",
                "change": "-0.00909",
            },
            {
                "timestamp": 2_000_100,
                "type": "delivery",
                "instrument_name": "BTC-30APR26-100000-C",
                "change": "-0.00001",
            },
        ]
    }
    loss = covered_call_settlement_loss_from_transaction_log(
        client,
        currency="BTC",
        instrument_name="BTC-30APR26-100000-C",
        expiration_timestamp_ms=2_000_000,
    )
    assert loss == Decimal("0.0091")


def test_resolve_prefers_transaction_log_when_available():
    group = _group()
    client = FakeClient()
    client.transaction_log = {
        "BTC": [
            {
                "timestamp": 2_000_000,
                "type": "settlement",
                "instrument_name": group.short_instrument_name,
                "change": "-0.00909",
            }
        ]
    }
    loss, source = resolve_covered_call_settlement_loss(
        group,
        index_price_usd=Decimal("110000"),
        short_instrument=None,
        client=client,
        reason="covered_call_settlement_exit",
        prefer_log=True,
    )
    assert loss == Decimal("0.00909")
    assert source == "transaction_log"


def test_resolve_skips_loss_for_robust_exit():
    group = _group()
    loss, source = resolve_covered_call_settlement_loss(
        group,
        index_price_usd=Decimal("110000"),
        short_instrument=None,
        client=None,
        reason="covered_call_robust_exit",
        prefer_log=False,
    )
    assert loss == Decimal("0")
    assert source == "skipped_robust"


def test_robust_reason_detection():
    assert covered_call_spot_exit_skips_settlement_loss(reason="covered_call_robust_exit") is True
    assert covered_call_spot_exit_skips_settlement_loss(reason="covered_call_settlement_exit") is False
