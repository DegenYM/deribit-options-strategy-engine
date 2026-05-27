from __future__ import annotations

import argparse
from pathlib import Path

from ..env_layout import find_repo_root
from ..wallet_ops import run_wallet_command
from .common import add_env_file_after_subcommand, render

WALLET_COMMANDS = frozenset({"trade-spot", "internal-transfer"})


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    trade_spot_parser = subparsers.add_parser(
        "trade-spot",
        help="Sell spot (BTC/ETH) for USDC/USDT — e.g. covered_call fee collection",
    )
    add_env_file_after_subcommand(trade_spot_parser)
    trade_spot_parser.add_argument(
        "--from-currency",
        required=True,
        choices=("BTC", "ETH"),
        help="Spot base currency to sell",
    )
    trade_spot_parser.add_argument(
        "--to",
        dest="to_currency",
        default="USDC",
        choices=("USDC", "USDT"),
        help="Quote stablecoin (default: USDC)",
    )
    trade_spot_parser.add_argument(
        "--instrument",
        dest="instrument_name",
        default=None,
        help="Override spot pair, e.g. BTC_USDC (default: <from>_<to>)",
    )
    trade_spot_parser.add_argument(
        "--amount",
        default=None,
        help="Amount in base currency; omit when using --all",
    )
    trade_spot_parser.add_argument(
        "--all",
        dest="sell_all",
        action="store_true",
        help="Sell all available base currency (aligned to exchange minimum)",
    )
    trade_spot_parser.add_argument(
        "--order-type",
        default="market",
        choices=("market", "limit"),
        help="Order type (default: market)",
    )
    trade_spot_parser.add_argument(
        "--label",
        default=None,
        help="Optional Deribit order label",
    )
    trade_spot_parser.add_argument(
        "--live",
        action="store_true",
        help="Submit order; default is dry-run preview",
    )
    trade_spot_parser.add_argument("--json", action="store_true", help="Emit JSON")

    transfer_parser = subparsers.add_parser(
        "internal-transfer",
        help="Internal transfer from strategy sub-account to investor fee_acc",
    )
    add_env_file_after_subcommand(transfer_parser)
    transfer_parser.add_argument(
        "--currency",
        required=True,
        choices=("BTC", "ETH", "USDC", "USDT"),
        help="Currency to transfer",
    )
    transfer_parser.add_argument(
        "--amount",
        required=True,
        help="Amount to transfer",
    )
    transfer_parser.add_argument(
        "--destination-id",
        type=int,
        default=None,
        help="Fee subaccount Deribit id (overrides FEE_SUBACCOUNT_ID / name lookup)",
    )
    transfer_parser.add_argument(
        "--nonce",
        default=None,
        help="Optional idempotency nonce (8-128 chars)",
    )
    transfer_parser.add_argument(
        "--live",
        action="store_true",
        help="Submit transfer; default is dry-run preview",
    )
    transfer_parser.add_argument("--json", action="store_true", help="Emit JSON")


def dispatch(args: argparse.Namespace) -> int | None:
    if args.command not in WALLET_COMMANDS:
        return None

    if args.command == "trade-spot" and not args.sell_all and not args.amount:
        raise SystemExit("trade-spot requires --amount or --all")

    repo_root = find_repo_root(Path.cwd())
    payload = run_wallet_command(
        command=args.command,
        investor=getattr(args, "investor", None),
        env_file=args.env_file,
        repo_root=repo_root,
        live=bool(args.live),
        json_output=bool(args.json),
        from_currency=args.from_currency if args.command == "trade-spot" else None,
        amount=args.amount if args.command == "trade-spot" else args.amount,
        to_currency=getattr(args, "to_currency", None),
        instrument_name=getattr(args, "instrument_name", None),
        order_type=getattr(args, "order_type", None),
        sell_all=getattr(args, "sell_all", False),
        label=getattr(args, "label", None),
        currency=args.currency if args.command == "internal-transfer" else None,
        destination_id=getattr(args, "destination_id", None),
        nonce=getattr(args, "nonce", None),
    )
    render(payload, args.json)
    return 0
