"""Backfill ``equity_native_by_book`` (and index fields) on frontend equity ledger rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .client import DeribitClient
from .config import load_config
from .env_layout import find_repo_root, shared_market_db_path
from .frontend_server.helpers import (
    _iter_ledger_files,
    _resolve_index_prices_for_ledger_row,
    compute_equity_native_by_book_for_row,
)
from .market_snapshot_store import MarketSnapshotStore
from .utils import format_decimal, json_default, to_decimal

LOGGER = logging.getLogger(__name__)


@dataclass
class LedgerEquityBackfillSummary:
    ledger_root: str
    files_scanned: int = 0
    rows_scanned: int = 0
    rows_updated: int = 0
    index_rows_written: int = 0
    index_api_points: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_root": self.ledger_root,
            "files_scanned": self.files_scanned,
            "rows_scanned": self.rows_scanned,
            "rows_updated": self.rows_updated,
            "index_rows_written": self.index_rows_written,
            "index_api_points": dict(self.index_api_points),
            "dry_run": self.dry_run,
        }


def _normalize_index_chart_points(raw: Any) -> list[tuple[int, Decimal]]:
    out: list[tuple[int, Decimal]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, list | tuple) or len(row) < 2:
            continue
        ts = int(row[0])
        if ts < 10_000_000_000:
            ts *= 1000
        price = to_decimal(row[1])
        if price > 0:
            out.append((ts, price))
    out.sort(key=lambda item: item[0])
    return out


def fetch_index_series_from_api(
    client: DeribitClient,
    index_name: str,
) -> list[tuple[int, Decimal]]:
    """Load historical index prices from Deribit public chart API."""
    for range_name in ("1y", "1m", "1d"):
        try:
            points = client.get_index_chart_data(index_name, range_name=range_name)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("index chart fetch failed %s %s: %s", index_name, range_name, exc)
            continue
        series = _normalize_index_chart_points(points)
        if series:
            return series
    return []


def _native_map_for_row(
    row: dict[str, Any],
    *,
    market_store: MarketSnapshotStore | None,
    index_btc_series: list[tuple[int, Decimal]] | None,
    index_eth_series: list[tuple[int, Decimal]] | None,
) -> tuple[dict[str, str], Decimal | None, Decimal | None]:
    index_btc, index_eth = _resolve_index_prices_for_ledger_row(
        row,
        market_store=market_store,
        index_btc_series=index_btc_series,
        index_eth_series=index_eth_series,
    )
    native = compute_equity_native_by_book_for_row(
        row,
        index_btc=index_btc,
        index_eth=index_eth,
    )
    return {book: format_decimal(value, 12) for book, value in native.items()}, index_btc, index_eth


def backfill_ledger_equity_native(
    ledger_root: Path,
    *,
    client: DeribitClient | None = None,
    market_store: MarketSnapshotStore | None = None,
    dry_run: bool = False,
) -> LedgerEquityBackfillSummary:
    """Rewrite equity ledger JSONL rows with computed ``equity_native_by_book``."""
    summary = LedgerEquityBackfillSummary(ledger_root=str(ledger_root), dry_run=dry_run)
    if not ledger_root.exists():
        return summary

    rows_by_file: dict[Path, list[dict[str, Any]]] = {}
    min_ts = 0
    max_ts = 0
    for path in _iter_ledger_files(ledger_root):
        file_rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    file_rows.append(row)
                    ts = int(row.get("ts_ms") or 0)
                    if ts > 0:
                        min_ts = ts if min_ts <= 0 else min(min_ts, ts)
                        max_ts = max(max_ts, ts)
        except OSError as exc:
            LOGGER.warning("ledger read failed for %s: %s", path, exc)
            continue
        if file_rows:
            rows_by_file[path] = file_rows

    index_btc_series: list[tuple[int, Decimal]] | None = None
    index_eth_series: list[tuple[int, Decimal]] | None = None
    if client is not None and max_ts > min_ts > 0:
        index_btc_series = fetch_index_series_from_api(client, "btc_usd")
        index_eth_series = fetch_index_series_from_api(client, "eth_usd")
        summary.index_api_points = {
            "btc_usd": len(index_btc_series),
            "eth_usd": len(index_eth_series),
        }

    for path, file_rows in rows_by_file.items():
        summary.files_scanned += 1
        changed = False
        for row in file_rows:
            summary.rows_scanned += 1
            native_map, index_btc, index_eth = _native_map_for_row(
                row,
                market_store=market_store,
                index_btc_series=index_btc_series,
                index_eth_series=index_eth_series,
            )
            if not native_map:
                continue
            existing = row.get("equity_native_by_book") or {}
            if existing != native_map:
                row["equity_native_by_book"] = native_map
                summary.rows_updated += 1
                changed = True
            if index_btc is not None and index_btc > 0 and not row.get("index_btc_usd"):
                row["index_btc_usd"] = format_decimal(index_btc, 4)
                summary.index_rows_written += 1
                changed = True
            if index_eth is not None and index_eth > 0 and not row.get("index_eth_usd"):
                row["index_eth_usd"] = format_decimal(index_eth, 4)
                summary.index_rows_written += 1
                changed = True
        if changed and not dry_run:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fp:
                for row in file_rows:
                    fp.write(json.dumps(row, default=json_default, ensure_ascii=False) + "\n")
            tmp.replace(path)
    return summary


def default_public_client(env_file: Path | None = None) -> DeribitClient:
    """Build a Deribit client for public index endpoints (credentials optional)."""
    if env_file is None:
        repo_root = find_repo_root(Path.cwd())
        if repo_root is not None:
            candidate = repo_root / "config" / "shared" / ".env.defaults"
            if candidate.is_file():
                env_file = candidate
    if env_file is not None and env_file.is_file():
        config = load_config(env_file, require_private=False)
    else:
        raise ValueError("Need --env-file or config/shared/.env.defaults for Deribit index API")
    return DeribitClient(config)


def market_store_for_ledger_root(ledger_root: Path) -> MarketSnapshotStore | None:
    repo_root = find_repo_root(ledger_root)
    if repo_root is None:
        return None
    path = shared_market_db_path(repo_root)
    if not path.exists():
        return None
    return MarketSnapshotStore(path)
