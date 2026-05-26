#!/usr/bin/env python3
"""Backfill closed-group PnL indices and position-size realized APR in strategy state files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deribit_engine.trade_journal_backfill import backfill_closed_group_stats_in_state


def _iter_state_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith(".performance_exclusions.json"):
            continue
        out.append(path)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=REPO_ROOT / ".state",
        help="Scan this directory for strategy state JSON files (default: .state)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        action="append",
        help="Backfill one state file (repeatable); overrides --state-root scan",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute updates without writing state files",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    paths = [p.resolve() for p in args.state_file] if args.state_file else _iter_state_files(args.state_root.resolve())
    if not paths:
        print("No state files found.", file=sys.stderr)
        return 1

    summaries = []
    total_pnl = total_apr = total_entry_apr = total_closed = 0
    for path in paths:
        summary = backfill_closed_group_stats_in_state(path, dry_run=args.dry_run)
        summaries.append(summary.to_dict())
        total_pnl += summary.pnl_updated
        total_apr += summary.apr_updated
        total_entry_apr += summary.entry_apr_updated
        total_closed += summary.closed_groups

    payload = {
        "dry_run": args.dry_run,
        "files": len(paths),
        "closed_groups": total_closed,
        "pnl_updated": total_pnl,
        "apr_updated": total_apr,
        "entry_apr_updated": total_entry_apr,
        "accounts": summaries,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        mode = "DRY RUN" if args.dry_run else "APPLIED"
        print(f"[{mode}] scanned {len(paths)} state file(s)")
        print(
            f"closed groups: {total_closed}; pnl updated: {total_pnl}; "
            f"close apr updated: {total_apr}; entry apr updated: {total_entry_apr}"
        )
        for row in summaries:
            if row["closed_groups"] == 0 and row["entry_apr_updated"] == 0:
                continue
            tag = "saved" if row["saved"] else "unchanged"
            print(
                f"  {row['state_file']}: closed={row['closed_groups']} "
                f"pnl={row['pnl_updated']} close_apr={row['apr_updated']} "
                f"entry_apr={row['entry_apr_updated']} ({tag})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
