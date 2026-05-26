from __future__ import annotations

import argparse

from ..utils import parse_csv
from .common import add_env_file_after_subcommand


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    fe_parser = subparsers.add_parser(
        "frontend",
        help="Serve HTML dashboard at http://host:port (use --host 0.0.0.0 behind TLS for remote access)",
    )
    add_env_file_after_subcommand(fe_parser)
    fe_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1; 0.0.0.0 = all interfaces for LAN/VPS)",
    )
    fe_parser.add_argument("--port", type=int, default=8765, help="Bind port (default 8765)")
    fe_parser.add_argument(
        "--account-env-files",
        help="Comma-separated .env files to aggregate into one dashboard",
    )
    fe_parser.add_argument("--no-scheduler", action="store_true", help="Disable equity-snapshot background loop")
    fe_parser.add_argument("--snapshot-interval-sec", type=int, default=None, help="Override scheduler tick interval")
    fe_parser.add_argument("--log-level", default="info", help="uvicorn log level (default info)")
    fe_parser.add_argument(
        "--investor-portal",
        action="store_true",
        help="Redirect / to /investor.html (for external investor URLs; pair with --investor)",
    )


def dispatch(args: argparse.Namespace) -> int | None:
    if args.command != "frontend":
        return None

    from ..frontend_server import serve as serve_frontend

    serve_frontend(
        host=args.host,
        port=args.port,
        env_file=args.env_file,
        account_env_files=tuple(parse_csv(args.account_env_files)) if args.account_env_files else None,
        enable_scheduler=not args.no_scheduler,
        snapshot_interval_sec=args.snapshot_interval_sec,
        investor_portal=bool(getattr(args, "investor_portal", False)),
        log_level=args.log_level,
        skipped_accounts=getattr(args, "investor_skipped_accounts", None),
    )
    return 0
