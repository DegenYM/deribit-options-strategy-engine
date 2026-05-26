#!/usr/bin/env python3
"""Cron-friendly investor NAV snapshot for performance-fee billing.

Example (daily at 23:55 UTC):
  python3 scripts/snapshot_investor_fee_nav.py --investor an
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deribit_engine.env_layout import find_repo_root  # noqa: E402
from deribit_engine.investor_nav_snapshot import (  # noqa: E402
    capture_investor_nav,
    store_nav_capture,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Append investor NAV_perf snapshot to fee ledger")
    parser.add_argument("--investor", required=True, metavar="ID", help="Investor id under config/investors/<ID>")
    parser.add_argument(
        "--kind",
        default="scheduled",
        help="Snapshot kind label stored in SQLite (default: scheduled)",
    )
    parser.add_argument("--notes", default=None, help="Optional free-text note")
    args = parser.parse_args()

    repo_root = find_repo_root(REPO_ROOT)
    if repo_root is None:
        print("error: cannot locate repository root", file=sys.stderr)
        return 1

    capture = capture_investor_nav(args.investor, repo_root=repo_root)
    row_id, bootstrap = store_nav_capture(
        capture,
        repo_root=repo_root,
        snapshot_kind=args.kind,
        notes=args.notes,
    )
    print(
        f"snapshot id={row_id} investor={capture.investor_id} nav_perf={capture.nav_perf} aum_mgmt={capture.aum_mgmt}"
    )
    if bootstrap:
        print(
            f"hwm bootstrap source={bootstrap.get('source')} "
            f"initial_hwm={bootstrap.get('initial_hwm_nav_perf')} "
            f"net_flow={bootstrap.get('cumulative_net_flow_usdc')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
