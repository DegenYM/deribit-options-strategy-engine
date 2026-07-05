from decimal import Decimal
from unittest.mock import patch

from conftest import make_config

from deribit_engine.engine import DeribitOptionTrialBot
from tests.test_engine import _build_group


def _short_filled_result(*, quantity: Decimal, average_price: Decimal = Decimal("0.01")) -> dict:
    response = {"order": {"order_state": "filled", "filled_amount": str(quantity)}}
    return {
        "responses": [response],
        "last_response": response,
        "average_price": average_price,
        "filled": quantity,
        "unfilled": Decimal("0"),
    }


def _long_partial_result(*, quantity: Decimal, filled: Decimal, average_price: Decimal = Decimal("0.005")) -> dict:
    response = {"order": {"order_state": "filled", "filled_amount": str(filled)}}
    return {
        "responses": [response],
        "last_response": response,
        "average_price": average_price,
        "filled": filled,
        "unfilled": quantity - filled,
    }


def test_close_group_keeps_open_when_short_filled_and_long_unfilled(tmp_path, fake_client):
    config = make_config(tmp_path, option_strategy="bull_put_spread", option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)
    ctx = engine._load_runtime()

    short = "BTC_USDC-14APR30-63000-P"
    long = "BTC_USDC-14APR30-60000-P"
    quantity = Decimal("0.1")
    group = _build_group(short_instrument_name=short, quantity=quantity)
    group.long_instrument_name = long
    group.strategy = "bull_put_spread"
    assert group.status == "open"
    assert group.close_incomplete_streak == 0

    short_result = _short_filled_result(quantity=quantity)
    long_result = _long_partial_result(quantity=quantity, filled=Decimal("0.04"))

    with (
        patch.object(engine, "_close_leg_with_retry", side_effect=[short_result, long_result]) as close_mock,
        patch.object(engine, "_mark_group_closed") as mark_closed_mock,
    ):
        actions = engine._close_group(ctx, group, reason="hard_stop", live=True)

    assert close_mock.call_count == 2
    mark_closed_mock.assert_not_called()
    assert group.status == "open"
    assert group.last_action == "hard_stop_incomplete"
    assert group.close_incomplete_streak == 1
    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "close_group_incomplete"
    assert action["reason"] == "hard_stop"
    assert action["group_id"] == group.group_id
    assert action["short_filled"] == quantity
    assert action["short_unfilled"] == Decimal("0")
    assert action["long_filled"] == Decimal("0.04")
    assert action["long_unfilled"] == Decimal("0.06")
