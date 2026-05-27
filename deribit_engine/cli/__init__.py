from __future__ import annotations

import argparse

from . import fee, frontend, investor, strategy, wallet
from .common import apply_investor_cli_args, configure_logging

__all__ = [
    "apply_investor_cli_args",
    "build_bot",
    "configure_logging",
    "main",
    "render",
]

from .common import build_bot, render  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bot", description="Deribit survival-first short put spread bot")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Account env file (legacy: repo-root .env or .env.<strategy>_sub)",
    )
    parser.add_argument(
        "--investor",
        metavar="ID",
        help="Investor id under config/investors/<ID> (uses accounts.toml)",
    )
    parser.add_argument(
        "--account",
        metavar="SLUG",
        help="Sub-account slug from accounts.toml when using --investor",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    strategy.register_parsers(subparsers)
    frontend.register_parsers(subparsers)
    fee.register_parsers(subparsers)
    investor.register_parsers(subparsers)
    wallet.register_parsers(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "env_file_after_cmd", None) is not None:
        args.env_file = args.env_file_after_cmd
    apply_investor_cli_args(args)
    configure_logging(args.verbose)

    for module in (investor, fee, frontend, strategy, wallet):
        code = module.dispatch(args)
        if code is not None:
            return code

    raise SystemExit(2)
