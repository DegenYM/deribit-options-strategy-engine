"""Tests for market + portal snapshot SQLite stores."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from deribit_engine.market_snapshot_store import MarketSnapshotStore
from deribit_engine.portal_snapshot_service import (
    PORTAL_PAYLOAD_STRING_MAX_CHARS,
    PortalSnapshotService,
    build_portal_payload,
)
from deribit_engine.portal_snapshot_store import PortalSnapshotStore
from deribit_engine.utils import utc_now_ms


def _portal_row_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM portal_snapshots").fetchone()[0])


def test_market_snapshot_store_roundtrip(tmp_path: Path) -> None:
    store = MarketSnapshotStore(tmp_path / "market.db")
    row_id = store.append_from_spot_payload(
        {
            "BTC": "65000",
            "ETH": "3500",
            "price_change_pct_24h": {"BTC": "1.2", "ETH": "-0.5"},
            "iv_rank_pct": {"BTC": "55", "ETH": "40"},
        }
    )
    row = store.get(row_id)
    assert row is not None
    assert row.btc_usd == Decimal("65000")
    assert row.btc_change_24h_pct == Decimal("1.2")
    assert row.eth_change_24h_pct == Decimal("-0.5")
    payload = row.to_spot_api_payload()
    assert payload["price_change_pct_24h"] == {"BTC": "1.2", "ETH": "-0.5"}
    assert store.latest() is not None
    deleted = store.purge_older_than(cutoff_ms=row.ts_ms + 1)
    assert deleted >= 1


def test_portal_snapshot_store_dedupes_fingerprint(tmp_path: Path) -> None:
    store = PortalSnapshotStore(tmp_path / "portal.db")
    payload = build_portal_payload(
        ledger_snapshot={
            "source": "ledger",
            "snapshot_ts_ms": 1_000,
            "freshness_ms": 100,
            "portfolio": {"total_equity_usdc": "1000"},
            "accounts": [],
            "scheduler": {},
        },
        groups={"open": [], "closed": [], "source": "disk"},
        realized_summary={"summary": {"realized_pnl_usdc": "10"}},
        dashboard_strategies=["covered_call"],
    )
    first = store.append(
        investor_id="alice",
        snapshot_kind="disk",
        payload=payload,
        content_fingerprint="abc123",
    )
    second = store.append(
        investor_id="alice",
        snapshot_kind="disk",
        payload=payload,
        content_fingerprint="abc123",
    )
    assert first is not None
    assert second is None


def test_portal_snapshot_store_serializes_datetime_in_payload(tmp_path: Path) -> None:
    store = PortalSnapshotStore(tmp_path / "portal.db")
    payload = {
        "portfolio": {"total_equity_usdc": "1000"},
        "realized_summary": {
            "generated_at": datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
            "summary": {"realized_pnl_usdc": "10"},
        },
    }
    row_id = store.append(
        investor_id="alice",
        snapshot_kind="disk",
        payload=payload,
        content_fingerprint="datetime-test",
    )
    assert row_id is not None
    row = store.latest("alice", snapshot_kind="disk")
    assert row is not None
    assert row.payload["realized_summary"]["generated_at"] == "2026-06-30T12:00:00+00:00"


def test_portal_service_load_prefers_live(tmp_path: Path) -> None:
    service = PortalSnapshotService(repo_root=tmp_path, investor_id="alice")
    disk_payload = build_portal_payload(
        ledger_snapshot={
            "source": "ledger",
            "snapshot_ts_ms": 1_000,
            "freshness_ms": 100,
            "portfolio": {"total_equity_usdc": "900"},
            "accounts": [],
            "scheduler": {},
        },
        groups={"open": [], "closed": [], "source": "disk"},
        realized_summary={"summary": {"realized_pnl_usdc": "5"}},
        dashboard_strategies=["covered_call"],
    )
    live_payload = build_portal_payload(
        ledger_snapshot={
            "source": "ledger",
            "snapshot_ts_ms": 2_000,
            "freshness_ms": 0,
            "portfolio": {"total_equity_usdc": "1000"},
            "accounts": [],
            "scheduler": {},
        },
        groups={"open": [], "closed": [], "source": "disk"},
        realized_summary={"summary": {"realized_pnl_usdc": "10"}},
        dashboard_strategies=["covered_call"],
    )
    now = utc_now_ms()
    service.portal_store.append(
        investor_id="alice",
        snapshot_kind="disk",
        payload=disk_payload,
        content_fingerprint="disk",
        ts_ms=now - 600_000,
    )
    service.portal_store.append(
        investor_id="alice",
        snapshot_kind="live",
        payload=live_payload,
        content_fingerprint="live",
        ts_ms=now - 1_000,
    )
    api = service.load_for_api(prefer_live=True, live_max_age_ms=3600_000)
    assert api is not None
    assert api["cache_kind"] == "live"
    assert api["source"] == "portal_cache"
    assert api["portfolio"]["total_equity_usdc"] == "1000"


def test_portal_snapshot_store_keeps_only_latest_per_kind(tmp_path: Path) -> None:
    db_path = tmp_path / "portal.db"
    store = PortalSnapshotStore(db_path)
    first = store.append(
        investor_id="alice",
        snapshot_kind="disk",
        payload={"n": 1},
        content_fingerprint="disk-1",
        ts_ms=1_000,
    )
    second = store.append(
        investor_id="alice",
        snapshot_kind="disk",
        payload={"n": 2},
        content_fingerprint="disk-2",
        ts_ms=2_000,
    )
    live = store.append(
        investor_id="alice",
        snapshot_kind="live",
        payload={"n": 3},
        content_fingerprint="live-1",
        ts_ms=3_000,
    )
    assert first is not None
    assert second is not None
    assert live is not None
    assert _portal_row_count(db_path) == 2
    disk_row = store.latest("alice", snapshot_kind="disk")
    live_row = store.latest("alice", snapshot_kind="live")
    assert disk_row is not None
    assert live_row is not None
    assert disk_row.payload == {"n": 2}
    assert live_row.payload == {"n": 3}


def test_build_portal_payload_truncates_long_strings() -> None:
    huge = "x" * (PORTAL_PAYLOAD_STRING_MAX_CHARS + 512)
    payload = build_portal_payload(
        ledger_snapshot={
            "source": "ledger",
            "snapshot_ts_ms": 1_000,
            "freshness_ms": 100,
            "portfolio": {"note": huge},
            "accounts": [],
            "scheduler": {},
        },
        groups={"open": [{"spot_exit_reason": huge}], "closed": []},
        realized_summary={"summary": {"realized_pnl_usdc": "10"}},
        dashboard_strategies=["covered_call"],
    )
    assert len(payload["groups"]["open"][0]["spot_exit_reason"]) == PORTAL_PAYLOAD_STRING_MAX_CHARS
    assert len(payload["portfolio"]["note"]) == PORTAL_PAYLOAD_STRING_MAX_CHARS


def test_capture_live_truncates_live_status_strings(tmp_path: Path) -> None:
    service = PortalSnapshotService(repo_root=tmp_path, investor_id="alice")
    huge = "y" * (PORTAL_PAYLOAD_STRING_MAX_CHARS + 128)
    captured = service.capture_live(
        ledger_snapshot={
            "source": "ledger",
            "snapshot_ts_ms": 2_000,
            "freshness_ms": 0,
            "portfolio": {"total_equity_usdc": "1000"},
            "accounts": [],
            "scheduler": {},
        },
        status={
            "portfolio": {"total_equity_usdc": "1000"},
            "underlying_index_usd": {"note": huge},
        },
        groups={"open": [{"spot_exit_reason": huge}], "closed": []},
        realized_summary={"summary": {"realized_pnl_usdc": "10"}},
        dashboard_strategies=["covered_call"],
        min_interval_sec=0,
    )
    assert captured is True
    row = service.portal_store.latest("alice", snapshot_kind="live")
    assert row is not None
    assert len(row.payload["groups"]["open"][0]["spot_exit_reason"]) == PORTAL_PAYLOAD_STRING_MAX_CHARS
    assert len(row.payload["live_status"]["underlying_index_usd"]["note"]) == PORTAL_PAYLOAD_STRING_MAX_CHARS
