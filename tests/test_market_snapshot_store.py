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


def test_market_snapshot_migrates_iv_percentile_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "market.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                btc_usd TEXT NOT NULL,
                eth_usd TEXT NOT NULL,
                btc_change_24h_pct TEXT,
                eth_change_24h_pct TEXT,
                iv_rank_btc_pct TEXT,
                iv_rank_eth_pct TEXT,
                dvol_btc TEXT,
                dvol_eth TEXT,
                source TEXT NOT NULL DEFAULT 'deribit_public'
            )
            """
        )
        conn.commit()

    store = MarketSnapshotStore(db_path)
    row_id = store.append_from_spot_payload(
        {
            "BTC": "65000",
            "ETH": "3500",
            "iv_percentile_pct": {"BTC": "70", "ETH": "40"},
            "dvol": {"BTC": "50", "ETH": "45"},
        }
    )
    row = store.get(row_id)
    assert row is not None
    assert row.iv_percentile_btc_pct == Decimal("70")
    assert row.dvol_btc == Decimal("50")
    payload = row.to_spot_api_payload()
    assert payload["iv_percentile_pct"]["BTC"] == "70"
    assert payload["dvol"]["ETH"] == "45"
