from __future__ import annotations

import argparse
import sys

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

# Display groups for `./bot help` (commands not listed fall into "Other").
_HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Strategy / trading",
        (
            "ping",
            "status",
            "report",
            "scan",
            "enter-best",
            "manage",
            "run",
            "panic-close",
            "close-position",
            "cancel",
            "stress-current",
            "backtest",
        ),
    ),
    (
        "Covered call ops",
        (
            "profit-sweep",
            "spot-restore",
        ),
    ),
    (
        "Wallet",
        (
            "trade-spot",
            "internal-transfer",
        ),
    ),
    (
        "Fees",
        (
            "fee-snapshot",
            "fee-settle",
            "fee-settle-period",
            "fee-status",
            "fee-balance",
            "fee-flow-report",
            "fee-report",
        ),
    ),
    (
        "Investor / platform",
        ("investor",),
    ),
    (
        "Dashboard",
        ("frontend",),
    ),
    (
        "Journal / diagnostics",
        (
            "user-trades",
            "backfill-trade-journal",
            "telegram-test",
        ),
    ),
)


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _subcommand_map(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = _subparsers_action(parser)
    if action is None:
        return {}
    return dict(action.choices or {})


def _subcommand_help_by_name(parser: argparse.ArgumentParser) -> dict[str, str]:
    """Map command name → short help (from add_parser help=)."""
    action = _subparsers_action(parser)
    if action is None:
        return {}
    out: dict[str, str] = {}
    for choice in getattr(action, "_choices_actions", []) or []:
        name = str(getattr(choice, "dest", "") or "")
        if not name:
            continue
        help_text = " ".join(str(getattr(choice, "help", "") or "").split())
        if not help_text:
            sub = (action.choices or {}).get(name)
            help_text = " ".join(str(getattr(sub, "description", "") or "").split()) if sub else ""
        out[name] = help_text
    return out


def format_command_catalog(parser: argparse.ArgumentParser) -> str:
    """Human-readable list of all top-level bot commands."""
    choices = _subcommand_map(parser)
    helps = _subcommand_help_by_name(parser)
    # Exclude the meta `help` command from the catalog body.
    names = [name for name in choices if name != "help"]
    grouped: set[str] = set()
    lines = [
        "Deribit options strategy bot",
        "",
        "Usage:",
        "  ./bot [--investor ID --account SLUG] <command> [options]",
        "  ./bot help",
        "  ./bot help <command>",
        "",
    ]
    for title, members in _HELP_GROUPS:
        present = [name for name in members if name in choices]
        if not present:
            continue
        lines.append(f"{title}:")
        width = max(len(name) for name in present)
        for name in present:
            grouped.add(name)
            help_text = helps.get(name, "")
            lines.append(f"  {name.ljust(width)}  {help_text}".rstrip())
        lines.append("")

    other = sorted(name for name in names if name not in grouped)
    if other:
        lines.append("Other:")
        width = max(len(name) for name in other)
        for name in other:
            help_text = helps.get(name, "")
            lines.append(f"  {name.ljust(width)}  {help_text}".rstrip())
        lines.append("")

    lines.extend(
        [
            "Tips:",
            "  ./bot <command> -h     detailed flags for one command",
            "  ./bot help investor    list investor subcommands",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def format_nested_command_help(parser: argparse.ArgumentParser, command: str) -> str | None:
    choices = _subcommand_map(parser)
    sub = choices.get(command)
    if sub is None:
        return None
    nested = _subcommand_map(sub)
    if not nested:
        return sub.format_help()
    nested_helps = _subcommand_help_by_name(sub)
    top_helps = _subcommand_help_by_name(parser)
    width = max(len(name) for name in nested)
    lines = [
        f"bot {command}",
        "",
        top_helps.get(command, "") or " ".join(str(sub.description or "").split()),
        "",
        "Subcommands:",
    ]
    for name in sorted(nested):
        help_text = nested_helps.get(name, "")
        lines.append(f"  {name.ljust(width)}  {help_text}".rstrip())
    lines.extend(
        [
            "",
            f"Details: ./bot {command} <subcommand> -h",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot",
        description="Deribit options strategy bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `./bot help` for a full command list.",
    )
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

    help_parser = subparsers.add_parser(
        "help",
        help="List all bot commands (or show help for one command)",
        description="List all bot commands, or show details / subcommands for one command.",
    )
    help_parser.add_argument(
        "help_target",
        nargs="?",
        default=None,
        metavar="COMMAND",
        help="Optional command name (e.g. investor, spot-restore)",
    )

    strategy.register_parsers(subparsers)
    frontend.register_parsers(subparsers)
    fee.register_parsers(subparsers)
    investor.register_parsers(subparsers)
    wallet.register_parsers(subparsers)

    # Keep `-h` epilog pointing at the catalog; rebuild after all commands exist.
    parser.epilog = "Run `./bot help` for a full command list grouped by category."
    return parser


def _dispatch_help(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    target = getattr(args, "help_target", None)
    if not target:
        sys.stdout.write(format_command_catalog(parser))
        return 0
    nested = format_nested_command_help(parser, target)
    if nested is not None:
        sys.stdout.write(nested)
        return 0
    choices = _subcommand_map(parser)
    if target in choices:
        choices[target].print_help()
        return 0
    known = ", ".join(sorted(name for name in choices if name != "help"))
    sys.stderr.write(f"Unknown command {target!r}. Known commands: {known}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    argv_list = list(sys.argv[1:] if argv is None else argv)
    # Bare `./bot` → full catalog (friendlier than argparse "required" error).
    if not argv_list:
        sys.stdout.write(format_command_catalog(parser))
        return 0

    args = parser.parse_args(argv)
    if args.command == "help":
        return _dispatch_help(parser, args)

    if getattr(args, "env_file_after_cmd", None) is not None:
        args.env_file = args.env_file_after_cmd
    apply_investor_cli_args(args)
    configure_logging(args.verbose)

    for module in (investor, fee, frontend, strategy, wallet):
        code = module.dispatch(args)
        if code is not None:
            return code

    raise SystemExit(2)
