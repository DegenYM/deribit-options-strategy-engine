from __future__ import annotations

import argparse

from ..admin_server.app import DEFAULT_ADMIN_PORT, DEFAULT_LOCAL_HOST


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "admin",
        help="Local admin console for every investor frontend (127.0.0.1 only)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_LOCAL_HOST,
        help="Bind address (default 127.0.0.1; public binds require --allow-public)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_ADMIN_PORT,
        help=f"Bind port (default {DEFAULT_ADMIN_PORT})",
    )
    parser.add_argument(
        "--allow-public",
        action="store_true",
        help="Allow a non-loopback bind (do not put this hostname on Cloudflare Access)",
    )
    parser.add_argument("--log-level", default="info", help="uvicorn log level (default info)")


def dispatch(args: argparse.Namespace) -> int | None:
    if args.command != "admin":
        return None

    from ..admin_server import serve_admin

    serve_admin(
        host=args.host,
        port=int(args.port),
        allow_public=bool(getattr(args, "allow_public", False)),
        log_level=args.log_level,
    )
    return 0
