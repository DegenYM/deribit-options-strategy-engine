"""Orderbook refresh failures for one group must not crash the manage cycle."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from conftest import make_config

from deribit_engine.engine import DeribitOptionTrialBot, ExchangePrefetch
from deribit_engine.exceptions import ExchangeError
from deribit_engine.models import Position, StrategyState
from tests.test_engine import _build_group


def _short_put_position(instrument_name: str, *, size: str = "0.1") -> Position:
    return Position.from_api(
        {
            "instrument_name": instrument_name,
            "direction": "sell",
            "kind": "option",
            "size": size,
            "size_currency": size,
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.1",
        }
    )


def test_load_runtime_continues_when_one_group_refresh_fails(tmp_path, fake_client):
    config = make_config(tmp_path, option_markets_profile="linear_usdc")
    engine = DeribitOptionTrialBot(config, fake_client)

    short_a = "BTC_USDC-14APR30-63000-P"
    short_b = "BTC_USDC-14APR30-60000-P"
    group_a = _build_group(short_instrument_name=short_a)
    group_a.group_id = "0001"
    group_b = _build_group(short_instrument_name=short_b)
    group_b.group_id = "0002"

    state = StrategyState()
    state.groups.extend([group_a, group_b])
    engine.state_store.save(state)

    markets = engine._load_supported_option_markets()
    option_positions = [_short_put_position(short_a), _short_put_position(short_b)]
    prefetch = ExchangePrefetch(
        summaries={},
        open_orders=[],
        positions=option_positions,
        option_positions=option_positions,
        future_positions=[],
        future_markets_by_name=engine._load_perpetual_markets(),
        markets_by_currency=markets,
    )

    real_refresh = engine._refresh_group
    refreshed_ids: list[str] = []

    def flaky_refresh(*, context_markets, group, orderbook_cache):
        if group.group_id == "0001":
            raise ExchangeError("orderbook unavailable for delisted instrument")
        refreshed_ids.append(group.group_id)
        real_refresh(context_markets=context_markets, group=group, orderbook_cache=orderbook_cache)

    with patch.object(engine, "_refresh_group", side_effect=flaky_refresh):
        context, _ = engine._load_runtime_from_exchange(prefetch, live=False)

    assert context is not None
    assert refreshed_ids == ["0002"]

    open_by_id = {g.group_id: g for g in context.state.groups if g.status == "open"}
    assert open_by_id["0001"].last_action == "refresh_failed"
    assert open_by_id["0001"].current_debit == Decimal("0")
    assert open_by_id["0002"].current_debit > 0
