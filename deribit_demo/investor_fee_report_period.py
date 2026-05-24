"""Investor-facing period settlement summary (simple balances + fees + trade log)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .config import load_config
from .env_layout import find_repo_root, load_investor_manifest
from .exceptions import ConfigurationError
from .investor_cash_flow import (
    SubscriptionFlowLine,
    native_book_amount_to_usdc,
)
from .models import is_phantom_reconcile_close, open_short_instrument_names
from .state import StrategyStateStore, load_performance_exclusion_group_ids
from .utils import to_decimal

_BOOKS: tuple[str, ...] = ("BTC", "ETH", "USDC")
_DEPOSIT_FLOW_TYPES = frozenset({"deposit", "transfer_in"})
_WITHDRAW_FLOW_TYPES = frozenset({"withdrawal", "transfer_out"})


def _ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _money(x: Decimal | str, *, places: int = 2) -> str:
    value = to_decimal(x)
    return f"{value:.{places}f}"


def _native(x: Decimal | str, book: str) -> str:
    value = to_decimal(x)
    places = 8 if book in {"BTC", "ETH"} else 2
    return f"{value:.{places}f}"

@dataclass(frozen=True)
class PeriodBookSnapshot:
    label: str
    ts_ms: int
    native: dict[str, Decimal]
    usdc_equiv: dict[str, Decimal]
    total_equity_usdc: Decimal


@dataclass(frozen=True)
class InvestorPeriodReport:
    investor_id: str
    display_name: str
    period_label: str
    period_start_ms: int
    period_end_ms: int
    day_a: PeriodBookSnapshot
    day_b: PeriodBookSnapshot
    deposit_native: dict[str, Decimal]
    withdraw_native: dict[str, Decimal]
    earned_native: dict[str, Decimal]
    earned_usdc: dict[str, Decimal]
    total_usdc_earned: Decimal
    performance_fee: Decimal
    management_fee: Decimal
    performance_fee_rate: Decimal
    management_fee_annual_rate: Decimal
    nav_perf_start: Decimal
    nav_perf_end: Decimal
    nav_perf_change: Decimal
    collateral_spot_start: Decimal
    collateral_spot_end: Decimal
    total_equity_change: Decimal
    usdc_equiv_change: dict[str, Decimal]
    net_flow_usdc: Decimal
    distributable_profit: Decimal
    index_end: dict[str, Decimal]
    period_flow_lines: tuple[SubscriptionFlowLine, ...]
    closed_trades: tuple[dict[str, Any], ...]
    hwm_start: Decimal
    hwm_end: Decimal


def _day_label(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _native_from_snapshot(snap: dict[str, Any] | None, book: str) -> Decimal:
    if not snap:
        return Decimal("0")
    native = snap.get("equity_native_by_book") or {}
    if book in native:
        return to_decimal(native[book])
    usdc = to_decimal((snap.get("equity_usdc_by_book") or snap.get("equity_by_book") or {}).get(book, 0))
    if book == "USDC":
        return usdc
    idx = to_decimal(snap.get("index_btc_usd" if book == "BTC" else "index_eth_usd", 0))
    return usdc / idx if idx > 0 else Decimal("0")


def _usdc_from_snapshot(snap: dict[str, Any] | None, book: str) -> Decimal:
    if not snap:
        return Decimal("0")
    usdc = snap.get("equity_usdc_by_book") or snap.get("equity_by_book") or {}
    return to_decimal(usdc.get(book, 0))


def _snapshot_to_period_view(snap: dict[str, Any] | None, *, fallback_label: str) -> PeriodBookSnapshot:
    if not snap:
        empty = {book: Decimal("0") for book in _BOOKS}
        return PeriodBookSnapshot(
            label=fallback_label,
            ts_ms=0,
            native=empty,
            usdc_equiv=empty,
            total_equity_usdc=Decimal("0"),
        )
    native = {book: _native_from_snapshot(snap, book) for book in _BOOKS}
    usdc = {book: _usdc_from_snapshot(snap, book) for book in _BOOKS}
    return PeriodBookSnapshot(
        label=_day_label(int(snap["ts_ms"])),
        ts_ms=int(snap["ts_ms"]),
        native=native,
        usdc_equiv=usdc,
        total_equity_usdc=to_decimal(snap.get("total_equity_usdc", 0)),
    )


def _split_period_flows(
    lines: tuple[SubscriptionFlowLine, ...],
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal]]:
    deposit = {book: Decimal("0") for book in _BOOKS}
    withdraw = {book: Decimal("0") for book in _BOOKS}
    net = {book: Decimal("0") for book in _BOOKS}
    for line in lines:
        if not line.included_in_subscription:
            continue
        book = str(line.book).upper()
        net[book] = net.get(book, Decimal("0")) + line.amount_native
        if line.flow_type in _DEPOSIT_FLOW_TYPES and line.amount_native > 0:
            deposit[book] = deposit.get(book, Decimal("0")) + line.amount_native
        elif line.flow_type in _WITHDRAW_FLOW_TYPES and line.amount_native < 0:
            withdraw[book] = withdraw.get(book, Decimal("0")) + (-line.amount_native)
        elif line.amount_native > 0:
            deposit[book] = deposit.get(book, Decimal("0")) + line.amount_native
        elif line.amount_native < 0:
            withdraw[book] = withdraw.get(book, Decimal("0")) + (-line.amount_native)
    return deposit, withdraw, net


def _earned_native(
    day_a: dict[str, Decimal],
    day_b: dict[str, Decimal],
    net_flow: dict[str, Decimal],
) -> dict[str, Decimal]:
    return {
        book: day_b.get(book, Decimal("0")) - day_a.get(book, Decimal("0")) - net_flow.get(book, Decimal("0"))
        for book in _BOOKS
    }


def fetch_period_closed_trades(
    investor: str,
    *,
    repo_root: Any,
    start_ms: int,
    end_ms: int,
    index_by_ccy: dict[str, Decimal],
) -> tuple[dict[str, Any], ...]:
    root = find_repo_root(repo_root)
    if root is None:
        return ()
    try:
        manifest = load_investor_manifest(investor, repo_root=root)
    except ConfigurationError:
        return ()
    rows: list[dict[str, Any]] = []

    for account in manifest.operational_accounts():
        try:
            config = load_config(account.env_path)
        except Exception:
            continue
        state_path = config.state_file
        if not state_path.exists():
            continue
        store = StrategyStateStore(state_path)
        state = store.load()
        excluded = load_performance_exclusion_group_ids(state_path)
        open_short_names = open_short_instrument_names(state.groups)
        account_label = account.display_name or account.slug

        for group in state.groups:
            if group.status != "closed":
                continue
            if group.group_id in excluded:
                continue
            if is_phantom_reconcile_close(group, open_short_names=open_short_names):
                continue
            closed_ms = int(group.closed_timestamp_ms or 0)
            if closed_ms < start_ms or closed_ms > end_ms:
                continue
            book = (group.collateral_currency or group.currency or "USDC").upper()
            spot = index_by_ccy.get(book, Decimal("1") if book == "USDC" else Decimal("0"))
            journal_rows = None
            if group.is_coin_collateral():
                from .frontend_server import _journal_executions_for_group

                journal_rows = _journal_executions_for_group(state_path, group.group_id)
            group.backfill_realized_pnl_collateral_native(
                spot_index_usd=spot if spot > 0 else None,
                journal_executions=journal_rows,
            )
            pnl_native = group.realized_pnl_collateral_native or Decimal("0")
            pnl_usdc = group.realized_pnl
            if pnl_usdc is None and spot > 0:
                pnl_usdc = pnl_native * spot if book != "USDC" else pnl_native
            rows.append(
                {
                    "closed_timestamp_ms": closed_ms,
                    "account": account_label,
                    "group_id": group.group_id,
                    "strategy": group.strategy,
                    "short_instrument": group.short_instrument_name,
                    "collateral": book,
                    "realized_pnl_native": pnl_native,
                    "realized_pnl_usdc": pnl_usdc or Decimal("0"),
                    "close_reason": group.close_reason or "",
                    "quantity": group.quantity,
                }
            )

    rows.sort(key=lambda item: int(item["closed_timestamp_ms"]))
    return tuple(rows)


def build_investor_period_report(
    ctx: Any,
    *,
    repo_root: Any,
) -> InvestorPeriodReport:
    s = ctx.settlement
    start_ms = int(s["period_start_ms"])
    end_ms = int(s["period_end_ms"])
    included = tuple(line for line in ctx.quarter_flow_lines if line.included_in_subscription)

    day_a_view = _snapshot_to_period_view(ctx.start_snapshot, fallback_label="Period start")
    day_b_view = _snapshot_to_period_view(ctx.end_snapshot, fallback_label="Period end")
    deposit, withdraw, net_flow = _split_period_flows(included)
    earned = _earned_native(day_a_view.native, day_b_view.native, net_flow)

    index_end = {
        "BTC": to_decimal((ctx.end_snapshot or {}).get("index_btc_usd", ctx.index_by_ccy["BTC"])),
        "ETH": to_decimal((ctx.end_snapshot or {}).get("index_eth_usd", ctx.index_by_ccy["ETH"])),
        "USDC": Decimal("1"),
    }
    earned_usdc = {
        book: native_book_amount_to_usdc(earned[book], book, index_end) for book in _BOOKS
    }

    closed_trades = fetch_period_closed_trades(
        ctx.investor_id,
        repo_root=repo_root,
        start_ms=start_ms,
        end_ms=end_ms,
        index_by_ccy=index_end,
    )

    nav_perf_start = to_decimal(s.get("nav_perf_start", 0))
    nav_perf_end = to_decimal(s.get("nav_perf_end", 0))
    if ctx.start_snapshot and nav_perf_start == 0:
        nav_perf_start = to_decimal(ctx.start_snapshot.get("nav_perf", 0))
    if ctx.end_snapshot and nav_perf_end == 0:
        nav_perf_end = to_decimal(ctx.end_snapshot.get("nav_perf", 0))
    collateral_spot_start = to_decimal((ctx.start_snapshot or {}).get("collateral_spot_usdc", 0))
    collateral_spot_end = to_decimal((ctx.end_snapshot or {}).get("collateral_spot_usdc", 0))
    total_equity_change = day_b_view.total_equity_usdc - day_a_view.total_equity_usdc
    usdc_equiv_change = {
        book: day_b_view.usdc_equiv[book] - day_a_view.usdc_equiv[book] for book in _BOOKS
    }
    net_flow_usdc = to_decimal(s["net_flow_usdc"])
    distributable_profit = to_decimal(s["distributable_profit"])

    return InvestorPeriodReport(
        investor_id=ctx.investor_id,
        display_name=ctx.display_name,
        period_label=ctx.period,
        period_start_ms=start_ms,
        period_end_ms=end_ms,
        day_a=day_a_view,
        day_b=day_b_view,
        deposit_native=deposit,
        withdraw_native=withdraw,
        earned_native=earned,
        earned_usdc=earned_usdc,
        total_usdc_earned=to_decimal(s.get("period_nav_perf_pnl", 0)),
        performance_fee=to_decimal(s["performance_fee"]),
        management_fee=to_decimal(s["management_fee"]),
        performance_fee_rate=to_decimal(ctx.fee_config["performance_fee_rate"]),
        management_fee_annual_rate=to_decimal(ctx.fee_config["management_fee_annual_rate"]),
        nav_perf_start=nav_perf_start,
        nav_perf_end=nav_perf_end,
        nav_perf_change=nav_perf_end - nav_perf_start,
        collateral_spot_start=collateral_spot_start,
        collateral_spot_end=collateral_spot_end,
        total_equity_change=total_equity_change,
        usdc_equiv_change=usdc_equiv_change,
        net_flow_usdc=net_flow_usdc,
        distributable_profit=distributable_profit,
        index_end=index_end,
        period_flow_lines=included,
        closed_trades=closed_trades,
        hwm_start=to_decimal(s["hwm_start"]),
        hwm_end=to_decimal(s["hwm_end"]),
    )


def _total_usdc(book_map: dict[str, Decimal], index_by_ccy: dict[str, Decimal]) -> Decimal:
    return sum(
        (native_book_amount_to_usdc(book_map[book], book, index_by_ccy) for book in _BOOKS),
        Decimal("0"),
    )


def _signed_money(amount: Decimal | str, *, places: int = 2) -> str:
    text = _money(amount, places=places)
    value = to_decimal(amount)
    if value > 0 and not text.startswith("+"):
        return f"+{text}"
    return text


def _usdc_equiv_total(book_map: dict[str, Decimal]) -> Decimal:
    return sum((book_map[book] for book in _BOOKS), Decimal("0"))


def render_period_summary_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.append(f"# Period Statement — {report.display_name}")
    lines.append("")
    lines.append(f"- Investor: `{report.investor_id}`")
    lines.append(f"- Period: `{report.period_label}`")
    lines.append(f"- From: `{report.day_a.label}`")
    lines.append(f"- To: `{report.day_b.label}`")
    lines.append(
        f"- Index at period end: BTC `{_money(report.index_end['BTC'])}` / "
        f"ETH `{_money(report.index_end['ETH'])}` USDC"
    )
    lines.append("")
    lines.append("## Account balances")
    lines.append("")
    lines.append("| | BTC | ETH | USDC |")
    lines.append("|---|-----|-----|------|")
    lines.append(
        f"| **Day A** ({report.day_a.label}) | "
        f"`{_native(report.day_a.native['BTC'], 'BTC')}` | "
        f"`{_native(report.day_a.native['ETH'], 'ETH')}` | "
        f"`{_native(report.day_a.native['USDC'], 'USDC')}` |"
    )
    lines.append(
        f"| **Day B** ({report.day_b.label}) | "
        f"`{_native(report.day_b.native['BTC'], 'BTC')}` | "
        f"`{_native(report.day_b.native['ETH'], 'ETH')}` | "
        f"`{_native(report.day_b.native['USDC'], 'USDC')}` |"
    )
    lines.append(
        f"| **Period deposits** | "
        f"`{_native(report.deposit_native['BTC'], 'BTC')}` | "
        f"`{_native(report.deposit_native['ETH'], 'ETH')}` | "
        f"`{_native(report.deposit_native['USDC'], 'USDC')}` |"
    )
    lines.append(
        f"| **Period withdrawals** | "
        f"`{_native(report.withdraw_native['BTC'], 'BTC')}` | "
        f"`{_native(report.withdraw_native['ETH'], 'ETH')}` | "
        f"`{_native(report.withdraw_native['USDC'], 'USDC')}` |"
    )
    lines.append(
        f"| **Earned** (balance change − deposits + withdrawals) | "
        f"`{_signed_native(report.earned_native['BTC'], 'BTC')}` | "
        f"`{_signed_native(report.earned_native['ETH'], 'ETH')}` | "
        f"`{_signed_native(report.earned_native['USDC'], 'USDC')}` |"
    )
    lines.append(
        f"| **USDC equivalent** (at period-end index) | "
        f"`{_money(report.earned_usdc['BTC'])}` | "
        f"`{_money(report.earned_usdc['ETH'])}` | "
        f"`{_money(report.earned_usdc['USDC'])}` |"
    )
    lines.append("")
    lines.append("## USDC-equivalent balances")
    lines.append("")
    lines.append("| | BTC | ETH | USDC | **Total** |")
    lines.append("|---|-----|-----|------|-----------|")
    lines.append(
        f"| **Day A** ({report.day_a.label}) | "
        f"`{_money(report.day_a.usdc_equiv['BTC'])}` | "
        f"`{_money(report.day_a.usdc_equiv['ETH'])}` | "
        f"`{_money(report.day_a.usdc_equiv['USDC'])}` | "
        f"**`{_money(_usdc_equiv_total(report.day_a.usdc_equiv))}`** |"
    )
    lines.append(
        f"| **Day B** ({report.day_b.label}) | "
        f"`{_money(report.day_b.usdc_equiv['BTC'])}` | "
        f"`{_money(report.day_b.usdc_equiv['ETH'])}` | "
        f"`{_money(report.day_b.usdc_equiv['USDC'])}` | "
        f"**`{_money(_usdc_equiv_total(report.day_b.usdc_equiv))}`** |"
    )
    lines.append(
        f"| **Change (B − A)** | "
        f"`{_signed_money(report.usdc_equiv_change['BTC'])}` | "
        f"`{_signed_money(report.usdc_equiv_change['ETH'])}` | "
        f"`{_signed_money(report.usdc_equiv_change['USDC'])}` | "
        f"**`{_signed_money(report.total_equity_change)}`** |"
    )
    lines.append("")
    lines.append("## NAV & performance fee (USDC)")
    lines.append("")
    lines.append("| Step | Amount (USDC) |")
    lines.append("|------|---------------|")
    lines.append(f"| Total equity at Day A | `{_money(_usdc_equiv_total(report.day_a.usdc_equiv))}` |")
    lines.append(f"| Total equity at Day B | `{_money(_usdc_equiv_total(report.day_b.usdc_equiv))}` |")
    lines.append(f"| Collateral spot deducted (Day A) | `-{_money(report.collateral_spot_start)}` |")
    lines.append(f"| Collateral spot deducted (Day B) | `-{_money(report.collateral_spot_end)}` |")
    lines.append(f"| **NAV_perf at Day A** | **`{_money(report.nav_perf_start)}`** |")
    lines.append(f"| **NAV_perf at Day B** | **`{_money(report.nav_perf_end)}`** |")
    lines.append(f"| **NAV_perf change (B − A)** | **`{_signed_money(report.nav_perf_change)}`** |")
    lines.append(f"| Net subscription in period | `{_money(report.net_flow_usdc)}` |")
    lines.append(f"| **Strategy P&L (period)** | **`{_money(report.total_usdc_earned)}`** |")
    lines.append(f"| HWM at period start | `{_money(report.hwm_start)}` |")
    lines.append(f"| **Distributable profit (above HWM)** | **`{_money(report.distributable_profit)}`** |")
    lines.append(
        f"| **Performance fee ({float(report.performance_fee_rate) * 100:.0f}%)** | "
        f"**`{_money(report.performance_fee)}`** |"
    )
    lines.append(f"| HWM after fee | `{_money(report.hwm_end)}` |")
    lines.append("")
    lines.append("## Fees (USDC)")
    lines.append("")
    lines.append(
        f"- **Strategy P&L (period NAV change)**: **`{_money(report.total_usdc_earned)}`**"
    )
    lines.append(
        f"- **Distributable profit (above HWM)**: **`{_money(report.distributable_profit)}`**"
    )
    lines.append(
        f"- **Performance fee** ({float(report.performance_fee_rate) * 100:.0f}%): **`{_money(report.performance_fee)}`**"
    )
    lines.append(
        f"- **Management fee**: **`{_money(report.management_fee)}`**"
    )
    lines.append(
        f"- Deposit total (USDC equiv.): `{_money(_total_usdc(report.deposit_native, report.index_end))}` · "
        f"Withdrawal total (USDC equiv.): `{_money(_total_usdc(report.withdraw_native, report.index_end))}`"
    )
    lines.append(
        "- *Strategy P&L* = NAV_perf at Day B − NAV_perf at Day A − net subscription. "
        "*Distributable profit* = max(0, NAV_perf at Day B − HWM at start − net subscription). "
        "Performance fee applies to distributable profit, not collateral spot price moves."
    )
    lines.append("")
    return lines


def _signed_native(amount: Decimal, book: str) -> str:
    text = _native(amount, book)
    if amount > 0 and not text.startswith("+"):
        return f"+{text}"
    return text


def render_period_flows_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.append("## Period cash movements")
    lines.append("")
    lines.append("| Time (UTC) | Account | Type | Currency | Amount | USDC equiv. |")
    lines.append("|------------|---------|------|----------|--------|-------------|")
    if not report.period_flow_lines:
        lines.append("| — | — | — | — | — | — |")
    for line in report.period_flow_lines:
        lines.append(
            f"| {_ts_fmt(line.timestamp_ms)} | {line.identity_label} | {line.flow_type} | "
            f"{line.book} | `{_signed_native(line.amount_native, line.book)}` | `{_money(line.usdc_equiv)}` |"
        )
    lines.append("")
    return lines


def render_period_trades_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.append("## Closed option trades (period)")
    lines.append("")
    lines.append(
        "| Closed (UTC) | Account | Strategy | Instrument | Collateral | "
        "Qty | PnL (native) | PnL (USDC) | Reason |"
    )
    lines.append(
        "|--------------|---------|----------|------------|------------|"
        "-----|--------------|------------|--------|"
    )
    if not report.closed_trades:
        lines.append("| — | — | — | — | — | — | — | — | — |")
    for row in report.closed_trades:
        book = str(row["collateral"])
        pnl_native = to_decimal(row["realized_pnl_native"])
        lines.append(
            f"| {_ts_fmt(int(row['closed_timestamp_ms']))} | {row['account']} | {row['strategy']} | "
            f"`{row['short_instrument']}` | {book} | {_native(to_decimal(row['quantity']), book)} | "
            f"`{_signed_native(pnl_native, book)}` | `{_money(row['realized_pnl_usdc'])}` | "
            f"{row.get('close_reason') or '—'} |"
        )
    lines.append("")
    return lines
