"""Bounding the crash-recovery repair's exchange calls.

The repair asks the exchange about every closed group, one request each, so its
cost grew without bound as trade history accumulated (85 requests per cycle on
the largest book here). The journal-backed half is local and stays unbounded.
"""

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import TradeGroup
from deribit_engine.utils import utc_now_ms

_DAY_MS = 86_400_000


def _closed_group(index: int, *, closed_days_ago: float | None):
    closed_ms = None if closed_days_ago is None else int(utc_now_ms() - closed_days_ago * _DAY_MS)
    return TradeGroup.from_dict(
        {
            "group_id": f"g{index}",
            "currency": "BTC",
            "short_instrument_name": "BTC-28MAR25-90000-C",
            "short_label": f"cc-{index}",
            "status": "closed",
            "strategy": "covered_call",
            "option_type": "call",
            "collateral_currency": "BTC",
            "quantity": "0.1",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "closed_timestamp_ms": closed_ms,
            "short_strike": "90000",
            "entry_credit": "30",
            "original_entry_credit": "30",
            "max_loss": "1000",
            "regime_at_entry": "normal",
        }
    )


class _LabelCountingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.label_lookups: list[str] = []

    def get_user_trades_by_currency(self, currency, *, kind="spot", count=100, historical=True):
        return {"trades": []}

    def get_order_state_by_label(self, currency, label):
        self.label_lookups.append(label)
        return []


def _engine(tmp_path, **overrides):
    return DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            order_label_prefix="cc",
            client_id="cid",
            client_secret="sec",
            **overrides,
        ),
        _LabelCountingClient(),
    )


def test_old_closed_groups_skip_the_exchange_reconcile(tmp_path):
    engine = _engine(tmp_path, covered_call_repair_lookback_days=30)
    state = engine.state_store.load()
    state.groups = [_closed_group(0, closed_days_ago=1), _closed_group(1, closed_days_ago=400)]

    engine._repair_reconciled_bot_income_exits_in_state(state)

    # Only the recently closed group reaches the exchange.
    assert engine.client.label_lookups == ["cc-profit-sweep-btc-g0"]


def test_zero_lookback_restores_the_unbounded_scan(tmp_path):
    engine = _engine(tmp_path, covered_call_repair_lookback_days=0)
    state = engine.state_store.load()
    state.groups = [_closed_group(0, closed_days_ago=1), _closed_group(1, closed_days_ago=400)]

    engine._repair_reconciled_bot_income_exits_in_state(state)

    assert len(engine.client.label_lookups) == 2


def test_group_with_unknown_close_time_is_never_skipped(tmp_path):
    """Missing closed_timestamp_ms must not silently drop a group from repair."""
    engine = _engine(tmp_path, covered_call_repair_lookback_days=30)
    state = engine.state_store.load()
    state.groups = [_closed_group(0, closed_days_ago=None)]

    engine._repair_reconciled_bot_income_exits_in_state(state)

    assert len(engine.client.label_lookups) == 1
