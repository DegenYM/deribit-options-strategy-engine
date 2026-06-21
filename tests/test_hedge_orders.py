from __future__ import annotations

from decimal import Decimal

import pytest

from deribit_engine.hedge_orders import (
    normalize_hedge_order_type,
    place_hedge_perp_order,
    resolve_hedge_perp_ioc_limit_price,
)
from deribit_engine.models import OptionInstrument, OrderBookSnapshot


def _perp_instrument() -> OptionInstrument:
    return OptionInstrument(
        instrument_name="BTC_USDC-PERPETUAL",
        base_currency="BTC",
        quote_currency="USDC",
        settlement_currency="USDC",
        strike=Decimal("0"),
        expiration_timestamp_ms=0,
        option_type="",
        instrument_type="linear",
        instrument_state="open",
        contract_size=Decimal("0.0001"),
        min_trade_amount=Decimal("0.0001"),
        tick_size=Decimal("0.5"),
        tick_size_steps=(),
    )


def _book(**kwargs) -> OrderBookSnapshot:
    defaults = dict(
        instrument_name="BTC_USDC-PERPETUAL",
        best_bid_price=Decimal("70000"),
        best_bid_amount=Decimal("1"),
        best_ask_price=Decimal("70001"),
        best_ask_amount=Decimal("1"),
        mark_price=Decimal("70000.5"),
        index_price=Decimal("70000.5"),
        delta=Decimal("0"),
        iv=Decimal("0"),
        open_interest=Decimal("0"),
    )
    defaults.update(kwargs)
    return OrderBookSnapshot(**defaults)


def test_resolve_hedge_ioc_buy_uses_best_ask():
    book = _book()
    price, reason = resolve_hedge_perp_ioc_limit_price(
        direction="buy",
        book=book,
        instrument=_perp_instrument(),
        max_slippage_pct=Decimal("0.01"),
    )
    assert reason is None
    assert price == Decimal("70001")


def test_resolve_hedge_ioc_sell_skips_when_bid_below_slippage_floor():
    book = _book(best_bid_price=Decimal("69000"), mark_price=Decimal("70000"), index_price=Decimal("70000"))
    price, reason = resolve_hedge_perp_ioc_limit_price(
        direction="sell",
        book=book,
        instrument=_perp_instrument(),
        max_slippage_pct=Decimal("0.005"),
    )
    assert price is None
    assert reason == "hedge_slippage_exceeded"


def test_place_hedge_perp_order_limit_ioc():
    captured: dict = {}

    class FakeClient:
        def place_order(self, **kwargs):
            captured.update(kwargs)
            return {"order": {"order_state": "filled"}}

    book = _book()
    place_hedge_perp_order(
        FakeClient(),
        hedge_order_type="limit_ioc",
        hedge_limit_slippage_pct=Decimal("0.01"),
        book=book,
        instrument=_perp_instrument(),
        direction="buy",
        instrument_name="BTC_USDC-PERPETUAL",
        amount=Decimal("0.01"),
        label="trial-hedge-btc-position",
        reduce_only=False,
    )
    assert captured["order_type"] == "limit"
    assert captured["time_in_force"] == "immediate_or_cancel"
    assert captured["price"] == Decimal("70001")


def test_normalize_hedge_order_type_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_hedge_order_type("gtc")
