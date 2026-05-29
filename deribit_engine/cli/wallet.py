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
        help="Trade spot: sell BTC/ETH→USDC/USDT or buy BTC/ETH with USDC/USDT",
    )
    add_env_file_after_subcommand(trade_spot_parser)
    trade_spot_parser.add_argument(
        "--from-currency",
        required=True,
        choices=("BTC", "ETH", "USDC", "USDT"),
        help="Currency to spend (BTC/ETH sell, or USDC/USDT buy)",
    )
    trade_spot_parser.add_argument(
        "--to",
        dest="to_currency",
        required=True,
        choices=("BTC", "ETH", "USDC", "USDT"),
        help="Currency to receive",
    )
    trade_spot_parser.add_argument(
        "--instrument",
        dest="instrument_name",
        default=None,
        help="Override spot pair, e.g. BTC_USDC (default: <base>_<quote>)",
    )
    trade_spot_parser.add_argument(
        "--amount",
        default=None,
        help="Amount in --from-currency (base for sell, quote spend for buy)",
    )
    trade_spot_parser.add_argument(
        "--all",
        dest="sell_all",
        action="store_true",
        help="Use full available --from-currency balance",
    )
    trade_spot_parser.add_argument(
        "--order-type",
        default="market",
        choices=("market", "limit"),
        help="Order type: market=taker; limit=maker (sell@ask, buy@bid)",
    )
    trade_spot_parser.add_argument(
        "--price",
        default=None,
        help="Limit order price in quote currency (default for limit: best ask)",
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
        limit_price=getattr(args, "price", None),
        sell_all=getattr(args, "sell_all", False),
        label=getattr(args, "label", None),
        currency=args.currency if args.command == "internal-transfer" else None,
        destination_id=getattr(args, "destination_id", None),
        nonce=getattr(args, "nonce", None),
    )
    render(payload, args.json)
    return 0
