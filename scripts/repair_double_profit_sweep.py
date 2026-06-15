#!/usr/bin/env python3
"""Repair duplicate profit sweeps: fix state and buy back over-sold native."""

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
from deribit_engine.exceptions import ConfigurationError
from deribit_engine.profit_sweep_repair import repair_double_profit_sweeps
from deribit_engine.utils import json_default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        required=True,
        help="Strategy sub-account env (e.g. config/investors/jack/accounts/.env.covered_call)",
    )
    parser.add_argument(
        "--investor",
        metavar="ID",
        help="Investor id under config/investors/<ID> (optional; use with --account)",
    )
    parser.add_argument(
        "--account",
        metavar="SLUG",
        help="Sub-account slug from accounts.toml when using --investor",
    )
    parser.add_argument(
        "--no-buyback",
        action="store_true",
        help="Only repair state; do not buy back duplicate-sold BTC/ETH",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Apply state fixes and submit buyback spot orders",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.investor:
        from deribit_engine.cli.common import apply_investor_cli_args

        ns = argparse.Namespace(
            command="profit-sweep-repair",
            investor=args.investor,
            account=args.account,
            env_file=args.env_file,
            env_file_after_cmd=None,
        )
        apply_investor_cli_args(ns)
        args.env_file = ns.env_file

    cfg = load_config(args.env_file, require_private=args.live)
    if cfg.option_strategy != "covered_call":
        raise SystemExit(f"profit sweep repair requires covered_call strategy (got {cfg.option_strategy!r})")

    bot_args = argparse.Namespace(
        command="manage",
        env_file=args.env_file,
        investor=None,
        account=None,
        strategy=None,
        verbose=args.verbose,
    )
    bot = build_bot(bot_args)

    try:
        result = repair_double_profit_sweeps(
            bot,
            live=args.live,
            buyback=not args.no_buyback,
            save_state=True,
        )
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    if args.json:
        print(json.dumps(result, default=json_default, ensure_ascii=False, indent=2))
    else:
        mode = "LIVE" if args.live else "DRY RUN"
        plan = result.get("plan") or {}
        print(f"[{mode}] duplicate_groups={plan.get('duplicate_groups')} repaired={result.get('repaired_groups')}")
        print(f"  state proceeds: {plan.get('state_proceeds_before')} -> {plan.get('state_proceeds_after')} USDT")
        if plan.get("buyback_native"):
            print(f"  buyback native: {plan.get('buyback_native')}")
        if plan.get("buyback_usdt_estimate"):
            print(f"  buyback USDT est: {plan.get('buyback_usdt_estimate')}")
        for row in plan.get("ledgers") or []:
            print(
                f"  group {row.get('group_id')} {row.get('currency')} "
                f"dup={row.get('duplicate_native')} ({row.get('duplicate_proceeds_usdt')} USDT)"
            )
        for action in result.get("buyback_actions") or []:
            print(
                f"  buyback action={action.get('action')} "
                f"target={action.get('buyback_base_target')} "
                f"budget={action.get('buyback_quote_budget')} USDT"
            )
        if result.get("state_saved"):
            print(f"state saved: {cfg.state_file}")
        elif not args.live:
            print("DRY RUN: no orders submitted; state file not modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
