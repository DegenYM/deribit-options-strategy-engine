from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from deribit_engine.market_snapshot_store import MarketSnapshotStore


def test_market_snapshot_nearest_at_or_before(tmp_path: Path) -> None:
    store = MarketSnapshotStore(tmp_path / "market.db")
    early = store.append_from_spot_payload({"BTC": "60000", "ETH": "3000"})
    row_early = store.get(early)
    assert row_early is not None
    late = store.append_from_spot_payload({"BTC": "70000", "ETH": "3500"})
    row_late = store.get(late)
    assert row_late is not None

    picked = store.nearest_at_or_before(row_late.ts_ms)
    assert picked is not None
    assert picked.btc_usd == Decimal("70000")

    picked_early = store.nearest_at_or_before(row_early.ts_ms)
    assert picked_early is not None
    assert picked_early.btc_usd == Decimal("60000")
