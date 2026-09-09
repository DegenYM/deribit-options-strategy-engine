"""CSP cover-restore: account summaries refresh once per cycle; no order on a stale balance."""

from __future__ import annotations

import logging

from conftest import FakeClient
from test_cash_secured_ops import _csp_engine, _pending_csp_restore_group

from deribit_engine.exceptions import TransientExchangeError
from deribit_engine.models import StrategyState


class _CountingClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.summary_calls = 0
        self.fail_summaries = False

    def get_account_summaries(self, *, extended=False):
        self.summary_calls += 1
        if self.fail_summaries:
            raise TransientExchangeError("private/get_account_summaries failed after retries: HTTP 502")
        return super().get_account_summaries(extended=extended)


def _engine_with_pending_groups(tmp_path, client, count: int):
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    for i in range(count):
        state.groups.append(_pending_csp_restore_group(group_id=f"010{i}", cash_secured_from_group_id=f"009{i}"))
    engine.state_store.save(state)
    return engine


def test_restore_loop_refreshes_summaries_once_for_many_groups(tmp_path):
    client = _CountingClient(btc_book_equity="0.2")
    engine = _engine_with_pending_groups(tmp_path, client, count=3)
    ctx = engine._load_runtime()
    client.summary_calls = 0

    actions = engine._pending_cash_secured_cover_restore_actions(ctx, live=True)

    assert client.summary_calls == 1, "summaries must be fetched at most once per cycle, not per group"
    by_group = {a["group_id"]: a for a in actions}
    # First group spends (nearly) all free USDC; the loop tracks that itself instead of
    # re-reading a balance the fake never decrements, so later groups see what is left.
    assert by_group["0100"]["action"] == "cash_secured_cover_restore"
    assert by_group["0101"]["reason"] == "usdc_short_for_min_size"
    assert by_group["0102"]["reason"] == "usdc_short_for_min_size"
    assert len([o for o in client.placed_orders if o["instrument_name"] == "BTC_USDC"]) == 1


def test_restore_loop_places_nothing_when_summaries_refresh_fails(tmp_path, caplog):
    client = _CountingClient(btc_book_equity="0.2")
    engine = _engine_with_pending_groups(tmp_path, client, count=2)
    ctx = engine._load_runtime()
    client.fail_summaries = True
    client.summary_calls = 0

    with caplog.at_level(logging.WARNING):
        actions = engine._pending_cash_secured_cover_restore_actions(ctx, live=True)

    assert client.summary_calls == 1
    assert client.placed_orders == [], "a restore buy sizes itself from free USDC; never place on a stale balance"
    skipped = [a for a in actions if a.get("action") == "cash_secured_cover_restore_skipped"]
    assert [a["reason"] for a in skipped] == ["usdc_balance_unavailable", "usdc_balance_unavailable"]
    assert all(g.spot_restore_status == "pending" for g in ctx.state.groups)
    assert any("account summaries refresh failed" in r.getMessage() for r in caplog.records)


def test_restore_preview_does_not_need_fresh_summaries(tmp_path):
    client = _CountingClient(btc_book_equity="0.2")
    engine = _engine_with_pending_groups(tmp_path, client, count=1)
    ctx = engine._load_runtime()
    client.fail_summaries = True

    actions = engine._pending_cash_secured_cover_restore_actions(ctx, live=False)

    assert [a["action"] for a in actions] == ["cash_secured_cover_restore_preview"]
