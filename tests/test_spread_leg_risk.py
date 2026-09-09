"""Sequential spread legs: when the second leg fails, the operator must be told."""

from __future__ import annotations

import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import FakeClient, make_config
from test_engine import _build_group

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.exceptions import TransientExchangeError
from deribit_engine.models import Position

SHORT = "BTC_USDC-14APR30-63000-P"
LONG = "BTC_USDC-14APR30-60000-P"


def _position(instrument: str, *, direction: str, size: str) -> dict:
    return {
        "instrument_name": instrument,
        "direction": direction,
        "kind": "option",
        "size": size,
        "size_currency": size,
        "mark_price": "610",
        "average_price": "600",
        "floating_profit_loss": "0",
        "delta": "-0.11",
    }


@pytest.fixture
def alerts(monkeypatch):
    """Capture ``_telegram_alert`` calls on any engine instance."""
    calls: list[dict] = []

    def _capture(self, title, *, body="", event_key, level="warning", extra=None):
        calls.append({"title": title, "body": body, "event_key": event_key, "level": level, "extra": extra})

    monkeypatch.setattr(DeribitOptionTrialBot, "_telegram_alert", _capture)
    return calls


def _spread_engine(tmp_path, client):
    client.positions = [
        _position(SHORT, direction="sell", size="0.1"),
        _position(LONG, direction="buy", size="0.1"),
    ]
    engine = DeribitOptionTrialBot(make_config(tmp_path, option_markets_profile="linear_usdc"), client)
    ctx = engine._load_runtime()
    ctx.option_positions = [Position.from_api(p) for p in client.positions]
    group = _build_group(short_instrument_name=SHORT, quantity=Decimal("0.1"))
    group.long_instrument_name = LONG
    group.long_strike = Decimal("60000")
    group.strategy = "bull_put_spread"
    return engine, ctx, group


# ------------------------------------------------------------------
# Close: short bought back, long sell does not fill
# ------------------------------------------------------------------


def test_close_group_marks_leg_risk_when_long_close_incomplete(tmp_path, alerts, caplog):
    client = FakeClient()
    engine, ctx, group = _spread_engine(tmp_path, client)
    # Short close fills normally (default script); long close never fills.
    client.order_scripts_by_label["trial-spread-btc-0001-long-close"] = [
        {"filled_amount": "0", "order_state": "cancelled"},
        {"filled_amount": "0", "order_state": "cancelled"},
    ]

    with caplog.at_level(logging.WARNING):
        actions = engine._close_group(ctx, group, reason="hard_stop", live=True)

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "close_group_incomplete"
    assert action["leg_risk"] == "close_incomplete_long"
    assert action["leg_risk_quantity"] == Decimal("0.1")
    assert action["short_filled"] == Decimal("0.1")
    assert action["long_unfilled"] == Decimal("0.1")
    assert group.status == "open", "group must not be marked closed with the long still open"
    assert any("LEG RISK close_incomplete_long" in r.getMessage() for r in caplog.records)
    assert [a["event_key"] for a in alerts] == ["leg_risk:close_incomplete_long:0001"]
    assert alerts[0]["level"] == "critical"


def test_close_group_no_leg_risk_when_both_legs_fill(tmp_path, alerts):
    client = FakeClient()
    engine, ctx, group = _spread_engine(tmp_path, client)

    actions = engine._close_group(ctx, group, reason="hard_stop", live=True)

    assert actions[0]["action"] == "close_group"
    assert "leg_risk" not in actions[0]
    # Only the ordinary close alert, no leg-risk page.
    assert all(not a["event_key"].startswith("leg_risk:") for a in alerts)


def test_close_group_pages_then_reraises_when_long_close_raises(tmp_path, alerts):
    client = FakeClient()
    engine, ctx, group = _spread_engine(tmp_path, client)
    original = client.place_sell_order

    def _boom(**kwargs):
        if kwargs.get("instrument_name") == LONG:
            raise TransientExchangeError("private/sell timed out; reconcile required")
        return original(**kwargs)

    client.place_sell_order = _boom

    with pytest.raises(TransientExchangeError):
        engine._close_group(ctx, group, reason="hard_stop", live=True)

    assert [a["event_key"] for a in alerts] == ["leg_risk:close_incomplete_long:0001"]
    assert "raised after short filled" in alerts[0]["body"]


# ------------------------------------------------------------------
# Entry: long bought, short never fills, unwind incomplete → orphan long
# ------------------------------------------------------------------


def _entry_harness(tmp_path, monkeypatch, client, *, short_filled: str, unwind_filled: str | None):
    engine = DeribitOptionTrialBot(make_config(tmp_path, option_markets_profile="linear_usdc"), client)
    ctx = engine._load_runtime()
    candidate = SimpleNamespace(
        currency="BTC",
        quantity=Decimal("0.1"),
        long_leg=SimpleNamespace(instrument_name=LONG, strike=Decimal("60000")),
        short_leg=SimpleNamespace(instrument_name=SHORT, strike=Decimal("63000")),
        to_dict=lambda: {"short_instrument_name": SHORT, "long_instrument_name": LONG},
    )
    long_state = {
        "order": {"order_id": "long-1", "filled_amount": "0.1", "average_price": "50", "order_state": "filled"}
    }
    monkeypatch.setattr(engine, "_entry_long_buy_request", lambda c, label, quantity: {"label": label})
    monkeypatch.setattr(engine, "_place_entry_order", lambda ctx_, direction, req: long_state)
    monkeypatch.setattr(engine, "_await_entry_order", lambda resp, max_wait_seconds: resp)
    monkeypatch.setattr(
        engine,
        "_execute_repriced_naked_short",
        lambda ctx_, c, *, label, quantity: {
            "candidate": None,
            "filled_amount": Decimal(short_filled),
            "requests": [],
            "responses": [],
            "trades": [],
            "first_request": None,
            "last_response": None,
            "reason": "unfilled",
        },
    )
    unwind_calls: list[dict] = []

    def _unwind(ctx_, *, instrument_name, quantity, label):
        unwind_calls.append({"instrument_name": instrument_name, "quantity": quantity, "label": label})
        if unwind_filled is None:
            raise TransientExchangeError("private/sell connection failed; reconcile required")
        return {"order": {"filled_amount": unwind_filled, "average_price": "48", "order_state": "filled"}}

    monkeypatch.setattr(engine, "_close_entry_long_remainder", _unwind)
    return engine, ctx, candidate, unwind_calls


def test_entry_marks_orphan_long_when_unwind_partially_fills(tmp_path, monkeypatch, alerts, caplog):
    engine, ctx, candidate, unwind_calls = _entry_harness(
        tmp_path, monkeypatch, FakeClient(), short_filled="0", unwind_filled="0.04"
    )

    with caplog.at_level(logging.WARNING):
        result = engine._execute_bull_put_spread_entry(ctx, candidate, "0001")

    assert result["action"] == "entry_aborted_short_unfilled"
    assert result["leg_risk"] == "orphan_long"
    assert result["leg_risk_quantity"] == Decimal("0.06")
    assert unwind_calls == [
        {"instrument_name": LONG, "quantity": Decimal("0.1"), "label": "trial-spread-btc-0001-long-abort"}
    ]
    assert any("LEG RISK orphan_long" in r.getMessage() for r in caplog.records)
    assert [a["event_key"] for a in alerts] == ["leg_risk:orphan_long:0001"]
    assert alerts[0]["level"] == "critical"


def test_entry_marks_orphan_long_when_unwind_raises(tmp_path, monkeypatch, alerts):
    engine, ctx, candidate, _ = _entry_harness(
        tmp_path, monkeypatch, FakeClient(), short_filled="0", unwind_filled=None
    )

    result = engine._execute_bull_put_spread_entry(ctx, candidate, "0001")

    # The failure is reported, not raised, so the orphan is recorded in the action log.
    assert result["action"] == "entry_aborted_short_unfilled"
    assert result["leg_risk"] == "orphan_long"
    assert result["leg_risk_quantity"] == Decimal("0.1")
    assert result["responses"]["long_unwind"]["unwind_failed"] is True
    assert [a["event_key"] for a in alerts] == ["leg_risk:orphan_long:0001"]


def test_entry_no_leg_risk_when_unwind_fully_fills(tmp_path, monkeypatch, alerts):
    engine, ctx, candidate, _ = _entry_harness(
        tmp_path, monkeypatch, FakeClient(), short_filled="0", unwind_filled="0.1"
    )

    result = engine._execute_bull_put_spread_entry(ctx, candidate, "0001")

    assert result["action"] == "entry_aborted_short_unfilled"
    assert "leg_risk" not in result
    assert alerts == []


def test_entry_pages_then_reraises_when_short_placement_raises(tmp_path, monkeypatch, alerts):
    engine, ctx, candidate, _ = _entry_harness(
        tmp_path, monkeypatch, FakeClient(), short_filled="0", unwind_filled="0.1"
    )

    def _boom(ctx_, c, *, label, quantity):
        raise TransientExchangeError("private/sell connection failed; reconcile required")

    monkeypatch.setattr(engine, "_execute_repriced_naked_short", _boom)

    with pytest.raises(TransientExchangeError):
        engine._execute_bull_put_spread_entry(ctx, candidate, "0001")

    assert [a["event_key"] for a in alerts] == ["leg_risk:orphan_long:0001"]
    assert "short leg placement raised" in alerts[0]["body"]
