"""CSV export for investor fee reports (flows + summary + trades)."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from .investor_cash_flow import (
    SubscriptionFlowLine,
    effective_fee_flow_start_ms,
    initial_spot_deduction_usdc,
    native_book_amount_to_usdc,
    ordered_net_flow_books,
)
from .investor_fee_report import (
    InitialFeeReportContext,
    SettlementFeeReportContext,
    _equity_native_for_book,
    _money,
    _native,
    _ts_fmt,
)
from .utils import to_decimal

_FLOW_HEADERS = [
    "timestamp_utc",
    "account",
    "type",
    "currency",
    "amount_native",
    "usdc_equiv",
    "included_in_subscription",
]

_TRADE_HEADERS = [
    "closed_utc",
    "account",
    "instrument",
    "collateral",
    "quantity",
    "pnl_native",
    "pnl_usdc",
    "close_reason",
]


def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def _flow_rows(lines: tuple[SubscriptionFlowLine, ...]) -> list[list[Any]]:
    return [
        [
            _ts_fmt(line.timestamp_ms),
            line.identity_label,
            line.flow_type,
            line.book,
            _native(line.amount_native, line.book),
            _money(line.usdc_equiv),
            "yes" if line.included_in_subscription else "no",
        ]
        for line in lines
    ]


def _summary_rows(pairs: list[tuple[str, str]]) -> list[list[str]]:
    return [[key, value] for key, value in pairs]


def write_initial_fee_report_csv(
    ctx: InitialFeeReportContext,
    *,
    flows_path: Path,
    summary_path: Path,
) -> tuple[Path, Path]:
    included = [line for line in ctx.flow_lines if line.included_in_subscription]
    subtotal = sum((line.usdc_equiv for line in included), Decimal("0"))

    summary_pairs: list[tuple[str, str]] = [
        ("report_type", "initial"),
        ("investor_id", ctx.investor_id),
        ("display_name", ctx.display_name),
        ("generated_at_utc", _ts_fmt(ctx.generated_at_ms)),
        ("performance_fee_rate", ctx.fee_config["performance_fee_rate"]),
        ("management_fee_annual_rate", ctx.fee_config["management_fee_annual_rate"]),
        ("index_btc_usd", _money(ctx.index_by_ccy["BTC"])),
        ("index_eth_usd", _money(ctx.index_by_ccy["ETH"])),
        ("flow_subtotal_usdc_equiv", _money(subtotal)),
    ]
    if ctx.live_nav:
        summary_pairs.append(("total_equity_usdc", _money(ctx.live_nav["total_equity_usdc"])))
        equity_by_book = {str(k).upper(): to_decimal(v) for k, v in (ctx.live_nav.get("equity_by_book") or {}).items()}
        equity_native_by_book = {
            str(k).upper(): to_decimal(v) for k, v in (ctx.live_nav.get("equity_native_by_book") or {}).items()
        }
        for book in ("BTC", "ETH", "USDC"):
            usdc = equity_by_book.get(book, Decimal("0"))
            native = _equity_native_for_book(
                book,
                equity_native_by_book=equity_native_by_book,
                equity_by_book=equity_by_book,
                index_by_ccy=ctx.index_by_ccy,
            )
            summary_pairs.append((f"equity_{book.lower()}_native", _native(native, book)))
            summary_pairs.append((f"equity_{book.lower()}_usdc", _money(usdc)))
    if ctx.baseline is not None:
        summary_pairs.extend(
            [
                ("baseline_source", ctx.baseline.source),
                (
                    "scan_start_utc",
                    _ts_fmt(effective_fee_flow_start_ms(ctx.baseline.start_timestamp_ms)),
                ),
                ("scan_end_utc", _ts_fmt(ctx.baseline.end_timestamp_ms)),
                ("entry_count", str(ctx.baseline.entry_count)),
                ("cumulative_net_flow_usdc", _money(ctx.baseline.cumulative_net_flow_usdc)),
                ("initial_hwm_nav_perf", _money(ctx.baseline.initial_hwm_nav_perf)),
                ("bootstrap_at_utc", _ts_fmt(ctx.baseline.bootstrapped_at_ms)),
            ]
        )
        books = ctx.baseline.net_flow_native_by_book
        btc_n = books.get("BTC", Decimal("0"))
        eth_n = books.get("ETH", Decimal("0"))
        _b, _e, spot_total, _hwm = initial_spot_deduction_usdc(books, index_by_ccy=ctx.index_by_ccy)
        summary_pairs.extend(
            [
                ("initial_spot_btc_native", _native(btc_n, "BTC")),
                ("initial_spot_eth_native", _native(eth_n, "ETH")),
                ("initial_spot_deduction_usdc_equiv", _money(spot_total)),
            ]
        )
        for book in ordered_net_flow_books(ctx.baseline.net_flow_native_by_book):
            amount = ctx.baseline.net_flow_native_by_book[book]
            summary_pairs.append((f"net_flow_{book.lower()}_native", _native(amount, book)))
            summary_pairs.append(
                (
                    f"net_flow_{book.lower()}_usdc_equiv",
                    _money(native_book_amount_to_usdc(amount, book, ctx.index_by_ccy)),
                )
            )
    else:
        native_by_book: dict[str, Decimal] = {}
        for line in included:
            native_by_book[line.book] = native_by_book.get(line.book, Decimal("0")) + line.amount_native
        _b, _e, _s, initial_hwm = initial_spot_deduction_usdc(native_by_book, index_by_ccy=ctx.index_by_ccy)
        summary_pairs.extend(
            [
                ("baseline_source", "provisional_from_log"),
                ("cumulative_net_flow_usdc", _money(subtotal)),
                ("initial_hwm_nav_perf", _money(initial_hwm)),
            ]
        )
    _write_csv(flows_path, _FLOW_HEADERS, _flow_rows(ctx.flow_lines))
    _write_csv(summary_path, ["field", "value"], _summary_rows(summary_pairs))
    return flows_path, summary_path


def write_settlement_fee_report_csv(
    ctx: SettlementFeeReportContext,
    *,
    repo_root: Path | str,
    flows_path: Path,
    summary_path: Path,
    trades_path: Path,
) -> tuple[Path, Path, Path]:
    from .investor_fee_report_period import build_investor_period_report

    report = build_investor_period_report(ctx, repo_root=repo_root)

    summary_pairs: list[tuple[str, str]] = [
        ("report_type", "settlement"),
        ("investor_id", report.investor_id),
        ("display_name", report.display_name),
        ("period", report.period_label),
        ("day_a", report.day_a.label),
        ("day_b", report.day_b.label),
        ("day_a_btc", _native(report.day_a.native["BTC"], "BTC")),
        ("day_a_eth", _native(report.day_a.native["ETH"], "ETH")),
        ("day_a_usdc", _native(report.day_a.native["USDC"], "USDC")),
        ("day_b_btc", _native(report.day_b.native["BTC"], "BTC")),
        ("day_b_eth", _native(report.day_b.native["ETH"], "ETH")),
        ("day_b_usdc", _native(report.day_b.native["USDC"], "USDC")),
        ("period_deposit_btc", _native(report.deposit_native["BTC"], "BTC")),
        ("period_deposit_eth", _native(report.deposit_native["ETH"], "ETH")),
        ("period_deposit_usdc", _native(report.deposit_native["USDC"], "USDC")),
        ("period_withdraw_btc", _native(report.withdraw_native["BTC"], "BTC")),
        ("period_withdraw_eth", _native(report.withdraw_native["ETH"], "ETH")),
        ("period_withdraw_usdc", _native(report.withdraw_native["USDC"], "USDC")),
        ("earned_btc", _native(report.earned_native["BTC"], "BTC")),
        ("earned_eth", _native(report.earned_native["ETH"], "ETH")),
        ("earned_usdc", _native(report.earned_native["USDC"], "USDC")),
        ("earned_btc_usdc_equiv", _money(report.earned_usdc["BTC"])),
        ("earned_eth_usdc_equiv", _money(report.earned_usdc["ETH"])),
        ("earned_usdc_usdc_equiv", _money(report.earned_usdc["USDC"])),
        ("day_a_total_equity_usdc", _money(report.day_a.total_equity_usdc)),
        ("day_b_total_equity_usdc", _money(report.day_b.total_equity_usdc)),
        ("total_equity_change_usdc", _money(report.total_equity_change)),
        ("nav_perf_start", _money(report.nav_perf_start)),
        ("nav_perf_end", _money(report.nav_perf_end)),
        ("nav_perf_change", _money(report.nav_perf_change)),
        ("collateral_spot_start_usdc", _money(report.collateral_spot_start)),
        ("collateral_spot_end_usdc", _money(report.collateral_spot_end)),
        ("net_flow_usdc", _money(report.net_flow_usdc)),
        ("strategy_pnl_period_usdc", _money(report.total_usdc_earned)),
        ("realized_trading_pnl_period_usdc", _money(report.realized_trading_pnl.total_pnl_usdc)),
        ("realized_options_pnl_period_usdc", _money(report.realized_trading_pnl.options_pnl_usdc)),
        ("realized_hedge_pnl_period_usdc", _money(report.realized_trading_pnl.hedge_pnl_usdc)),
        ("realized_options_pnl_lifetime_usdc", _money(report.lifetime_realized_trading_pnl.options_pnl_usdc)),
        ("realized_hedge_pnl_lifetime_usdc", _money(report.lifetime_realized_trading_pnl.hedge_pnl_usdc)),
        ("dashboard_total_profit_usdc", _money(report.dashboard_total_profit_usdc)),
        ("trading_profit_fee_basis_usdc", _money(report.fee_basis_trading_profit_usdc)),
        ("hwm_start", _money(report.hwm_start)),
        ("distributable_profit_usdc", _money(report.distributable_profit)),
        ("hwm_end", _money(report.hwm_end)),
        ("total_usdc_earned", _money(report.total_usdc_earned)),
        ("performance_fee_usdc", _money(report.performance_fee)),
        ("management_fee_usdc", _money(report.management_fee)),
        ("total_fees_due_usdc", _money(report.total_fees_due)),
        ("avg_aum_mgmt_usdc", _money(report.avg_aum_mgmt)),
        ("period_return_pct", _money(report.period_return_pct) if report.period_return_pct is not None else ""),
        ("equity_return_pct", _money(report.equity_return_pct) if report.equity_return_pct is not None else ""),
        ("closed_trade_count", str(report.statement_trade_summary.closed_count)),
        (
            "closed_trade_win_rate_pct",
            _money(report.statement_trade_summary.win_rate_pct)
            if report.statement_trade_summary.win_rate_pct is not None
            else "",
        ),
        ("closed_trade_pnl_usdc", _money(report.executive_profit.options_pnl_usdc)),
        ("closed_trade_count_period", str(report.trade_summary.closed_count)),
        ("closed_trade_pnl_usdc_period", _money(report.trade_summary.total_pnl_usdc)),
        ("index_btc_usd_end", _money(report.index_end["BTC"])),
        ("index_eth_usd_end", _money(report.index_end["ETH"])),
    ]

    trade_rows = [
        [
            _ts_fmt(int(row["closed_timestamp_ms"])),
            row["account"],
            row["short_instrument"],
            row["collateral"],
            _native(to_decimal(row["quantity"]), str(row["collateral"])),
            _native(to_decimal(row["realized_pnl_native"]), str(row["collateral"])),
            _money(row["realized_pnl_usdc"]),
            row.get("close_reason") or "",
        ]
        for row in report.statement_closed_trades
    ]

    _write_csv(flows_path, _FLOW_HEADERS, _flow_rows(ctx.quarter_flow_lines))
    _write_csv(summary_path, ["field", "value"], _summary_rows(summary_pairs))
    _write_csv(trades_path, _TRADE_HEADERS, trade_rows)
    return flows_path, summary_path, trades_path
