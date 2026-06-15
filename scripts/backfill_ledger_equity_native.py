#!/usr/bin/env python3
"""Backfill ``equity_native_by_book`` on frontend equity ledger JSONL snapshots.

Uses existing ``equity_by_book`` on each row plus index prices from (in order):
stored row fields, local market.db, and Deribit public index chart API.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deribit_engine.env_layout import investor_frontend_ledger_dir, load_investor_manifest
from deribit_engine.frontend_ledger_backfill import (
    backfill_ledger_equity_native,
    default_public_client,
    market_store_for_ledger_root,
)


def _resolve_ledger_root(args: argparse.Namespace) -> Path:
    if args.ledger_dir:
        return args.ledger_dir.resolve()
    if args.investor:
        manifest = load_investor_manifest(args.investor, repo_root=REPO_ROOT)
        return investor_frontend_ledger_dir(REPO_ROOT, manifest.investor_id)
    default = REPO_ROOT / "data" / "frontend_ledger"
    if default.is_dir():
        return default
    raise SystemExit("Specify --ledger-dir or --investor")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--investor", help="Investor id (uses data/frontend_ledger/<id>)")
    parser.add_argument("--ledger-dir", type=Path, help="Explicit frontend ledger directory")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional env file for Deribit REST base URL (public index API only)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute updates without writing files")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Do not call Deribit index chart API (use row + market.db only)",
    )
    args = parser.parse_args()

    ledger_root = _resolve_ledger_root(args)
    client = None if args.no_api else default_public_client(args.env_file)
    market_store = market_store_for_ledger_root(ledger_root)
    summary = backfill_ledger_equity_native(
        ledger_root,
        client=client,
        market_store=market_store,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    else:
        mode = "DRY RUN" if args.dry_run else "APPLIED"
        print(f"[{mode}] ledger={ledger_root}")
        print(
            f"files={summary.files_scanned} rows={summary.rows_scanned} "
            f"updated={summary.rows_updated} index_fields={summary.index_rows_written}"
        )
        if summary.index_api_points:
            print(f"index_api_points={summary.index_api_points}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
