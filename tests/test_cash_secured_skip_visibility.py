"""Skipped wheel puts are visible in live runs — once per reason, not every poll.

Every skip in ``_pending_itm_cash_secured_actions`` used to sit behind ``if not live``, so in
live mode a wheel that never re-entered looked exactly like one with nothing to do.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot


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
