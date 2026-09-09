"""Market-fallback allowlist for rejected reduce_only limit closes (execution.py)."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.engine.execution import is_market_fallback_eligible
from deribit_engine.exceptions import ExchangeError, TransientExchangeError
from deribit_engine.models import OptionInstrument, Position

SHORT = "BTC_USDC-14APR30-63000-P"


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


class _RejectingClient(FakeClient):
    """Raises ``error`` for every reduce_only *limit* order; records market orders normally."""

    def __init__(self, error: Exception):
        super().__init__()
        self.error = error
        self.limit_attempts = 0

    def place_buy_order(self, **kwargs):
        if kwargs.get("order_type") == "limit":
            self.limit_attempts += 1
            raise self.error
        return super().place_buy_order(**kwargs)


def _engine(tmp_path, client):
    client.positions = [_short_option_position(instrument=SHORT, size="0.2")]
    return DeribitOptionTrialBot(make_config(tmp_path, option_markets_profile="linear_usdc"), client)


# ------------------------------------------------------------------
# is_market_fallback_eligible
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        'private/buy failed: HTTP 400 {"message":"price_too_high 2180.0"}',
        'private/sell failed: HTTP 400 {"error":{"code":10005,"message":"price_too_low 0.0005"}}',
        "private/buy failed: code=10007 message=price_too_high data={'limit': 2180.0}",
        "private/buy failed: code=10023 message=invalid_price data=None",
        'private/buy failed: HTTP 400 {"error":{"code":11054,"message":"post_only_reject"}}',
        "private/buy failed: code=10043 message=price_wrong_tick data=None",
        "private/buy failed: code=10011 message=price_not_allowed data=None",
    ],
)
def test_is_market_fallback_eligible_true_for_price_and_post_only_classes(message):
    assert is_market_fallback_eligible(ExchangeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "private/buy failed: code=10009 message=not_enough_funds data=None",
        'private/buy failed: HTTP 400 {"error":{"code":11044,"message":"not_enough_funds"}}',
        "private/buy failed: code=10021 message=invalid_amount data=None",
        "private/buy failed: code=11029 message=invalid_arguments data={'reason': 'reduce_only'}",
        "private/buy failed: code=11051 message=system_maintenance data=None",
        "private/buy failed: code=11042 message=permission_denied data=None",
        "private/buy failed: HTTP 400",
        "",
    ],
)
def test_is_market_fallback_eligible_false_for_everything_else(message):
    assert not is_market_fallback_eligible(ExchangeError(message))


# ------------------------------------------------------------------
# _submit_option_close_limit
# ------------------------------------------------------------------


def test_price_band_error_still_clamps_then_falls_back_to_market(tmp_path):
    """Regression: price_too_high whose clamp equals the requested price → market."""
    err = ExchangeError('private/buy failed: HTTP 400 {"message":"price_too_high 2200.0"}')
    client = _RejectingClient(err)
    engine = _engine(tmp_path, client)
    instrument = OptionInstrument.from_api(client.get_instrument(SHORT))

    response = engine._submit_option_close_limit(
        client.place_buy_order,
        instrument=instrument,
        instrument_name=SHORT,
        amount=Decimal("0.05"),
        label="test-close",
        price=Decimal("2200"),
        direction="buy",
    )

    # clamp == price → straight to market fallback (one limit attempt, one market order).
    assert client.limit_attempts == 1
    assert len(client.placed_orders) == 1
    order = client.placed_orders[0]
    assert order["order_type"] == "market"
    assert order["reduce_only"] is True
    assert Decimal(str(order["amount"])) == Decimal("0.05")
    assert response.get("order") is not None


def test_price_band_error_with_new_limit_reprices_once(tmp_path):
    class PriceTooHighOnce(FakeClient):
        def __init__(self):
            super().__init__()
            self.prices: list[Decimal] = []

        def place_buy_order(self, **kwargs):
            self.prices.append(Decimal(str(kwargs.get("price"))))
            if len(self.prices) == 1:
                raise ExchangeError("private/buy failed: code=10007 message=price_too_high 2180.0 data=None")
            return super().place_buy_order(**kwargs)

    client = PriceTooHighOnce()
    engine = _engine(tmp_path, client)
    instrument = OptionInstrument.from_api(client.get_instrument(SHORT))

    engine._submit_option_close_limit(
        client.place_buy_order,
        instrument=instrument,
        instrument_name=SHORT,
        amount=Decimal("0.05"),
        label="test-close",
        price=Decimal("2200"),
        direction="buy",
    )

    assert client.prices == [Decimal("2200"), Decimal("2180")]
    assert all(o["order_type"] == "limit" for o in client.placed_orders)


def test_insufficient_funds_error_does_not_place_market_order(tmp_path, caplog):
    err = ExchangeError("private/buy failed: code=10009 message=not_enough_funds data=None")
    client = _RejectingClient(err)
    engine = _engine(tmp_path, client)
    instrument = OptionInstrument.from_api(client.get_instrument(SHORT))

    with caplog.at_level(logging.WARNING, logger="deribit_engine"):
        response = engine._submit_option_close_limit(
            client.place_buy_order,
            instrument=instrument,
            instrument_name=SHORT,
            amount=Decimal("0.05"),
            label="test-close",
            price=Decimal("2200"),
            direction="buy",
        )

    assert client.limit_attempts == 1
    assert client.placed_orders == [], "no market fallback for a non-price rejection"
    assert client.closed_positions == []
    assert response["rejected"] is True
    assert "not_enough_funds" in response["error"]
    assert engine._response_filled_amount(response) == Decimal("0")
    assert engine._response_order(response).get("order_state") == "rejected"
    assert any(
        "not eligible for market fallback" in rec.getMessage() and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_generic_http_400_without_reason_does_not_place_market_order(tmp_path):
    client = _RejectingClient(ExchangeError("private/buy failed: HTTP 400"))
    engine = _engine(tmp_path, client)
    instrument = OptionInstrument.from_api(client.get_instrument(SHORT))

    response = engine._submit_option_close_limit(
        client.place_buy_order,
        instrument=instrument,
        instrument_name=SHORT,
        amount=Decimal("0.05"),
        label="test-close",
        price=Decimal("2200"),
        direction="buy",
    )

    assert client.placed_orders == []
    assert response["rejected"] is True


def test_transient_error_still_propagates(tmp_path):
    """TransientExchangeError is not ExchangeError; the caller's reconcile path must see it."""
    client = _RejectingClient(TransientExchangeError("private/buy timed out; reconcile required"))
    engine = _engine(tmp_path, client)
    instrument = OptionInstrument.from_api(client.get_instrument(SHORT))

    with pytest.raises(TransientExchangeError):
        engine._submit_option_close_limit(
            client.place_buy_order,
            instrument=instrument,
            instrument_name=SHORT,
            amount=Decimal("0.05"),
            label="test-close",
            price=Decimal("2200"),
            direction="buy",
        )
    assert client.placed_orders == []


# ------------------------------------------------------------------
# _close_leg_with_retry contract: non-eligible rejection → leg reported unfilled
# ------------------------------------------------------------------


def test_close_leg_with_retry_reports_unfilled_on_non_eligible_rejection(tmp_path):
    err = ExchangeError("private/buy failed: code=10009 message=not_enough_funds data=None")
    client = _RejectingClient(err)
    engine = _engine(tmp_path, client)
    ctx = engine._load_runtime()
    ctx.option_positions = [Position.from_api(_short_option_position(instrument=SHORT, size="0.2"))]

    result = engine._close_leg_with_retry(
        ctx,
        instrument_name=SHORT,
        quantity=Decimal("0.1"),
        direction="buy",
        label="test-close",
        initial_price=Decimal("2200"),
        reason="hard_stop",
    )

    # Initial attempt + one repriced retry, both rejected; nothing went to market.
    assert client.limit_attempts == 2
    assert client.placed_orders == []
    assert result["filled"] == Decimal("0")
    assert result["unfilled"] == Decimal("0.1")
    assert result.get("resting_pending") is None
    assert all(r.get("rejected") for r in result["responses"])
