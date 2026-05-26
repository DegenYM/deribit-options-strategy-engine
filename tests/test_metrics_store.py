from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from deribit_engine.metrics_store import MetricsStore, performance_scope_key


class _Account:
    def __init__(self, name: str, state_path):
        self.name = name
        self.state_path = state_path


def test_metrics_store_sync_and_reload(tmp_path):
    db = tmp_path / "metrics.db"
    store = MetricsStore(db)
    scope = performance_scope_key([_Account("a", tmp_path / "a.json")])
    closed = [
        {
            "closed_timestamp_ms": int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000),
            "realized_pnl": "100",
            "collateral_currency": "USDC",
        },
        {
            "closed_timestamp_ms": int(datetime(2024, 6, 2, tzinfo=UTC).timestamp() * 1000),
            "realized_pnl": "50",
            "collateral_currency": "BTC",
        },
    ]
    store.sync_from_closed(scope, "fp-v1", closed, synced_at_ms=1)
    assert store.is_synced(scope, "fp-v1")
    assert not store.is_synced(scope, "fp-v2")

    totals = store.load_daily_totals(scope)
    assert totals[datetime(2024, 6, 1, tzinfo=UTC).date()] == Decimal("100")
    assert totals[datetime(2024, 6, 2, tzinfo=UTC).date()] == Decimal("50")

    by_book, daily_total = store.load_daily_by_book(scope)
    assert daily_total["2024-06-01"] == Decimal("100")
    assert by_book["BTC"]["2024-06-02"] == Decimal("50")
    assert store.closed_count(scope) == 2

    store.sync_from_closed(
        scope,
        "fp-v2",
        closed
        + [
            {
                "closed_timestamp_ms": int(datetime(2024, 6, 3, tzinfo=UTC).timestamp() * 1000),
                "realized_pnl": "25",
                "collateral_currency": "USDC",
            }
        ],
        synced_at_ms=2,
    )
    assert store.closed_count(scope) == 3
    totals = store.load_daily_totals(scope)
    assert totals[datetime(2024, 6, 3, tzinfo=UTC).date()] == Decimal("25")
