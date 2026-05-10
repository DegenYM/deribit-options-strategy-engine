from decimal import Decimal

from deribit_demo.models import OptionInstrument
from deribit_demo.stress import (
    StressScenario,
    black_swan_strategy_analysis,
    stress_option_position_pnl_breakdown_usdc,
    stress_short_option_loss_usdc,
)


def _make_inverse_put() -> OptionInstrument:
    return OptionInstrument(
        instrument_name="BTC-01JAN30-50000-P",
        base_currency="BTC",
        quote_currency="BTC",
        settlement_currency="BTC",
        instrument_type="reversed",
        tick_size=Decimal("0.0001"),
        tick_size_steps=(),
        min_trade_amount=Decimal("0.1"),
        contract_size=Decimal("0.1"),
        option_type="put",
        expiration_timestamp_ms=0,
        strike=Decimal("50000"),
        instrument_state="open",
    )


def test_stress_loss_put_more_negative_on_bigger_spot_drop():
    inst = _make_inverse_put()
    qty = Decimal("0.1")
    entry_premium = Decimal("0.002")  # coin
    spot = Decimal("70000")

    s20 = StressScenario(name="s20", spot_shock=Decimal("-0.20"), liquidity_slippage=Decimal("0.10"))
    s60 = StressScenario(name="s60", spot_shock=Decimal("-0.60"), liquidity_slippage=Decimal("0.30"))

    loss20 = stress_short_option_loss_usdc(
        inst,
        option_type="put",
        quantity=qty,
        entry_premium=entry_premium,
        spot=spot,
        scenario=s20,
    )
    loss60 = stress_short_option_loss_usdc(
        inst,
        option_type="put",
        quantity=qty,
        entry_premium=entry_premium,
        spot=spot,
        scenario=s60,
    )

    assert loss60 <= loss20


def test_long_put_stress_offsets_downside():
    inst = _make_inverse_put()
    scenario = StressScenario(name="s40", spot_shock=Decimal("-0.40"), liquidity_slippage=Decimal("0.20"))

    short = stress_option_position_pnl_breakdown_usdc(
        inst,
        option_type="put",
        quantity=Decimal("0.1"),
        current_premium=Decimal("0.002"),
        spot=Decimal("70000"),
        scenario=scenario,
        direction="sell",
    )
    long = stress_option_position_pnl_breakdown_usdc(
        inst,
        option_type="put",
        quantity=Decimal("0.1"),
        current_premium=Decimal("0.002"),
        spot=Decimal("70000"),
        scenario=scenario,
        direction="buy",
    )

    assert Decimal(str(short["total_usdc"])) < 0
    assert Decimal(str(long["total_usdc"])) > 0


def test_black_swan_analysis_normalizes_put_spread_alias():
    analysis = black_swan_strategy_analysis("put_spread")

    assert analysis["label"] == "bull_put_spread"
    assert "long put" in analysis["summary"]

