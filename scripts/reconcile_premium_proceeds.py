#!/usr/bin/env python3
"""Reconcile per-group profit_sweep_quote_proceeds from net exchange premium sweeps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deribit_engine.cli.common import build_bot, configure_logging
from deribit_engine.config import load_config
from deribit_engine.profit_sweep_repair import run_reconcile_premium_proceeds
from deribit_engine.utils import json_default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        required=True,
        help="Strategy sub-account env (e.g. config/investors/jack/accounts/.env.covered_call)",
    )
    parser.add_argument("--investor", metavar="ID", help="Investor id (use with --account)")
    parser.add_argument("--account", metavar="SLUG", help="Sub-account slug from accounts.toml")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write reconciled proceeds into state file",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.investor:
        from deribit_engine.cli.common import apply_investor_cli_args

        ns = argparse.Namespace(
            command="reconcile-premium-proceeds",
            investor=args.investor,
            account=args.account,
            env_file=args.env_file,
            env_file_after_cmd=None,
        )
        apply_investor_cli_args(ns)
        args.env_file = ns.env_file

    cfg = load_config(args.env_file, require_private=True)
    if cfg.option_strategy != "covered_call":
        raise SystemExit(f"reconcile premium proceeds requires covered_call strategy (got {cfg.option_strategy!r})")

    bot = build_bot(
        argparse.Namespace(
            command="manage",
            env_file=args.env_file,
            investor=None,
            account=None,
            strategy=None,
            verbose=args.verbose,
        )
    )

    result = run_reconcile_premium_proceeds(bot, live=args.live)

    if args.json:
        print(json.dumps(result, default=json_default, ensure_ascii=False, indent=2))
    else:
        mode = "LIVE" if args.live else "DRY RUN"
        print(f"[{mode}] reconcile premium proceeds")
        print(f"  net USDT by book: {result.get('net_usdt_by_book')}")
        print(f"  total USDT: {result.get('total_usdt')}")
        print(f"  updated groups: {result.get('updated_groups')}")
        for row in result.get("groups") or []:
            print(
                f"  group {row.get('group_id')} {row.get('currency')} "
                f"premium={row.get('premium_native')} "
                f"proceeds {row.get('before_usdt')} -> {row.get('proceeds_usdt')} USDT"
            )
        if result.get("state_saved"):
            print(f"state saved: {cfg.state_file}")
        elif not args.live:
            print("DRY RUN: state file not modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
