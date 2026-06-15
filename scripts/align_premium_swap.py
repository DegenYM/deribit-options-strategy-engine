#!/usr/bin/env python3
"""Buy back over-sold native so exchange net sold matches realized premium."""

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
from deribit_engine.profit_sweep_repair import repair_premium_swap_alignment
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
        help="Do not buy back over-sold native",
    )
    parser.add_argument(
        "--no-sell",
        action="store_true",
        help="Do not sell under-swept premium deficit",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Submit buyback spot orders on Deribit",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.investor:
        from deribit_engine.cli.common import apply_investor_cli_args

        ns = argparse.Namespace(
            command="premium-align",
            investor=args.investor,
            account=args.account,
            env_file=args.env_file,
            env_file_after_cmd=None,
        )
        apply_investor_cli_args(ns)
        args.env_file = ns.env_file

    cfg = load_config(args.env_file, require_private=args.live)
    if cfg.option_strategy != "covered_call":
        raise SystemExit(f"premium alignment requires covered_call strategy (got {cfg.option_strategy!r})")

    bot_args = argparse.Namespace(
        command="manage",
        env_file=args.env_file,
        investor=None,
        account=None,
        strategy=None,
        verbose=args.verbose,
    )
    bot = build_bot(bot_args)

    result = repair_premium_swap_alignment(
        bot,
        live=args.live,
        buyback=not args.no_buyback,
        sell_deficit=not args.no_sell,
    )

    if args.json:
        print(json.dumps(result, default=json_default, indent=2))
    else:
        plan = result["plan"]
        print("Premium alignment plan:")
        for currency in ("BTC", "ETH"):
            print(
                f"  {currency}: premium={plan['premium_native'][currency]} "
                f"net_sold={plan['net_sold_native'][currency]} "
                f"buyback={plan['buyback_native'].get(currency, '0')} "
                f"sell={plan['sell_native'].get(currency, '0')}"
            )
        if result["buyback_actions"]:
            print("Buyback actions:")
            for action in result["buyback_actions"]:
                print(f"  {action.get('action')}: {action}")
        if result.get("sell_actions"):
            print("Deficit sell actions:")
            for action in result["sell_actions"]:
                print(f"  {action.get('action')}: {action}")
        if not result["buyback_actions"] and not result.get("sell_actions"):
            if not args.no_buyback and not args.no_sell:
                print("Aligned — net sold matches premium on exchange.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
