"""Profit sweep must never treat a failed exchange-fills lookup as "not swept yet"."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from conftest import FakeClient, make_config
from test_engine import _covered_call_group

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.exceptions import TransientExchangeError
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.profit_sweep_ops import (
    ProfitSweepTradeCache,
    ProfitSweepTradesUnavailable,
    _exchange_remaining_native,
    exchange_swept_native_for_group,
    guard_profit_sweep_against_oversell,
    profit_sweep_sell_trades_for_group,
    refresh_profit_sweep_exchange_native,
    reschedule_failed_profit_sweeps,
    reschedule_ledger_only_profit_sweeps,
    run_remaining_profit_sweeps,
    schedule_remaining_closed_profit_sweeps,
)


class _TradesDownClient(FakeClient):
    """``get_user_trades_by_currency`` fails for the sweep-decision fetches; everything else works.

    Models an intermittent outage: the sweep cache / decision helpers (which pass
    ``currency`` positionally) get a transient error, while the unrelated
    unlabeled-premium repair scan in ``trade_journal_backfill`` (keyword
    ``currency=``, not owned by this module and already hard-failing on error)
    is allowed through so the manage cycle reaches the sweep decision.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.trade_fetches = 0

    def get_user_trades_by_currency(self, *args, **kwargs):
        if not args:
            return super().get_user_trades_by_currency(*args, **kwargs)
        self.trade_fetches += 1
        raise TransientExchangeError("private/get_user_trades_by_currency failed after retries: HTTP 502")


def _group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "g1",
        "currency": "BTC",
        "short_instrument_name": "BTC-28MAR25-90000-C",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "short_strike": "90000",
        "entry_credit": "30",
        "original_entry_credit": "30",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "realized_pnl_collateral_native": "0.001",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def _cc_config(tmp_path):
    return make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_profit_sweep_enabled=True,
        traded_collaterals=("BTC", "ETH", "USDT"),
    )


# ------------------------------------------------------------------
# Cache / primitive semantics
# ------------------------------------------------------------------


def test_cache_raises_and_does_not_mark_loaded_on_fetch_failure(caplog):
    client = _TradesDownClient()
    cache = ProfitSweepTradeCache(client)
    group = _group()

    with caplog.at_level(logging.WARNING, logger="deribit_engine.profit_sweep"):
        with pytest.raises(ProfitSweepTradesUnavailable) as info:
            cache.trades_for_group_strict(group, "trial")

    assert info.value.currency == "BTC"
    assert "BTC" not in cache._loaded
    assert cache.is_unavailable("BTC")
    assert cache.unavailable_currencies() == {"BTC": str(info.value.__cause__)}
    assert any("get_user_trades_by_currency(BTC) failed" in r.getMessage() for r in caplog.records)

    # Second lookup for the same currency re-raises without hammering the API.
    with pytest.raises(ProfitSweepTradesUnavailable):
        cache.trades_for_group_strict(group, "trial")
    assert client.trade_fetches == 1

    # Lenient accessor (read-only callers) still returns [] instead of raising.
    assert cache.trades_for_group(group, "trial") == []


def test_cache_success_path_unchanged():
    client = FakeClient()
    client.user_trades_by_currency = {
        ("BTC", None): {
            "trades": [
                {
                    "trade_id": "t1",
                    "label": "trial-profit-sweep-btc-g1",
                    "direction": "sell",
                    "amount": "0.0004",
                    "price": "70000",
                    "timestamp": 1,
                    "instrument_name": "BTC_USDT",
                    "order_id": "o1",
                }
            ]
        }
    }
    cache = ProfitSweepTradeCache(client)
    group = _group()

    trades = cache.trades_for_group_strict(group, "trial")

    assert [t["trade_id"] for t in trades] == ["t1"]
    assert "BTC" in cache._loaded
    assert not cache.is_unavailable("BTC")


def test_profit_sweep_sell_trades_for_group_raises_without_cache():
    client = _TradesDownClient()
    with pytest.raises(ProfitSweepTradesUnavailable):
        profit_sweep_sell_trades_for_group(client, _group(), "trial")
    with pytest.raises(ProfitSweepTradesUnavailable):
        exchange_swept_native_for_group(client, _group(), "trial")


# ------------------------------------------------------------------
# Decision helpers: unavailable → skip / block, never "sweep"
# ------------------------------------------------------------------


def test_guard_blocks_when_fills_unavailable(caplog):
    client = _TradesDownClient()
    group = _group()
    with caplog.at_level(logging.WARNING, logger="deribit_engine.profit_sweep"):
        blocked = guard_profit_sweep_against_oversell(group, client, "trial")
    assert blocked is True
    assert group.profit_sweep_status == ""
    assert any("oversell guard blocking" in r.getMessage() for r in caplog.records)


def test_exchange_remaining_native_is_none_when_fills_unavailable():
    assert _exchange_remaining_native(_TradesDownClient(), _group(), "trial") is None


def test_refresh_exchange_native_keeps_persisted_values_when_unavailable():
    group = _group(
        profit_sweep_status="filled",
        profit_sweep_amount="0.001",
        profit_sweep_order_id="o1",
        profit_sweep_exchange_native="0.0009",
        profit_sweep_exchange_quote_proceeds="60",
    )
    assert refresh_profit_sweep_exchange_native(group, _TradesDownClient(), "trial") is False
    assert group.profit_sweep_exchange_native == Decimal("0.0009")
    assert group.profit_sweep_exchange_quote_proceeds == Decimal("60")


def test_schedulers_do_not_queue_when_fills_unavailable(tmp_path):
    client = _TradesDownClient(btc_book_equity="0.5")
    engine = DeribitOptionTrialBot(_cc_config(tmp_path), client)

    never = _group(close_reason="reconciled_external")
    failed = _group(group_id="g2", profit_sweep_status="failed", profit_sweep_reason="take_profit")
    ledger_only = _group(
        group_id="g3",
        profit_sweep_status="filled",
        profit_sweep_reason="proceeds_reconciled",
    )

    cache = ProfitSweepTradeCache(client)
    assert schedule_remaining_closed_profit_sweeps(engine, [never], trade_cache=cache) == 0
    assert reschedule_failed_profit_sweeps(engine, [failed], trade_cache=cache) == 0
    assert reschedule_ledger_only_profit_sweeps(engine, [ledger_only], trade_cache=cache) == 0

    assert never.profit_sweep_status == ""
    assert failed.profit_sweep_status == "failed"
    assert ledger_only.profit_sweep_status == "filled"
    assert client.placed_orders == []


# ------------------------------------------------------------------
# End-to-end: live manage cycle / manual sweep run
# ------------------------------------------------------------------


def _pending_state() -> StrategyState:
    state = StrategyState()
    group = _covered_call_group()
    group.status = "closed"
    group.close_reason = "take_profit"
    group.profit_sweep_status = "pending"
    group.profit_sweep_amount = Decimal("0.0005")
    group.realized_pnl_collateral_native = Decimal("0.0005")
    state.groups.append(group)
    return state


def test_manage_live_places_sweep_when_fills_available(tmp_path):
    """Control: identical setup with a healthy trades API does place the sweep."""
    client = FakeClient(btc_book_equity="0.5")
    engine = DeribitOptionTrialBot(_cc_config(tmp_path), client)
    engine.state_store.save(_pending_state())

    engine.manage(live=True)

    assert [o for o in client.placed_orders if o["instrument_name"] == "BTC_USDT"]


def test_manage_live_skips_pending_sweep_when_fills_unavailable(tmp_path, caplog):
    client = _TradesDownClient(btc_book_equity="0.5")
    engine = DeribitOptionTrialBot(_cc_config(tmp_path), client)
    engine.state_store.save(_pending_state())

    with caplog.at_level(logging.WARNING):
        result = engine.manage(live=True)

    spot_orders = [o for o in client.placed_orders if o["instrument_name"] == "BTC_USDT"]
    assert spot_orders == [], "no sweep order may be placed while exchange fills are unknown"
    skipped = [a for a in result["actions"] if a.get("action") == "covered_call_profit_sweep_skipped"]
    assert skipped and skipped[0]["reason"] == "exchange_trades_unavailable"
    saved = engine.state_store.load().groups[0]
    assert saved.profit_sweep_status == "pending"
    assert saved.profit_sweep_amount == Decimal("0.0005")
    assert not saved.profit_sweep_order_id
    assert any("fills unavailable this cycle" in r.getMessage() for r in caplog.records)
    assert client.trade_fetches >= 1


def test_run_remaining_profit_sweeps_live_skips_when_fills_unavailable(tmp_path, caplog):
    client = _TradesDownClient(btc_book_equity="0.5")
    engine = DeribitOptionTrialBot(_cc_config(tmp_path), client)
    engine.state_store.save(_pending_state())

    with caplog.at_level(logging.WARNING, logger="deribit_engine.profit_sweep"):
        summary = run_remaining_profit_sweeps(engine, live=True)

    assert summary.scheduled == 0
    assert [o for o in client.placed_orders if o["instrument_name"] == "BTC_USDT"] == []
    assert not any(a.get("action") == "covered_call_profit_sweep" for a in summary.actions)
    saved = engine.state_store.load().groups[0]
    assert saved.profit_sweep_status == "pending"
    assert any("unavailable" in r.getMessage() for r in caplog.records)
