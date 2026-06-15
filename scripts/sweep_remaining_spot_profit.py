#!/usr/bin/env python3
"""Sell remaining covered-call spot profit (BTC/ETH premium) to USDT and update state."""

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
from deribit_engine.exceptions import ConfigurationError, TransientExchangeError
from deribit_engine.profit_sweep_ops import run_remaining_profit_sweeps
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
        "--group-id",
        default=None,
        help="Sweep one closed trade group only",
    )
    parser.add_argument(
        "--reconcile-only",
        action="store_true",
        help="Only sync profit_sweep_* fields from Deribit order labels; do not place orders",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Submit spot sell orders; default is dry-run preview",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.investor:
        from deribit_engine.cli.common import apply_investor_cli_args

        ns = argparse.Namespace(
            command="profit-sweep",
            investor=args.investor,
            account=args.account,
            env_file=args.env_file,
            env_file_after_cmd=None,
        )
        apply_investor_cli_args(ns)
        args.env_file = ns.env_file

    cfg = load_config(args.env_file, require_private=args.live)
    if cfg.option_strategy != "covered_call":
        raise SystemExit(
            f"profit sweep requires covered_call strategy (got {cfg.option_strategy!r}); "
            "pass the covered_call account --env-file"
        )

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
        summary = run_remaining_profit_sweeps(
            bot,
            live=args.live,
            group_id=args.group_id,
            reconcile_only=args.reconcile_only,
        )
    except TransientExchangeError as exc:
        raise SystemExit(
            f"Deribit rate limited or temporarily unavailable: {exc}\n"
            "Wait 30–60s and retry, or set DERIBIT_MIN_REQUEST_INTERVAL_SEC=0.25"
        ) from exc
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    payload = summary.to_dict()
    if args.json:
        print(json.dumps(payload, default=json_default, ensure_ascii=False, indent=2))
    else:
        mode = "LIVE" if args.live else "DRY RUN"
        if args.reconcile_only:
            mode = f"{mode} reconcile-only"
        print(
            f"[{mode}] candidates={len(summary.candidates)} reconciled={summary.reconciled} scheduled={summary.scheduled}"
        )
        if summary.blocked_oversweep:
            print(f"  blocked_oversweep={summary.blocked_oversweep} (exchange already fully swept)")
        if not args.live and not args.reconcile_only:
            print("DRY RUN: no orders submitted; state file not modified")
        elif args.reconcile_only:
            print("Reconcile-only: updated profit_sweep_* from Deribit labels only")
        for row in summary.candidates:
            print(
                f"  {row.group_id} {row.currency} kind={row.kind} "
                f"to_sweep={row.to_sweep_native} remaining={row.remaining_native} "
                f"status={row.profit_sweep_status or '(none)'}"
            )
        for action in summary.actions:
            print(
                f"  action={action.get('action')} group={action.get('group_id')} "
                f"amount={action.get('amount')} status={action.get('profit_sweep_status') or action.get('reason')}"
            )
        if summary.saved:
            print(f"state saved: {cfg.state_file}")
        elif not args.live:
            print("(state unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
