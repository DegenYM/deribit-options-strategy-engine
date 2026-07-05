from decimal import Decimal

from conftest import make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.exceptions import ExchangeError
from deribit_engine.models import OptionInstrument, Position
from tests.conftest import FakeClient


def _short_option_position(*, instrument: str, size: str) -> dict:
    return {
        "instrument_name": instrument,
        "direction": "sell",
        "kind": "option",
        "size": size,
        "size_currency": size,
        "mark_price": "610",
        "average_price": "600",
        "floating_profit_loss": "0",
        "delta": "-0.11",
    }


def test_fallback_close_position_market_orders_group_quantity_not_full_position(tmp_path, fake_client):
    short = "BTC_USDC-14APR30-63000-P"
    group_qty = Decimal("0.1")
    fake_client.positions = [_short_option_position(instrument=short, size="0.2")]

    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    instrument = OptionInstrument.from_api(fake_client.get_instrument(short))

    engine._fallback_close_position_market(
        instrument=instrument,
        instrument_name=short,
        quantity=group_qty,
        direction="buy",
        label="test-fallback",
        original_error=ExchangeError("limit rejected"),
    )

    assert fake_client.closed_positions == []
    assert len(fake_client.placed_orders) == 1
    order = fake_client.placed_orders[0]
    assert order["instrument_name"] == short
    assert order["direction"] == "buy"
    assert Decimal(str(order["amount"])) == group_qty
    assert order["order_type"] == "market"
    assert order["reduce_only"] is True


def test_submit_option_close_limit_fallback_uses_reduce_only_market_quantity(tmp_path):
    short = "BTC_USDC-14APR30-63000-P"
    amount = Decimal("0.05")

    class LimitRejectClient(FakeClient):
        def place_buy_order(self, **kwargs):
            if kwargs.get("order_type") == "limit":
                raise ExchangeError("private/buy failed: HTTP 400")
            return super().place_buy_order(**kwargs)

    client = LimitRejectClient()
    client.positions = [_short_option_position(instrument=short, size="0.2")]
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, client)
    instrument = OptionInstrument.from_api(client.get_instrument(short))

    response = engine._submit_option_close_limit(
        client.place_buy_order,
        instrument=instrument,
        instrument_name=short,
        amount=amount,
        label="test-close",
        price=Decimal("2200"),
        direction="buy",
    )

    assert client.closed_positions == []
    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert Decimal(str(order["amount"])) == amount
    assert order["order_type"] == "market"
    assert order["reduce_only"] is True
    assert response.get("order") is not None


def test_close_leg_income_exit_escalation_orders_remaining_quantity(tmp_path):
    short = "BTC_USDC-14APR30-63000-P"
    group_qty = Decimal("0.1")

    class LimitRejectClient(FakeClient):
        def place_buy_order(self, **kwargs):
            if kwargs.get("order_type") == "limit":
                raise ExchangeError("private/buy failed: HTTP 400")
            return super().place_buy_order(**kwargs)

    client = LimitRejectClient()
    client.positions = [_short_option_position(instrument=short, size="0.2")]
    config = make_config(
        tmp_path,
        option_markets_profile="linear_usdc",
        income_exit_market_after_attempts=1,
    )
    engine = DeribitOptionTrialBot(config, client)
    ctx = engine._load_runtime()
    ctx.option_positions = [Position.from_api(_short_option_position(instrument=short, size="0.2"))]

    result = engine._close_leg_with_retry(
        ctx,
        instrument_name=short,
        quantity=group_qty,
        direction="buy",
        label="income-exit-test",
        initial_price=Decimal("2200"),
        reason="take_profit",
        incomplete_streak=1,
    )

    assert client.closed_positions == []
    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert Decimal(str(order["amount"])) == group_qty
    assert order["order_type"] == "market"
    assert order["reduce_only"] is True
    assert result["filled"] == group_qty
