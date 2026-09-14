"""Skipped wheel puts are visible in live runs — once per reason, not every poll.

Every skip in ``_pending_itm_cash_secured_actions`` used to sit behind ``if not live``, so in
live mode a wheel that never re-entered looked exactly like one with nothing to do.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import TradeGroup


def _engine(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("BTC",),
        enable_short_put=False,
        enable_short_call=True,
    )
    return DeribitOptionTrialBot(config, FakeClient())


G1 = SimpleNamespace(group_id="g1")


def test_no_skip_is_hidden_behind_not_live():
    source = inspect.getsource(DeribitOptionTrialBot._pending_itm_cash_secured_actions)
    assert "if not live" not in source


def test_dry_run_reports_every_cycle(tmp_path):
    engine = _engine(tmp_path)
    assert engine._cash_secured_skip(G1, "usdc_unavailable", live=False)["reason"] == "usdc_unavailable"
    assert engine._cash_secured_skip(G1, "usdc_unavailable", live=False) is not None


def test_live_reports_a_reason_once_until_it_changes(tmp_path):
    engine = _engine(tmp_path)
    first = engine._cash_secured_skip(G1, "no_short_dated_put", live=True, detail={"strike_min": "84550.00"})
    assert first == {
        "action": "cash_secured_skipped",
        "group_id": "g1",
        "reason": "no_short_dated_put",
        "strike_min": "84550.00",
    }
    assert engine._cash_secured_skip(G1, "no_short_dated_put", live=True) is None
    assert engine._cash_secured_skip(G1, "crisis_regime", live=True)["reason"] == "crisis_regime"


def test_entering_forgets_the_last_reason(tmp_path):
    engine = _engine(tmp_path)
    engine._cash_secured_skip(G1, "usdc_unavailable", live=True)
    engine._cash_secured_skip_memory().pop("g1", None)
    assert engine._cash_secured_skip(G1, "usdc_unavailable", live=True) is not None


def test_groups_are_tracked_separately(tmp_path):
    engine = _engine(tmp_path)
    assert engine._cash_secured_skip(SimpleNamespace(group_id="a"), "crisis_regime", live=True) is not None
    assert engine._cash_secured_skip(SimpleNamespace(group_id="b"), "crisis_regime", live=True) is not None


def _closed_covered_call(group_id: str, **overrides) -> TradeGroup:
    payload = {
        "group_id": group_id,
        "currency": "BTC",
        "short_instrument_name": "BTC-28AUG26-77000-C",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "covered_underlying_quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "short_strike": "77000",
        "entry_credit": "0.001",
        "original_entry_credit": "0.001",
        "max_loss": "0",
        "regime_at_entry": "normal",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_a_covered_call_that_was_never_called_away_is_not_reported(tmp_path):
    """Seen on the first live cycle after a restart: one skip line per closed call, ~90 on a busy account.

    Only a spot exit that exists and is still selling is a reason the put is waiting.
    """
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("BTC",),
        enable_short_put=False,
        enable_short_call=True,
        covered_call_itm_to_cash_secured_enabled=True,
    )
    engine = DeribitOptionTrialBot(config, FakeClient())
    never_called_away = _closed_covered_call("0001")
    still_selling = _closed_covered_call("0002", spot_exit_status="pending")
    context = SimpleNamespace(
        state=SimpleNamespace(groups=[never_called_away, still_selling]),
        summaries={},
        snapshot=SimpleNamespace(hard_derisk_by_book={}),
        regime_by_currency={},
        orderbook_cache={},
    )
    actions = engine._pending_itm_cash_secured_actions(context, live=False)
    assert [(row["group_id"], row["reason"]) for row in actions] == [("0002", "spot_exit_not_filled")]
