from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..env_layout import find_repo_root, load_investor_manifest
from ..utils import to_decimal
from .common import add_env_file_after_subcommand, render

FEE_COMMANDS = frozenset(
    {
        "fee-snapshot",
        "fee-settle",
        "fee-settle-period",
        "fee-status",
        "fee-balance",
        "fee-flow-report",
        "fee-report",
    }
)


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    fee_snap_parser = subparsers.add_parser(
        "fee-snapshot",
        help="Capture investor-level NAV_perf snapshot for performance-fee billing",
    )
    add_env_file_after_subcommand(fee_snap_parser)
    fee_snap_parser.add_argument(
        "--kind",
        default="manual",
        help="Snapshot kind stored in fee ledger (default: manual)",
    )
    fee_snap_parser.add_argument("--notes", default=None, help="Optional note")
    fee_snap_parser.add_argument(
        "--force-bootstrap",
        action="store_true",
        help="Re-fetch transaction log and overwrite HWM / flow baseline (fixes bad first run)",
    )
    fee_snap_parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Skip auto-fetching cumulative deposits to set initial HWM on first run",
    )
    fee_snap_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_settle_parser = subparsers.add_parser(
        "fee-settle",
        help="Settle a quarter: performance fee + HWM update",
    )
    add_env_file_after_subcommand(fee_settle_parser)
    fee_settle_parser.add_argument(
        "--period",
        required=True,
        metavar="YYYY-QN",
        help="Quarter to settle, e.g. 2026-Q1",
    )
    fee_settle_parser.add_argument(
        "--net-flow-usdc",
        default=None,
        help="Override net subscription for the quarter (default: Deribit transaction log)",
    )
    fee_settle_parser.add_argument(
        "--fee-payment-usdc",
        default=None,
        help=(
            "Deribit withdrawals that paid fees off-exchange (USDC); excluded from capital "
            "redemption when net flow is auto-fetched from transaction log"
        ),
    )
    fee_settle_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing settlement for the same period",
    )
    fee_settle_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_period_parser = subparsers.add_parser(
        "fee-settle-period",
        help="Settle fees for a custom time window and write PDF/MD/CSV report",
    )
    add_env_file_after_subcommand(fee_period_parser)
    fee_period_parser.add_argument(
        "--to",
        required=True,
        metavar="WHEN",
        help="Period end: YYYY-MM-DD, ISO-8601, unix ms, or now",
    )
    fee_period_parser.add_argument(
        "--from",
        dest="from_ts",
        default=None,
        metavar="WHEN",
        help="Period start (default: latest snapshot strictly before --to). Same formats as --to",
    )
    fee_period_parser.add_argument(
        "--net-flow-usdc",
        default=None,
        help="Override net subscription for the window (default: Deribit transaction log)",
    )
    fee_period_parser.add_argument(
        "--fee-payment-usdc",
        default=None,
        help=(
            "Deribit withdrawals that paid fees off-exchange (USDC); excluded from capital "
            "redemption when net flow is auto-fetched from transaction log"
        ),
    )
    fee_period_parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Compute report only; do not save settlement or update HWM",
    )
    fee_period_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing settlement for the same auto period id",
    )
    fee_period_parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip writing PDF/MD/CSV (JSON output only)",
    )
    fee_period_parser.add_argument(
        "--format",
        default="all",
        choices=("both", "all", "pdf", "md", "csv"),
        help="Report formats when not using --no-report (default: all)",
    )
    fee_period_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_status_parser = subparsers.add_parser(
        "fee-status",
        help="Show HWM, latest NAV snapshot, and past settlements",
    )
    add_env_file_after_subcommand(fee_status_parser)
    fee_status_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_balance_parser = subparsers.add_parser(
        "fee-balance",
        help="Show live wallet balance for the investor fee-collection sub-account",
    )
    add_env_file_after_subcommand(fee_balance_parser)
    fee_balance_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_flow_parser = subparsers.add_parser(
        "fee-flow-report",
        help="Show deposit/withdrawal breakdown from Deribit transaction log (read-only)",
    )
    add_env_file_after_subcommand(fee_flow_parser)
    fee_flow_parser.add_argument(
        "--from",
        dest="from_ts",
        default=None,
        metavar="WHEN",
        help="Period start for line-by-line preview (same formats as fee-settle-period --from)",
    )
    fee_flow_parser.add_argument(
        "--to",
        dest="to_ts",
        default=None,
        metavar="WHEN",
        help="Period end for line-by-line preview (default: now when --from is set)",
    )
    fee_flow_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_report_parser = subparsers.add_parser(
        "fee-report",
        help="Generate markdown fee report (initial bootstrap or quarterly settlement)",
    )
    add_env_file_after_subcommand(fee_report_parser)
    fee_report_parser.add_argument(
        "--kind",
        required=True,
        choices=("initial", "settlement"),
        help="initial = strategy start baseline; settlement = quarterly fee settlement",
    )
    fee_report_parser.add_argument(
        "--period",
        default=None,
        metavar="YYYY-QN",
        help="Required for --kind settlement (e.g. 2026-Q1 or fee-settle-period period id)",
    )
    fee_report_parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Output base path (.md and/or .pdf; default: data/fee_ledger/<id>/reports/...)",
    )
    fee_report_parser.add_argument(
        "--format",
        default="both",
        choices=("both", "all", "pdf", "md", "csv"),
        help="both=PDF+MD, all=PDF+MD+CSV, csv=flows+summary CSV only (default: both)",
    )
    fee_report_parser.add_argument("--json", action="store_true", help="Emit JSON")


def dispatch(args: argparse.Namespace) -> int | None:
    if args.command not in FEE_COMMANDS:
        return None

    if args.command == "fee-snapshot":
        from ..investor_nav_snapshot import capture_investor_nav, store_nav_capture

        if not args.investor:
            raise SystemExit("fee-snapshot requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        capture = capture_investor_nav(args.investor, repo_root=repo_root)
        row_id, bootstrap = store_nav_capture(
            capture,
            repo_root=repo_root,
            snapshot_kind=args.kind,
            notes=args.notes,
            bootstrap_hwm=not args.no_bootstrap,
            force_bootstrap=bool(args.force_bootstrap),
        )
        payload = {
            "action": "fee-snapshot",
            "snapshot_id": row_id,
            "investor_id": capture.investor_id,
            "ts_ms": capture.ts_ms,
            "total_equity_usdc": str(capture.total_equity_usdc),
            "collateral_spot_usdc": str(capture.collateral_spot_usdc),
            "nav_perf": str(capture.nav_perf),
            "aum_mgmt": str(capture.aum_mgmt),
            "index_btc_usd": str(capture.index_btc_usd),
            "index_eth_usd": str(capture.index_eth_usd),
            "equity_by_book": {k: str(v) for k, v in capture.equity_by_book.items()},
            "hwm_bootstrap": bootstrap,
        }
        if isinstance(bootstrap, dict) and bootstrap.get("report_path"):
            payload["report_path"] = bootstrap["report_path"]
            if bootstrap.get("report_markdown_path"):
                payload["report_markdown_path"] = bootstrap["report_markdown_path"]
        render(payload, args.json)
        return 0

    if args.command == "fee-settle":
        from ..investor_nav_snapshot import settle_quarter

        if not args.investor:
            raise SystemExit("fee-settle requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        net_flow = to_decimal(args.net_flow_usdc) if args.net_flow_usdc is not None else None
        fee_payment = to_decimal(args.fee_payment_usdc) if args.fee_payment_usdc is not None else None
        result = settle_quarter(
            args.investor,
            args.period,
            net_flow_usdc=net_flow,
            fee_payment_usdc=fee_payment,
            repo_root=repo_root,
            force=bool(args.force),
        )
        render({"action": "fee-settle", **result}, args.json)
        return 0

    if args.command == "fee-settle-period":
        from ..investor_nav_snapshot import parse_fee_timestamp, settle_period

        if not args.investor:
            raise SystemExit("fee-settle-period requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        end_ms = parse_fee_timestamp(args.to, boundary="end")
        start_ms = parse_fee_timestamp(args.from_ts, boundary="start") if args.from_ts else None
        net_flow = to_decimal(args.net_flow_usdc) if args.net_flow_usdc is not None else None
        fee_payment = to_decimal(args.fee_payment_usdc) if args.fee_payment_usdc is not None else None
        write_pdf = args.format in {"both", "all", "pdf"}
        write_md = args.format in {"both", "all", "md"}
        write_csv = args.format in {"all", "csv"}
        result = settle_period(
            args.investor,
            end_ms=end_ms,
            start_ms=start_ms,
            net_flow_usdc=net_flow,
            fee_payment_usdc=fee_payment,
            repo_root=repo_root,
            persist=not args.no_persist,
            force=bool(args.force),
            write_report=False,
        )
        payload: dict[str, Any] = {"action": "fee-settle-period", **result}
        if not args.no_report:
            from ..investor_fee_report import write_settlement_fee_report

            period_flow_lines = result.pop("period_flow_lines", None)
            report_out = write_settlement_fee_report(
                args.investor,
                result["period"],
                repo_root=repo_root,
                settlement_payload=result,
                period_flow_lines=period_flow_lines,
                write_pdf=write_pdf,
                write_markdown=write_md,
                write_csv=write_csv,
            )
            payload["report_path"] = str(report_out.primary)
            if report_out.pdf is not None:
                payload["report_pdf_path"] = str(report_out.pdf)
            if report_out.markdown is not None:
                payload["report_markdown_path"] = str(report_out.markdown)
            if report_out.flows_csv is not None:
                payload["report_flows_csv_path"] = str(report_out.flows_csv)
            if report_out.summary_csv is not None:
                payload["report_summary_csv_path"] = str(report_out.summary_csv)
            if report_out.trades_csv is not None:
                payload["report_trades_csv_path"] = str(report_out.trades_csv)
        render(payload, args.json)
        return 0

    if args.command == "fee-status":
        from ..investor_nav_snapshot import fee_status

        if not args.investor:
            raise SystemExit("fee-status requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        render({"action": "fee-status", **fee_status(args.investor, repo_root=repo_root)}, args.json)
        return 0

    if args.command == "fee-balance":
        raise SystemExit(
            "fee-balance is deprecated: fee sub-accounts are no longer used. "
            "Investors pay via external addresses in config/platform/fee-payout-addresses.toml. "
            "See docs/investor-fee-disclosure-zh-TW.md section 5."
        )

    if args.command == "fee-flow-report":
        from ..investor_cash_flow import (
            fetch_cumulative_net_flow_usdc,
            fetch_subscription_flow_lines,
            flow_report_dict,
            period_flow_report_dict,
        )
        from ..investor_nav_snapshot import capture_investor_nav, parse_fee_timestamp
        from ..utils import utc_now_ms

        if not args.investor:
            raise SystemExit("fee-flow-report requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        manifest = load_investor_manifest(args.investor, repo_root=repo_root)
        capture = capture_investor_nav(args.investor, repo_root=repo_root)
        index_by_ccy = {
            "BTC": capture.index_btc_usd,
            "ETH": capture.index_eth_usd,
            "USDC": to_decimal("1"),
        }
        if args.from_ts is not None:
            start_ms = parse_fee_timestamp(args.from_ts, boundary="start")
            end_ms = parse_fee_timestamp(args.to_ts, boundary="end") if args.to_ts is not None else utc_now_ms()
            lines = fetch_subscription_flow_lines(
                manifest.root,
                repo_root=repo_root,
                index_by_ccy=index_by_ccy,
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
            )
            payload = {
                "action": "fee-flow-report",
                "mode": "period",
                "investor_id": manifest.investor_id,
                **period_flow_report_dict(lines, start_timestamp_ms=start_ms, end_timestamp_ms=end_ms),
            }
        else:
            flow = fetch_cumulative_net_flow_usdc(
                manifest.root,
                repo_root=repo_root,
                index_by_ccy=index_by_ccy,
            )
            payload = {
                "action": "fee-flow-report",
                "mode": "cumulative",
                "investor_id": manifest.investor_id,
                **flow_report_dict(flow, index_by_ccy=index_by_ccy),
            }
        render(payload, args.json)
        return 0

    if args.command == "fee-report":
        from ..investor_fee_report import write_initial_fee_report, write_settlement_fee_report

        if not args.investor:
            raise SystemExit("fee-report requires --investor <ID>")
        if args.kind == "settlement" and not args.period:
            raise SystemExit("fee-report --kind settlement requires --period YYYY-QN")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        output = Path(args.output) if args.output else None
        write_pdf = args.format in {"both", "all", "pdf"}
        write_md = args.format in {"both", "all", "md"}
        write_csv = args.format in {"all", "csv"}
        if args.kind == "initial":
            out = write_initial_fee_report(
                args.investor,
                repo_root=repo_root,
                output_path=output,
                write_pdf=write_pdf,
                write_markdown=write_md,
                write_csv=write_csv,
            )
        else:
            out = write_settlement_fee_report(
                args.investor,
                args.period,
                repo_root=repo_root,
                output_path=output,
                write_pdf=write_pdf,
                write_markdown=write_md,
                write_csv=write_csv,
            )
        payload = {
            "action": "fee-report",
            "kind": args.kind,
            "report_path": str(out.primary),
        }
        if out.pdf is not None:
            payload["report_pdf_path"] = str(out.pdf)
        if out.markdown is not None:
            payload["report_markdown_path"] = str(out.markdown)
        if out.flows_csv is not None:
            payload["report_flows_csv_path"] = str(out.flows_csv)
        if out.summary_csv is not None:
            payload["report_summary_csv_path"] = str(out.summary_csv)
        if out.trades_csv is not None:
            payload["report_trades_csv_path"] = str(out.trades_csv)
        render(payload, args.json)
        return 0

    raise SystemExit(2)
