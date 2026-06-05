from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..backtest import BacktestConfig, run_backtest
from ..backtest_data import BacktestCache, BacktestDataClient
from ..client import DeribitClient
from ..config import load_config
from ..current_stress import compute_current_stress, render_current_stress_md
from ..env_layout import find_repo_root
from ..param_scan import run_param_scan
from ..report_md import render_backtest_report_md
from ..utils import parse_csv, to_decimal
from .common import add_env_file_after_subcommand, build_bot, parse_instrument_names, render

BOT_COMMANDS = frozenset(
    {
        "ping",
        "status",
        "report",
        "backtest",
        "scan",
        "enter-best",
        "manage",
        "run",
        "panic-close",
        "close-position",
        "cancel",
        "stress-current",
    }
)

STANDALONE_COMMANDS = frozenset(
    {
        "telegram-test",
        "user-trades",
        "backfill-trade-journal",
    }
)


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    ping_parser = subparsers.add_parser("ping", help="Ping Deribit public API")
    add_env_file_after_subcommand(ping_parser)
    ping_parser.add_argument("--json", action="store_true", help="Emit JSON")

    telegram_parser = subparsers.add_parser(
        "telegram-test",
        help="Send a test message to the configured Telegram chat",
    )
    add_env_file_after_subcommand(telegram_parser)
    telegram_parser.add_argument("--json", action="store_true", help="Emit JSON")

    status_parser = subparsers.add_parser("status", help="Show portfolio state, trade groups, orders, and positions")
    add_env_file_after_subcommand(status_parser)
    status_parser.add_argument("--json", action="store_true", help="Emit JSON")

    report_parser = subparsers.add_parser("report", help="Show realized spread report from local state")
    add_env_file_after_subcommand(report_parser)
    report_parser.add_argument("--days", type=int, default=30, help="Rolling window in days; 0 means all history")
    report_parser.add_argument("--json", action="store_true", help="Emit JSON")

    backtest_parser = subparsers.add_parser("backtest", help="Run public-data historical backtest + black-swan overlay")
    add_env_file_after_subcommand(backtest_parser)
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
    add_env_file_after_subcommand(scan_parser)
    scan_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    scan_parser.add_argument(
        "--strategy",
        help="Override OPTION_STRATEGY for this scan: naked_short, bull_put_spread, covered_call",
    )
    scan_parser.add_argument("--top-n", type=int, help="Number of candidates to return")
    scan_parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Include scan_rejections detail (extra Deribit orderbook calls)",
    )
    scan_parser.add_argument("--json", action="store_true", help="Emit JSON")

    enter_parser = subparsers.add_parser("enter-best", help="Preview or enter the best spread candidate")
    add_env_file_after_subcommand(enter_parser)
    enter_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    enter_parser.add_argument("--live", action="store_true", help="Actually place orders")
    enter_parser.add_argument("--json", action="store_true", help="Emit JSON")

    manage_parser = subparsers.add_parser("manage", help="Run one portfolio management cycle")
    add_env_file_after_subcommand(manage_parser)
    manage_parser.add_argument("--live", action="store_true", help="Actually place hedge/exit/roll orders")
    manage_parser.add_argument("--json", action="store_true", help="Emit JSON")

    run_parser = subparsers.add_parser("run", help="Run repeated manage+enter cycles")
    add_env_file_after_subcommand(run_parser)
    run_parser.add_argument("--live", action="store_true", help="Actually place orders")
    run_parser.add_argument("--cycles", type=int, default=1, help="Number of cycles to run; 0 means forever")
    run_parser.add_argument("--currencies", help="Comma-separated currencies, e.g. BTC,ETH")
    run_parser.add_argument("--json", action="store_true", help="Emit JSON")

    panic_parser = subparsers.add_parser("panic-close", help="Cancel orders and flatten option/perp risk")
    add_env_file_after_subcommand(panic_parser)
    panic_parser.add_argument("--live", action="store_true", help="Actually place closing orders")
    panic_parser.add_argument("--json", action="store_true", help="Emit JSON")

    close_parser = subparsers.add_parser(
        "close-position",
        help="Close specific option or perp positions (use sub-account --env-file)",
    )
    add_env_file_after_subcommand(close_parser)
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
    add_env_file_after_subcommand(cancel_parser)
    cancel_parser.add_argument("--order-id", required=True, help="Deribit order id")
    cancel_parser.add_argument("--json", action="store_true", help="Emit JSON")

    stress_parser = subparsers.add_parser("stress-current", help="Stress test current live positions (uses index)")
    add_env_file_after_subcommand(stress_parser)
    stress_parser.add_argument(
        "--shocks", default="0.10,0.20,0.30,0.40,0.50,0.60", help="Comma-separated magnitudes, e.g. 0.1,0.2"
    )
    stress_parser.add_argument("--report", default="docs/backtest/current_black_swan.md", help="Output markdown path")
    stress_parser.add_argument("--json", action="store_true", help="Emit JSON")

    trades_parser = subparsers.add_parser(
        "user-trades",
        help="Query fills: by wallet currency (get_user_trades_by_currency), by contract (--instrument), or transaction log",
    )
    add_env_file_after_subcommand(trades_parser)
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

    backfill_parser = subparsers.add_parser(
        "backfill-trade-journal",
        help="Backfill trade_journal.db from Deribit API fills and local strategy state",
    )
    add_env_file_after_subcommand(backfill_parser)
    backfill_parser.add_argument(
        "--all-accounts",
        action="store_true",
        help="With --investor: backfill every enabled account in accounts.toml",
    )
    backfill_parser.add_argument(
        "--all-investors",
        action="store_true",
        help="Backfill every investor under config/investors/ (implies --all-accounts)",
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


def _dispatch_backfill(args: argparse.Namespace) -> int:
    from ..trade_journal_backfill import backfill_account, backfill_all_investors, backfill_investor

    kwargs = {
        "use_api": not args.no_api,
        "use_state": not args.no_state,
        "sync_metrics": not args.no_metrics,
        "historical": not args.recent_only,
        "start_timestamp_ms": args.start_timestamp_ms,
        "skip_state_if_group_has_journal": not args.force_state,
    }
    if getattr(args, "all_investors", False):
        summaries = backfill_all_investors(**kwargs)
        render(
            {"action": "backfill-trade-journal", "investors": "all", "accounts": [s.to_dict() for s in summaries]},
            args.json,
        )
        return 0
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


def _dispatch_user_trades(args: argparse.Namespace) -> int:
    from ..utils import utc_now_ms

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


def _dispatch_telegram_test(args: argparse.Namespace) -> int:
    from ..telegram_alerts import bootstrap_telegram_env, send_test_alert

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


def _dispatch_bot_commands(args: argparse.Namespace) -> int:
    bot = build_bot(args)
    if args.command == "run" and getattr(args, "live", False):
        from ..structured_log import configure_live_structured_logging

        configure_live_structured_logging(bot._telegram_scope(), verbose=args.verbose)

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
                    include_scan_diagnostics=args.diagnostics or None,
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
            instruments = parse_instrument_names(args.instrument)
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
                shocks.append(-val)
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


def dispatch(args: argparse.Namespace) -> int | None:
    if args.command == "backfill-trade-journal":
        return _dispatch_backfill(args)
    if args.command == "user-trades":
        return _dispatch_user_trades(args)
    if args.command == "telegram-test":
        return _dispatch_telegram_test(args)
    if args.command in BOT_COMMANDS:
        return _dispatch_bot_commands(args)
    return None
