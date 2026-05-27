from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from ..client import DeribitClient
from ..config import assert_trading_account, load_config
from ..engine import DeribitOptionTrialBot
from ..env_layout import find_repo_root, load_investor_manifest
from ..exceptions import ConfigurationError
from ..utils import json_default


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_bot(args) -> DeribitOptionTrialBot:
    private_commands = {
        "status",
        "enter-best",
        "manage",
        "run",
        "panic-close",
        "close-position",
        "cancel",
        "trade-spot",
        "internal-transfer",
    }
    require_private = args.command in private_commands or (args.command == "scan" and getattr(args, "live", False))
    config = load_config(
        args.env_file,
        require_private=require_private,
        strategy_override=getattr(args, "strategy", None),
    )
    assert_trading_account(config)
    client = DeribitClient(config)
    return DeribitOptionTrialBot(config, client)


def parse_instrument_names(raw_values: list[str] | None) -> list[str]:
    names: list[str] = []
    for raw in raw_values or []:
        for part in str(raw).split(","):
            name = part.strip()
            if name:
                names.append(name)
    return list(dict.fromkeys(names))


def render(data, json_output: bool) -> None:
    if json_output:
        print(json.dumps(data, default=json_default, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(data, default=json_default, ensure_ascii=False, indent=2, sort_keys=True))


def apply_investor_cli_args(args: argparse.Namespace) -> None:
    if getattr(args, "command", None) == "investor":
        return
    investor = getattr(args, "investor", None)
    if not investor:
        return
    repo_root = find_repo_root(Path.cwd())
    try:
        manifest = load_investor_manifest(investor, repo_root=repo_root)
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    account_slug = getattr(args, "account", None)
    if args.command == "frontend":
        if not getattr(args, "account_env_files", None):
            skipped = manifest.accounts_without_creds()
            files = manifest.account_env_files(require_creds=True)
            if skipped:
                slugs = ", ".join(account.slug for account in skipped)
                logging.getLogger(__name__).warning(
                    "Skipping enabled account(s) without DERIBIT_CLIENT_ID/SECRET: %s",
                    slugs,
                )
            if not files:
                raise SystemExit(f"No enabled accounts with API credentials in {manifest.root / 'accounts.toml'}")
            args.account_env_files = ",".join(str(path) for path in files)
            args.investor_skipped_accounts = tuple(
                {
                    "slug": account.slug,
                    "strategy": account.strategy,
                    "display_name": account.display_name or account.slug,
                    "reason": "missing_api_creds",
                }
                for account in skipped
            )
        if account_slug:
            args.env_file = str(manifest.env_for_slug(account_slug))
        elif not getattr(args, "env_file_after_cmd", None) and args.env_file == ".env":
            args.env_file = str(manifest.account_env_files()[0])
        if not getattr(args, "investor_portal", False):
            args.investor_portal = True
        return

    if not account_slug:
        if args.command in {
            "backfill-trade-journal",
            "frontend",
            "fee-snapshot",
            "fee-settle",
            "fee-settle-period",
            "fee-status",
            "fee-balance",
            "fee-flow-report",
            "fee-report",
        }:
            return
        slugs = ", ".join(account.slug for account in manifest.accounts)
        raise SystemExit(f"--account <slug> is required with --investor for `{args.command}` (known: {slugs})")
    args.env_file = str(manifest.env_for_slug(account_slug))


def add_env_file_after_subcommand(sub: argparse.ArgumentParser) -> None:
    """Allow `./bot scan --env-file path` as well as `./bot --env-file path scan`."""

    sub.add_argument(
        "--env-file",
        dest="env_file_after_cmd",
        default=None,
        metavar="PATH",
        help="Env file (same as global --env-file; use when passing after the subcommand)",
    )
