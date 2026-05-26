from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .backtest import BacktestConfig, run_backtest
from .backtest_data import BacktestCache, BacktestDataClient
from .client import DeribitClient
from .config import assert_trading_account, load_config
from .current_stress import compute_current_stress, render_current_stress_md
from .engine import DeribitOptionTrialBot
from .env_layout import find_repo_root, load_investor_manifest
from .exceptions import ConfigurationError
from .param_scan import run_param_scan
from .report_md import render_backtest_report_md
from .utils import json_default, parse_csv, to_decimal


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_bot(args) -> DeribitOptionTrialBot:
    private_commands = {"status", "enter-best", "manage", "run", "panic-close", "close-position", "cancel"}
    require_private = args.command in private_commands or (args.command == "scan" and getattr(args, "live", False))
    config = load_config(
        args.env_file,
        require_private=require_private,
        strategy_override=getattr(args, "strategy", None),
    )
    assert_trading_account(config)
    client = DeribitClient(config)
    return DeribitOptionTrialBot(config, client)


def _parse_instrument_names(raw_values: list[str] | None) -> list[str]:
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


def _apply_investor_cli_args(args: argparse.Namespace) -> None:
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
        # External investor URLs should land on investor.html, not the ops index.
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
            "fee-flow-report",
            "fee-report",
        }:
            return
        slugs = ", ".join(account.slug for account in manifest.accounts)
        raise SystemExit(f"--account <slug> is required with --investor for `{args.command}` (known: {slugs})")
    args.env_file = str(manifest.env_for_slug(account_slug))


def _add_env_file_after_subcommand(sub: argparse.ArgumentParser) -> None:
    """Allow `./bot scan --env-file path` as well as `./bot --env-file path scan`."""

    sub.add_argument(
        "--env-file",
        dest="env_file_after_cmd",
        default=None,
        metavar="PATH",
        help="Env file (same as global --env-file; use when passing after the subcommand)",
    )


def main(argv: list[str] | None = None) -> int:
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

    ping_parser = subparsers.add_parser("ping", help="Ping Deribit public API")
    _add_env_file_after_subcommand(ping_parser)
    ping_parser.add_argument("--json", action="store_true", help="Emit JSON")

    telegram_parser = subparsers.add_parser(
        "telegram-test",
        help="Send a test message to the configured Telegram chat",
    )
    _add_env_file_after_subcommand(telegram_parser)
    telegram_parser.add_argument("--json", action="store_true", help="Emit JSON")

    status_parser = subparsers.add_parser("status", help="Show portfolio state, trade groups, orders, and positions")
    _add_env_file_after_subcommand(status_parser)
    status_parser.add_argument("--json", action="store_true", help="Emit JSON")

    report_parser = subparsers.add_parser("report", help="Show realized spread report from local state")
    _add_env_file_after_subcommand(report_parser)
    report_parser.add_argument("--days", type=int, default=30, help="Rolling window in days; 0 means all history")
    report_parser.add_argument("--json", action="store_true", help="Emit JSON")

    backtest_parser = subparsers.add_parser("backtest", help="Run public-data historical backtest + black-swan overlay")
    _add_env_file_after_subcommand(backtest_parser)
    backtest_parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    backtest_parser.add_argument("--end", default="today", help="YYYY-MM-DD or 'today'")
    backtest_parser.add_argument("--resolution", default="1D", help="TradingView resolution (e.g. 1D, 60)")
    backtest_parser.add_argument("--cache-root", default="data/backtest_cache", help="Cache directory for public data")
    backtest_parser.add_argument(
        "--report", default="docs/backtest/backtest_black_swan.md", help="Output markdown path"
    )
    backtest_parser.add_argument(
        "--scan-params", action="store_true", help="Run baseline/conservative/profit-seek scan"
    )
    backtest_parser.add_argument(
        "--auto-fallback-window-days", type=int, default=30, help="If 0 trades, fallback to last N days (0 disables)"
    )
    backtest_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    backtest_parser.add_argument(
        "--json", action="store_true", help="Emit JSON (also writes report unless --report is empty)"
    )

    scan_parser = subparsers.add_parser("scan", help="Scan option strategy candidates")
    _add_env_file_after_subcommand(scan_parser)
    scan_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    scan_parser.add_argument(
        "--strategy",
        help="Override OPTION_STRATEGY for this scan: naked_short, bull_put_spread, covered_call",
    )
    scan_parser.add_argument("--top-n", type=int, help="Number of candidates to return")
    scan_parser.add_argument("--json", action="store_true", help="Emit JSON")

    enter_parser = subparsers.add_parser("enter-best", help="Preview or enter the best spread candidate")
    _add_env_file_after_subcommand(enter_parser)
    enter_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    enter_parser.add_argument("--live", action="store_true", help="Actually place orders")
    enter_parser.add_argument("--json", action="store_true", help="Emit JSON")

    manage_parser = subparsers.add_parser("manage", help="Run one portfolio management cycle")
    _add_env_file_after_subcommand(manage_parser)
    manage_parser.add_argument("--live", action="store_true", help="Actually place hedge/exit/roll orders")
    manage_parser.add_argument("--json", action="store_true", help="Emit JSON")

    run_parser = subparsers.add_parser("run", help="Run repeated manage+enter cycles")
    _add_env_file_after_subcommand(run_parser)
    run_parser.add_argument("--live", action="store_true", help="Actually place orders")
    run_parser.add_argument("--cycles", type=int, default=1, help="Number of cycles to run; 0 means forever")
    run_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    run_parser.add_argument("--json", action="store_true", help="Emit JSON")

    panic_parser = subparsers.add_parser("panic-close", help="Cancel orders and flatten option/perp risk")
    _add_env_file_after_subcommand(panic_parser)
    panic_parser.add_argument("--live", action="store_true", help="Actually place closing orders")
    panic_parser.add_argument("--json", action="store_true", help="Emit JSON")

    close_parser = subparsers.add_parser(
        "close-position",
        help="Close specific option or perp positions (use sub-account --env-file)",
    )
    _add_env_file_after_subcommand(close_parser)
    close_parser.add_argument(
        "--instrument",
        action="append",
        default=None,
        metavar="NAME",
        help="Contract to close; repeat or comma-separate (e.g. BTC_USDC-27MAR26-90000-P)",
    )
    close_parser.add_argument(
        "--list",
        action="store_true",
        help="List non-zero positions only (dry-run; ignores --instrument)",
    )
    close_parser.add_argument("--live", action="store_true", help="Actually place closing orders")
    close_parser.add_argument(
        "--order-type",
        choices=["market", "limit"],
        default="market",
        help="market: perp via close_position, option via reduce-only market; "
        "limit: option IOC limit with retry (default market)",
    )
    close_parser.add_argument(
        "--amount",
        default=None,
        metavar="QTY",
        help="Partial close size in contracts; default closes full position",
    )
    close_parser.add_argument("--json", action="store_true", help="Emit JSON")

    cancel_parser = subparsers.add_parser("cancel", help="Cancel an order by order_id")
    _add_env_file_after_subcommand(cancel_parser)
    cancel_parser.add_argument("--order-id", required=True, help="Deribit order id")
    cancel_parser.add_argument("--json", action="store_true", help="Emit JSON")

    stress_parser = subparsers.add_parser("stress-current", help="Stress test current live positions (uses index)")
    _add_env_file_after_subcommand(stress_parser)
    stress_parser.add_argument(
        "--shocks", default="0.10,0.20,0.30,0.40,0.50,0.60", help="Comma-separated magnitudes, e.g. 0.1,0.2"
    )
    stress_parser.add_argument("--report", default="docs/backtest/current_black_swan.md", help="Output markdown path")
    stress_parser.add_argument("--json", action="store_true", help="Emit JSON")

    trades_parser = subparsers.add_parser(
        "user-trades",
        help="Query fills: by wallet currency (get_user_trades_by_currency), by contract (--instrument), or transaction log",
    )
    _add_env_file_after_subcommand(trades_parser)
    trades_parser.add_argument(
        "--currency",
        default=None,
        metavar="CCY",
        help="Wallet currency for by-currency or for --from-transaction-log: BTC, ETH, USDC … "
        "Linear options (BTC_USDC-*, ETH_USDC-*) settle under USDC. Omit if only --instrument.",
    )
    trades_parser.add_argument(
        "--instrument",
        default=None,
        metavar="NAME",
        help="Full contract name, e.g. BTC_USDC-27MAR26-90000-P (private/get_user_trades_by_instrument)",
    )
    trades_parser.add_argument("--count", type=int, default=50, help="Max trades (1–1000, default 50)")
    trades_parser.add_argument(
        "--subaccount-id",
        type=int,
        default=None,
        metavar="ID",
        help="Subaccount user id (main-account API key only)",
    )
    trades_parser.add_argument(
        "--historical",
        action="store_true",
        help="Use historical index (older than ~24h); excludes very recent fills",
    )
    trades_parser.add_argument(
        "--kind",
        default=None,
        help="With by-currency only: instrument kind filter — option, future, spot, any, … (omit = all kinds)",
    )
    trades_parser.add_argument(
        "--sorting",
        default=None,
        help="Optional: asc | desc | default (omit for Deribit default ordering)",
    )
    trades_parser.add_argument(
        "--recent-only",
        action="store_true",
        help="Only use the rolling ~24h recent trades index (do not auto-retry with historical=true)",
    )
    trades_parser.add_argument(
        "--from-transaction-log",
        action="store_true",
        help="Use private/get_transaction_log with query=trade (last --log-days, max 250 rows); needs account:read",
    )
    trades_parser.add_argument(
        "--log-days",
        type=int,
        default=30,
        metavar="N",
        help="With --from-transaction-log: window length in days (default 30)",
    )
    trades_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fe_parser = subparsers.add_parser(
        "frontend",
        help="Serve HTML dashboard at http://host:port (use --host 0.0.0.0 behind TLS for remote access)",
    )
    _add_env_file_after_subcommand(fe_parser)
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

    backfill_parser = subparsers.add_parser(
        "backfill-trade-journal",
        help="Backfill trade_journal.db from Deribit API fills and local strategy state",
    )
    _add_env_file_after_subcommand(backfill_parser)
    backfill_parser.add_argument(
        "--all-accounts",
        action="store_true",
        help="With --investor: backfill every enabled account in accounts.toml",
    )
    backfill_parser.add_argument(
        "--no-api",
        action="store_true",
        help="Skip Deribit get_user_trades (state-only synthetic rows)",
    )
    backfill_parser.add_argument(
        "--no-state",
        action="store_true",
        help="Skip strategy state synthetic rows",
    )
    backfill_parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Skip rebuilding metrics.db daily PnL buckets",
    )
    backfill_parser.add_argument(
        "--force-state",
        action="store_true",
        help="Write state-derived rows even when the group already has journal entries",
    )
    backfill_parser.add_argument(
        "--start-timestamp-ms",
        type=int,
        default=None,
        help="Only fetch API trades at/after this UTC ms timestamp",
    )
    backfill_parser.add_argument(
        "--recent-only",
        action="store_true",
        help="API: use rolling index only (no historical=true pagination)",
    )
    backfill_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_snap_parser = subparsers.add_parser(
        "fee-snapshot",
        help="Capture investor-level NAV_perf snapshot for performance-fee billing",
    )
    _add_env_file_after_subcommand(fee_snap_parser)
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
    _add_env_file_after_subcommand(fee_settle_parser)
    fee_settle_parser.add_argument(
        "--period",
        required=True,
        metavar="YYYY-QN",
        help="Quarter to settle, e.g. 2026-Q1",
    )
    fee_settle_parser.add_argument(
        "--net-flow-usdc",
        default="0",
        help="Net subscription adjustment for the quarter (positive=deposit, negative=withdrawal)",
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
    _add_env_file_after_subcommand(fee_period_parser)
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
    _add_env_file_after_subcommand(fee_status_parser)
    fee_status_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_flow_parser = subparsers.add_parser(
        "fee-flow-report",
        help="Show deposit/withdrawal breakdown from Deribit transaction log (read-only)",
    )
    _add_env_file_after_subcommand(fee_flow_parser)
    fee_flow_parser.add_argument("--json", action="store_true", help="Emit JSON")

    fee_report_parser = subparsers.add_parser(
        "fee-report",
        help="Generate markdown fee report (initial bootstrap or quarterly settlement)",
    )
    _add_env_file_after_subcommand(fee_report_parser)
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

    investor_parser = subparsers.add_parser(
        "investor",
        help="Investor onboarding: init, import handoff, validate, list (ops registry separate from accounts.toml)",
    )
    investor_sub = investor_parser.add_subparsers(dest="investor_command", required=True)

    inv_init = investor_sub.add_parser("init", help="Scaffold config/investors/<id>/ and registry row")
    inv_init.add_argument("investor_id", metavar="ID", help="New investor id (lowercase)")
    inv_init.add_argument(
        "--strategies",
        default="naked",
        help="Comma-separated strategy slugs: naked, bull_put, covered_call (default: naked)",
    )
    inv_init.add_argument("--display-name", default=None, help="Display name for manifest/registry")
    inv_init.add_argument("--email", default=None, help="Dashboard Access email (stored in registry only)")
    inv_init.add_argument(
        "--deribit-env",
        default="mainnet",
        choices=("mainnet", "testnet"),
        help="DERIBIT_ENV written into scaffolded account env files (default: mainnet)",
    )
    inv_init.add_argument(
        "--no-register",
        action="store_true",
        help="Skip appending [[investors]] to config/platform/registry.toml",
    )
    inv_init.add_argument("--json", action="store_true", help="Emit JSON")

    inv_import = investor_sub.add_parser(
        "import-handoff",
        help="Import secrets from handoff TOML into accounts/.env.*",
    )
    inv_import.add_argument("handoff_file", metavar="PATH", help="Handoff TOML path")
    inv_import.add_argument("--investor", metavar="ID", default=None, help="Override [investor].id")
    inv_import.add_argument("--json", action="store_true", help="Emit JSON")

    inv_validate = investor_sub.add_parser("validate", help="Check manifest, registry, and Deribit API auth")
    inv_validate.add_argument("investor_id", metavar="ID")
    inv_validate.add_argument("--no-api", action="store_true", help="Skip live Deribit API checks")
    inv_validate.add_argument(
        "--no-bootstrap-hwm",
        action="store_true",
        help="Skip automatic initial HWM bootstrap after successful API checks",
    )
    inv_validate.add_argument("--json", action="store_true", help="Emit JSON")

    inv_bootstrap = investor_sub.add_parser(
        "bootstrap-hwm",
        help="Bootstrap initial HWM from transaction log (or INITIAL_HWM_NAV_PERF)",
    )
    inv_bootstrap.add_argument("investor_id", metavar="ID")
    inv_bootstrap.add_argument(
        "--force",
        action="store_true",
        help="Re-run bootstrap even if fee ledger already has HWM / flow baseline",
    )
    inv_bootstrap.add_argument("--json", action="store_true", help="Emit JSON")

    inv_list = investor_sub.add_parser("list", help="List investors from registry and disk")
    inv_list.add_argument("--json", action="store_true", help="Emit JSON")

    inv_launchd = investor_sub.add_parser(
        "render-launchd",
        help="Write launchd plists to config/platform/generated/launchd/",
    )
    inv_launchd.add_argument("investor_id", metavar="ID")
    inv_launchd.add_argument("--port", type=int, default=None, help="Override frontend port in plist")
    inv_launchd.add_argument("--json", action="store_true", help="Emit JSON")

    inv_frontend = investor_sub.add_parser(
        "frontend",
        help="Start/stop/restart/status all investor frontends via launchd (macOS)",
    )
    inv_frontend_sub = inv_frontend.add_subparsers(dest="frontend_command", required=True)
    for action in ("start", "stop", "restart", "status"):
        p = inv_frontend_sub.add_parser(action, help=f"{action} frontend LaunchAgent(s)")
        p.add_argument(
            "--investor",
            metavar="ID",
            default=None,
            help="Only this investor (default: all frontend_enabled in registry.toml)",
        )
        p.add_argument(
            "--include-disabled",
            action="store_true",
            help="Include registry rows with frontend_enabled=false",
        )
        p.add_argument(
            "--no-health",
            action="store_true",
            help="Skip local http://127.0.0.1:<port>/api/health probe",
        )
        p.add_argument("--json", action="store_true", help="Emit JSON")

    inv_live = investor_sub.add_parser(
        "live",
        help="Start/stop/restart/status all investor live bots via launchd (macOS)",
    )
    inv_live_sub = inv_live.add_subparsers(dest="live_command", required=True)
    for action in ("start", "stop", "restart", "status"):
        p = inv_live_sub.add_parser(action, help=f"{action} live LaunchAgent(s)")
        p.add_argument(
            "--investor",
            metavar="ID",
            default=None,
            help="Only this investor (default: all live_enabled in registry.toml)",
        )
        p.add_argument(
            "--include-disabled",
            action="store_true",
            help="Include registry rows with live_enabled=false",
        )
        p.add_argument(
            "--no-supervisor-check",
            action="store_true",
            help="Skip waiting for logs/live/<id>/supervisor.log started pid=",
        )
        p.add_argument("--json", action="store_true", help="Emit JSON")

    args = parser.parse_args(argv)
    if getattr(args, "env_file_after_cmd", None) is not None:
        args.env_file = args.env_file_after_cmd
    _apply_investor_cli_args(args)
    configure_logging(args.verbose)
    if args.command == "investor":
        from .investor_ops import (
            bootstrap_initial_hwm,
            import_handoff,
            investor_init,
            list_investors,
            parse_strategy_slugs,
            render_launchd_plists,
            validate_investor,
        )

        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")

        if args.investor_command == "init":
            strategies = parse_strategy_slugs(args.strategies)
            result = investor_init(
                args.investor_id,
                strategies=strategies,
                display_name=args.display_name,
                dashboard_email=args.email,
                deribit_env=args.deribit_env,
                register=not args.no_register,
                repo_root=repo_root,
            )
            payload = {
                "action": "investor-init",
                "investor_id": result.investor_id,
                "investor_dir": str(result.investor_dir),
                "strategies": list(result.strategies),
                "frontend_port": result.frontend_port,
                "launchd_paths": [str(path) for path in result.launchd_paths],
                "next_steps": [
                    "Fill secrets: ./bot investor import-handoff config/handoff/<id>.toml",
                    f"Validate + initial HWM: ./bot investor validate {result.investor_id}",
                    "Install launchd: see docs/operator-onboarding-zh-TW.md",
                ],
            }
            render(payload, args.json)
            return 0 if result.investor_id else 1

        if args.investor_command == "import-handoff":
            outcome = import_handoff(
                Path(args.handoff_file),
                investor_id=args.investor,
                repo_root=repo_root,
            )
            render({"action": "investor-import-handoff", **outcome}, args.json)
            return 0

        if args.investor_command == "validate":
            result = validate_investor(
                args.investor_id,
                check_api=not args.no_api,
                bootstrap_hwm=not args.no_bootstrap_hwm,
                repo_root=repo_root,
            )
            payload = {
                "action": "investor-validate",
                "investor_id": result.investor_id,
                "ok": result.ok,
                "issues": [
                    {"level": issue.level, "code": issue.code, "message": issue.message} for issue in result.issues
                ],
                "api_checks": list(result.api_checks),
                "hwm_bootstrap": result.hwm_bootstrap,
            }
            render(payload, args.json)
            return 0 if result.ok else 1

        if args.investor_command == "bootstrap-hwm":
            outcome = bootstrap_initial_hwm(
                args.investor_id,
                repo_root=repo_root,
                force=args.force,
            )
            render({"action": "investor-bootstrap-hwm", **outcome}, args.json)
            return 0

        if args.investor_command == "list":
            rows = list_investors(repo_root=repo_root)
            render({"action": "investor-list", "investors": rows}, args.json)
            return 0

        if args.investor_command == "render-launchd":
            from .investor_registry import load_platform_registry

            registry = load_platform_registry(repo_root=repo_root)
            paths = render_launchd_plists(
                args.investor_id,
                repo_root=repo_root,
                registry=registry,
                frontend_port=args.port,
            )
            render(
                {
                    "action": "investor-render-launchd",
                    "investor_id": args.investor_id,
                    "paths": [str(path) for path in paths],
                },
                args.json,
            )
            return 0

        if args.investor_command == "frontend":
            from .investor_frontend_launchd import manage_frontend_launchd

            results = manage_frontend_launchd(
                args.frontend_command,
                repo_root=repo_root,
                investor_id=args.investor,
                include_disabled=args.include_disabled,
                check_health=not args.no_health,
            )
            payload = {
                "action": f"investor-frontend-{args.frontend_command}",
                "results": [row.to_dict() for row in results],
            }
            render(payload, args.json)
            if not args.json:
                for row in results:
                    port = row.frontend_port if row.frontend_port is not None else "?"
                    health = ""
                    if row.health_ok is not None:
                        health = " health=" + ("ok" if row.health_ok else "fail")
                    mark = "ok" if row.ok else "FAIL"
                    print(f"[{mark}] {row.investor_id} :{port} {row.state} — {row.message}{health}")
            return 0 if all(row.ok for row in results) else 1

        if args.investor_command == "live":
            from .investor_live_launchd import manage_live_launchd

            results = manage_live_launchd(
                args.live_command,
                repo_root=repo_root,
                investor_id=args.investor,
                include_disabled=args.include_disabled,
                check_supervisor=not args.no_supervisor_check,
            )
            payload = {
                "action": f"investor-live-{args.live_command}",
                "results": [row.to_dict() for row in results],
            }
            render(payload, args.json)
            if not args.json:
                for row in results:
                    supervisor = ""
                    if row.supervisor_ok is not None:
                        supervisor = " supervisor=" + ("ok" if row.supervisor_ok else "fail")
                    mark = "ok" if row.ok else "FAIL"
                    print(f"[{mark}] {row.investor_id} {row.state} — {row.message}{supervisor}")
            return 0 if all(row.ok for row in results) else 1

        raise SystemExit(2)

    if args.command == "backfill-trade-journal":
        from .trade_journal_backfill import backfill_account, backfill_investor

        repo_root = find_repo_root(Path.cwd())
        kwargs = {
            "use_api": not args.no_api,
            "use_state": not args.no_state,
            "sync_metrics": not args.no_metrics,
            "historical": not args.recent_only,
            "start_timestamp_ms": args.start_timestamp_ms,
            "skip_state_if_group_has_journal": not args.force_state,
        }
        if args.investor and (args.all_accounts or not args.account):
            summaries = backfill_investor(args.investor, **kwargs)
            render(
                {"action": "backfill-trade-journal", "accounts": [s.to_dict() for s in summaries]},
                args.json,
            )
            return 0
        summary = backfill_account(Path(args.env_file), **kwargs)
        render({"action": "backfill-trade-journal", **summary.to_dict()}, args.json)
        return 0
    if args.command == "fee-snapshot":
        from .investor_nav_snapshot import capture_investor_nav, store_nav_capture

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
        from .investor_nav_snapshot import settle_quarter

        if not args.investor:
            raise SystemExit("fee-settle requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        result = settle_quarter(
            args.investor,
            args.period,
            net_flow_usdc=to_decimal(args.net_flow_usdc),
            repo_root=repo_root,
            force=bool(args.force),
        )
        render({"action": "fee-settle", **result}, args.json)
        return 0
    if args.command == "fee-settle-period":
        from .investor_nav_snapshot import parse_fee_timestamp, settle_period

        if not args.investor:
            raise SystemExit("fee-settle-period requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        end_ms = parse_fee_timestamp(args.to, boundary="end")
        start_ms = parse_fee_timestamp(args.from_ts, boundary="start") if args.from_ts else None
        net_flow = to_decimal(args.net_flow_usdc) if args.net_flow_usdc is not None else None
        write_pdf = args.format in {"both", "all", "pdf"}
        write_md = args.format in {"both", "all", "md"}
        write_csv = args.format in {"all", "csv"}
        result = settle_period(
            args.investor,
            end_ms=end_ms,
            start_ms=start_ms,
            net_flow_usdc=net_flow,
            repo_root=repo_root,
            persist=not args.no_persist,
            force=bool(args.force),
            write_report=False,
        )
        payload: dict[str, Any] = {"action": "fee-settle-period", **result}
        if not args.no_report:
            from .investor_fee_report import write_settlement_fee_report

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
        from .investor_nav_snapshot import fee_status

        if not args.investor:
            raise SystemExit("fee-status requires --investor <ID>")
        repo_root = find_repo_root(Path.cwd())
        if repo_root is None:
            raise SystemExit("Cannot locate repository root")
        render({"action": "fee-status", **fee_status(args.investor, repo_root=repo_root)}, args.json)
        return 0
    if args.command == "fee-flow-report":
        from .investor_cash_flow import fetch_cumulative_net_flow_usdc, flow_report_dict
        from .investor_nav_snapshot import capture_investor_nav

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
        flow = fetch_cumulative_net_flow_usdc(
            manifest.root,
            repo_root=repo_root,
            index_by_ccy=index_by_ccy,
        )
        render(
            {
                "action": "fee-flow-report",
                "investor_id": manifest.investor_id,
                **flow_report_dict(flow, index_by_ccy=index_by_ccy),
            },
            args.json,
        )
        return 0
    if args.command == "fee-report":
        from .investor_fee_report import write_initial_fee_report, write_settlement_fee_report

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
    if args.command == "frontend":
        from .frontend_server import serve as serve_frontend

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
    if args.command == "user-trades":
        from .utils import utc_now_ms

        cfg = load_config(args.env_file, require_private=True)
        client = DeribitClient(cfg)
        sorting = args.sorting
        if sorting is not None and str(sorting).strip().lower() in {"", "none"}:
            sorting = None

        if args.from_transaction_log and args.instrument:
            raise SystemExit("user-trades: do not combine --from-transaction-log with --instrument")

        if args.from_transaction_log:
            if not args.currency:
                raise SystemExit("user-trades: --currency is required with --from-transaction-log")
        elif args.instrument:
            pass
        elif not args.currency:
            raise SystemExit("user-trades: pass --currency (by wallet) or --instrument (by contract name)")

        if args.from_transaction_log:
            now_ms = utc_now_ms()
            span_ms = max(1, int(args.log_days)) * 86_400_000
            logs = client.get_transaction_log(
                currency=args.currency,
                start_timestamp=now_ms - span_ms,
                end_timestamp=now_ms,
                count=250,
                subaccount_id=args.subaccount_id,
                query="trade",
            )
            render(
                {
                    "action": "user-trades",
                    "source": "transaction_log",
                    "currency": str(args.currency).upper(),
                    "deribit_env": cfg.env,
                    "note": "Rows are in result.logs (not result.trades). Filter query=trade; fields differ from get_user_trades.",
                    "result": {"logs": logs, "count": len(logs)},
                },
                args.json,
            )
            return 0

        instrument = str(args.instrument).strip() if args.instrument else ""

        def _fetch_currency(*, historical: bool) -> dict:
            assert args.currency is not None
            return client.get_user_trades_by_currency(
                args.currency,
                kind=args.kind,
                count=args.count,
                sorting=sorting,
                historical=historical,
                subaccount_id=args.subaccount_id,
            )

        def _fetch_instrument(*, historical: bool) -> dict:
            return client.get_user_trades_by_instrument(
                instrument,
                count=args.count,
                sorting=sorting,
                historical=historical,
                subaccount_id=args.subaccount_id,
            )

        _fetch = _fetch_instrument if instrument else _fetch_currency
        source = "get_user_trades_by_instrument" if instrument else "get_user_trades_by_currency"

        used_historical = bool(args.historical)
        if args.historical:
            payload = _fetch(historical=True)
        else:
            payload = _fetch(historical=False)
            trades = list(payload.get("trades") or [])
            if not trades and not args.recent_only:
                historical_payload = _fetch(historical=True)
                historical_trades = list(historical_payload.get("trades") or [])
                if historical_trades:
                    payload = historical_payload
                    used_historical = True

        out_currency = str(args.currency).upper() if args.currency else None
        render(
            {
                "action": "user-trades",
                "source": source,
                "currency": out_currency,
                "instrument": instrument or None,
                "kind_filter": (args.kind or None) if not instrument else None,
                "deribit_env": cfg.env,
                "used_historical_index": used_historical,
                "note": (
                    "By-currency without --kind returns options+futures+perp+spot for that wallet. "
                    "Use --kind option for options only, or --instrument NAME for one series. "
                    "Linear options (BTC_USDC-…) live under currency=USDC. "
                    "Deribit splits indexes: recent (~24h) vs historical=true for older fills "
                    "(auto-retried when empty unless --recent-only)."
                ),
                "result": payload,
            },
            args.json,
        )
        return 0
    if args.command == "telegram-test":
        from .telegram_alerts import bootstrap_telegram_env, send_test_alert

        repo_root = find_repo_root(Path.cwd())
        if args.env_file and args.env_file != ".env" and Path(args.env_file).is_file():
            from dotenv import dotenv_values

            for key, value in dotenv_values(args.env_file).items():
                if value is not None:
                    os.environ[key] = str(value)
        bootstrap_telegram_env(repo_root)
        try:
            sent = send_test_alert(repo_root=repo_root)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        payload = {"action": "telegram-test", "sent": sent}
        render(payload, args.json)
        return 0 if sent else 1
    bot = build_bot(args)

    try:
        if args.command == "ping":
            render(bot.ping(), args.json)
            return 0
        if args.command == "status":
            render(bot.status(), args.json)
            return 0
        if args.command == "report":
            render(bot.report(days=args.days), args.json)
            return 0
        if args.command == "backtest":
            start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
            end_raw = args.end
            end = datetime.now(tz=UTC) if end_raw == "today" else datetime.fromisoformat(end_raw).replace(tzinfo=UTC)
            cfg = load_config(args.env_file, require_private=False)
            client = DeribitClient(cfg)
            cache = BacktestCache(root=Path(args.cache_root))
            data = BacktestDataClient(client, cache=cache)
            bt = BacktestConfig(start=start, end=end, resolution=str(args.resolution), cache_root=str(args.cache_root))
            currencies = parse_csv(args.currencies, upper=True) or cfg.scan_underlyings or cfg.managed_currencies
            res = run_backtest(cfg, data, bt, currencies=currencies)
            fallback_note = None
            if (res.params.get("open_trade_count") or 0) == 0 and int(args.auto_fallback_window_days or 0) > 0:
                days = int(args.auto_fallback_window_days)
                fallback_start = end - timedelta(days=days)
                bt2 = BacktestConfig(
                    start=fallback_start, end=end, resolution=str(args.resolution), cache_root=str(args.cache_root)
                )
                res2 = run_backtest(cfg, data, bt2, currencies=currencies)
                fallback_note = {
                    "reason": "no_trades_in_requested_window (likely limited public expired instruments coverage)",
                    "requested": {"start": start.isoformat(), "end": end.isoformat()},
                    "fallback": {"start": fallback_start.isoformat(), "end": end.isoformat(), "days": days},
                    "fallback_open_trade_count": res2.params.get("open_trade_count", 0),
                }
                if (res2.params.get("open_trade_count") or 0) > 0:
                    res = res2
            scan = None
            if args.scan_params:
                scan = run_param_scan(
                    cfg,
                    client,
                    start=start,
                    end=end,
                    resolution=str(args.resolution),
                    cache_root=str(args.cache_root),
                )
            report_md = render_backtest_report_md(
                generated_at=datetime.now(tz=UTC),
                backtest={
                    "params": res.params,
                    "stress": res.stress,
                    "notes": (res.notes + ([str(fallback_note)] if fallback_note else [])),
                },
                scan=scan,
            )
            report_path = Path(args.report) if args.report else None
            if report_path is not None:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report_md)
            payload = {
                "action": "backtest",
                "params": res.params,
                "stress": res.stress,
                "scan": scan,
                "report_path": str(report_path) if report_path is not None else None,
                "fallback": fallback_note,
            }
            render(payload, args.json)
            return 0
        if args.command == "scan":
            render(
                bot.scan(
                    currencies=parse_csv(args.currencies, upper=True) or None,
                    top_n=args.top_n,
                ),
                args.json,
            )
            return 0
        if args.command == "enter-best":
            render(
                bot.enter_best(
                    currencies=parse_csv(args.currencies, upper=True) or None,
                    live=args.live,
                ),
                args.json,
            )
            return 0
        if args.command == "manage":
            render(bot.manage(live=args.live), args.json)
            return 0
        if args.command == "run":
            render(
                bot.run(
                    live=args.live,
                    cycles=args.cycles,
                    currencies=parse_csv(args.currencies, upper=True) or None,
                ),
                args.json,
            )
            return 0
        if args.command == "panic-close":
            render(bot.panic_close(live=args.live), args.json)
            return 0
        if args.command == "close-position":
            instruments = _parse_instrument_names(args.instrument)
            if not args.list and not instruments:
                raise SystemExit("close-position: pass --instrument NAME or use --list")
            amount = to_decimal(args.amount) if args.amount is not None else None
            if amount is not None and amount <= 0:
                raise SystemExit("close-position: --amount must be positive")
            render(
                bot.close_positions(
                    instruments=instruments,
                    list_only=args.list,
                    live=args.live,
                    order_type=args.order_type,
                    amount=amount,
                ),
                args.json,
            )
            return 0
        if args.command == "cancel":
            render(bot.cancel(args.order_id), args.json)
            return 0
        if args.command == "stress-current":
            cfg = load_config(args.env_file, require_private=True)
            client = DeribitClient(cfg)
            shocks = []
            for raw in str(args.shocks or "").split(","):
                raw = raw.strip()
                if not raw:
                    continue
                val = to_decimal(raw)
                if val <= 0:
                    continue
                shocks.append(-val)  # drops are negative shocks
            result = compute_current_stress(cfg, client, shocks=shocks)
            md = render_current_stress_md(result)
            report_path = Path(args.report) if args.report else None
            if report_path is not None:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(md)
            payload = {
                "action": "stress-current",
                "report_path": str(report_path) if report_path is not None else None,
                "option_strategy": result.option_strategy,
                "strategy_analysis": result.strategy_analysis,
                "index_by_ccy": {k: str(v) for k, v in result.index_by_ccy.items()},
                "equity_usdc_by_book": {k: str(v) for k, v in result.equity_usdc_by_book.items()},
                "scenario_count": len(result.scenarios),
            }
            render(payload, args.json)
            return 0
        raise SystemExit(2)
    except KeyboardInterrupt:
        logging.info("Interrupted")
        return 130
