"""Investor-facing period settlement summary (simple balances + fees + trade log)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import load_config
from .env_layout import find_repo_root, load_investor_manifest
from .exceptions import ConfigurationError
from .investor_cash_flow import (
    SubscriptionFlowLine,
    native_book_amount_to_usdc,
)
from .models import TradeGroup, is_phantom_reconcile_close, open_short_instrument_names
from .state import StrategyStateStore, load_performance_exclusion_group_ids
from .utils import to_decimal

LOGGER = logging.getLogger(__name__)

_BOOKS: tuple[str, ...] = ("BTC", "ETH", "USDC", "USDT")
_DEPOSIT_FLOW_TYPES = frozenset({"deposit", "transfer_in"})
_WITHDRAW_FLOW_TYPES = frozenset({"withdrawal", "transfer_out"})
_SNAPSHOT_BOUNDARY_SLACK_MS = 3600_000


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
    uses_realized_balance: bool = False


@dataclass(frozen=True)
class PeriodTradeSummary:
    closed_count: int
    win_count: int
    total_pnl_usdc: Decimal

    @property
    def win_rate_pct(self) -> Decimal | None:
        if self.closed_count <= 0:
            return None
        return Decimal(self.win_count * 100) / Decimal(self.closed_count)


@dataclass(frozen=True)
class RealizedTradingPnl:
    """Matches investor dashboard Total profit (options + perp hedge)."""

    options_pnl_usdc: Decimal
    hedge_pnl_usdc: Decimal
    closed_count: int

    @property
    def total_pnl_usdc(self) -> Decimal:
        return self.options_pnl_usdc + self.hedge_pnl_usdc


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
    mtm_equity_start_usdc: Decimal
    mtm_equity_end_usdc: Decimal
    mtm_snapshot_end_ts_ms: int
    usdc_equiv_change: dict[str, Decimal]
    net_flow_usdc: Decimal
    net_flow_usdc_raw: Decimal | None
    fee_payment_usdc_excluded: Decimal
    distributable_profit: Decimal
    index_end: dict[str, Decimal]
    period_flow_lines: tuple[SubscriptionFlowLine, ...]
    closed_trades: tuple[dict[str, Any], ...]
    lifetime_closed_trades: tuple[dict[str, Any], ...]
    hwm_start: Decimal
    hwm_end: Decimal
    avg_aum_mgmt: Decimal
    aum_mgmt_start: Decimal
    aum_mgmt_end: Decimal
    generated_at_ms: int
    period_return_pct: Decimal | None
    equity_return_pct: Decimal | None
    total_fees_due: Decimal
    trade_summary: PeriodTradeSummary
    realized_trading_pnl: RealizedTradingPnl
    lifetime_realized_trading_pnl: RealizedTradingPnl
    dashboard_total_profit_usdc: Decimal

    @property
    def is_quarterly(self) -> bool:
        from .investor_nav_snapshot import is_quarter_period

        return is_quarter_period(self.period_label)

    @property
    def executive_profit(self) -> RealizedTradingPnl:
        """Headline profit block: lifetime when it differs from the statement period."""
        period = self.realized_trading_pnl
        if abs(self.dashboard_total_profit_usdc - period.total_pnl_usdc) < Decimal("0.01"):
            return period
        return self.lifetime_realized_trading_pnl

    @property
    def fee_basis_trading_profit_usdc(self) -> Decimal:
        return fee_basis_trading_profit_usdc(
            self.realized_trading_pnl,
            self.lifetime_realized_trading_pnl,
        )

    @property
    def uses_lifetime_profit(self) -> bool:
        return self.executive_profit is not self.realized_trading_pnl

    @property
    def statement_closed_trades(self) -> tuple[dict[str, Any], ...]:
        """Trade rows aligned with Total profit (lifetime when dashboard differs)."""
        if self.uses_lifetime_profit:
            return self.lifetime_closed_trades
        return self.closed_trades

    @property
    def statement_trade_summary(self) -> PeriodTradeSummary:
        return compute_trade_summary(self.statement_closed_trades)

    @property
    def period_start_boundary_label(self) -> str:
        return _day_label(self.period_start_ms)

    @property
    def period_end_boundary_label(self) -> str:
        return _day_label(self.period_end_ms)

    @property
    def opening_nav_row_label(self) -> str:
        if self.day_a.ts_ms > 0:
            return f"Opening balance ({self.day_a.label})"
        return "Opening balance (no stored snapshot, baseline zero)"

    @property
    def closing_nav_row_label(self) -> str:
        if self.day_b.uses_realized_balance:
            return f"Closing balance (realized, through {self.period_end_boundary_label})"
        return f"Closing balance ({self.day_b.label})"

    @property
    def balance_table_uses_realized_only(self) -> bool:
        return self.day_b.uses_realized_balance

    @property
    def shows_opening_nav_snapshot(self) -> bool:
        return self.day_a.ts_ms > 0 and nav_snapshot_differs_from_boundary(
            self.day_a.ts_ms,
            self.period_start_ms,
        )

    @property
    def shows_closing_nav_snapshot(self) -> bool:
        ts_ms = self.mtm_snapshot_end_ts_ms if self.day_b.uses_realized_balance else self.day_b.ts_ms
        return nav_snapshot_differs_from_boundary(ts_ms, self.period_end_ms)

    @property
    def closing_mtm_snapshot_label(self) -> str:
        if self.mtm_snapshot_end_ts_ms <= 0:
            return ""
        return _day_label(self.mtm_snapshot_end_ts_ms)

    @property
    def nav_snapshot_end_note(self) -> str | None:
        if self.day_b.uses_realized_balance:
            parts = [
                "Closing balance includes only realized P&L from closed trades and premium sweeps, "
                "not open-position marks or unrealized MTM."
            ]
            if self.mtm_snapshot_end_ts_ms > 0:
                parts.append(
                    f"MTM account equity at period end was ${_money(self.mtm_equity_end_usdc)} "
                    f"(snapshot {self.closing_mtm_snapshot_label})."
                )
            return " ".join(parts)
        if not self.shows_closing_nav_snapshot:
            return None
        return (
            "Transfers and closed trades are counted through the period end above. "
            f"Ending equity uses the nearest stored NAV snapshot ({self.closing_mtm_snapshot_label or self.day_b.label})."
        )


def nav_snapshot_differs_from_boundary(snapshot_ts_ms: int, boundary_ts_ms: int) -> bool:
    """True when a stored NAV snapshot is not aligned with the accounting boundary."""
    if snapshot_ts_ms <= 0:
        return False
    return abs(snapshot_ts_ms - boundary_ts_ms) > _SNAPSHOT_BOUNDARY_SLACK_MS


def iter_period_header_fields(report: InvestorPeriodReport) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("Investor", report.investor_id),
        ("Period", report.period_label),
        ("Period start", report.period_start_boundary_label),
        ("Period end", report.period_end_boundary_label),
    ]
    if report.shows_opening_nav_snapshot:
        fields.append(("Opening NAV snapshot", report.day_a.label))
    if report.shows_closing_nav_snapshot:
        label = report.closing_mtm_snapshot_label or report.day_b.label
        fields.append(("Closing NAV snapshot (MTM)", label))
    fields.append(("Generated", _ts_fmt(report.generated_at_ms)))
    return fields


def render_period_header_lines(report: InvestorPeriodReport) -> list[str]:
    """Investor-facing period metadata: accounting boundaries vs NAV snapshot times."""
    lines = [f"- {label}: `{value}`" for label, value in iter_period_header_fields(report)]
    note = report.nav_snapshot_end_note
    if note:
        lines.append(f"- Note: {note}")
    return lines


def fee_basis_trading_profit_usdc(
    period: RealizedTradingPnl,
    lifetime: RealizedTradingPnl,
) -> Decimal:
    """Performance fee basis: dashboard Total profit (lifetime when period differs)."""
    if abs(lifetime.total_pnl_usdc - period.total_pnl_usdc) < Decimal("0.01"):
        return max(Decimal("0"), period.total_pnl_usdc)
    return max(Decimal("0"), lifetime.total_pnl_usdc)


def compute_trading_profit_performance_fee(
    profit_usdc: Decimal,
    *,
    performance_fee_rate: Decimal,
) -> tuple[Decimal, Decimal]:
    distributable = max(Decimal("0"), profit_usdc)
    return distributable, distributable * performance_fee_rate


def period_report_title(report: InvestorPeriodReport) -> str:
    if report.is_quarterly:
        return f"Quarterly Settlement Statement — {report.display_name}"
    return f"Period Statement — {report.display_name}"


def compute_period_return_pct(
    *,
    nav_perf_start: Decimal,
    net_flow_usdc: Decimal,
    realized_trading_pnl: Decimal,
) -> Decimal | None:
    base = nav_perf_start + net_flow_usdc / 2
    if base <= 0:
        return None
    return (realized_trading_pnl / base) * Decimal("100")


def _spot_index_from_end(index_end: dict[str, Decimal]) -> dict[str, Decimal]:
    return {k: v for k, v in index_end.items() if k in ("BTC", "ETH") and v > 0}


def sum_realized_options_pnl_usdc(
    closed_trades: tuple[dict[str, Any], ...],
) -> Decimal:
    return sum((to_decimal(row.get("realized_pnl_usdc", 0)) for row in closed_trades), Decimal("0"))


def fetch_investor_premium_sweep_fill_stats(
    investor: str,
    *,
    repo_root: Any,
) -> dict[str, dict[str, str]] | None:
    """Exchange VWAP for premium sweeps — same source as the investor dashboard."""
    from .client import DeribitClient
    from .frontend_server.aggregation import _aggregate_premium_sweep_fill_stats
    from .profit_sweep_repair import premium_sweep_fill_stats_by_book

    root = find_repo_root(repo_root)
    if root is None:
        return None
    try:
        manifest = load_investor_manifest(investor, repo_root=root)
    except ConfigurationError:
        return None
    statuses: list[dict[str, Any]] = []
    for account in manifest.operational_accounts():
        try:
            config = load_config(account.env_path, require_private=True)
        except Exception:
            continue
        if not config.has_private_credentials:
            continue
        try:
            client = DeribitClient(config)
            stats = premium_sweep_fill_stats_by_book(client, config.order_label_prefix)
            if stats:
                statuses.append({"premium_sweep_fill_stats_by_book": stats})
        except Exception:
            LOGGER.warning(
                "premium_sweep_fill_stats_failed investor=%s account=%s",
                investor,
                account.slug,
                exc_info=True,
            )
    return _aggregate_premium_sweep_fill_stats(statuses)


def sum_realized_options_pnl_dashboard(
    closed_group_rows: tuple[dict[str, Any], ...],
    *,
    spot_index: dict[str, Decimal],
    fill_stats: dict[str, dict[str, str]] | None = None,
) -> Decimal:
    """Dashboard-aligned options PnL: swapped USDT + unswept native × spot index."""
    from .realized_summary import _row_realized_pnl_usdc, total_realized_usdc_from_swap_disposition

    rows = [
        row
        for row in closed_group_rows
        if row.get("realized_pnl") is not None and row.get("closed_timestamp_ms") is not None
    ]
    if not rows:
        return Decimal("0")
    total = total_realized_usdc_from_swap_disposition(rows, spot_index=spot_index, fill_stats=fill_stats)
    if total is not None:
        return total
    return sum((_row_realized_pnl_usdc(row, spot_index) for row in rows), Decimal("0"))


def fetch_investor_hedge_pnl_usdc(
    investor: str,
    *,
    repo_root: Any,
    since_ms: int | None = None,
) -> Decimal:
    from .config import load_config
    from .hedge_pnl import merge_hedge_pnl_summaries, summarize_hedge_pnl_for_state

    root = find_repo_root(repo_root)
    if root is None:
        return Decimal("0")
    try:
        manifest = load_investor_manifest(investor, repo_root=root)
    except ConfigurationError:
        return Decimal("0")
    parts: list[dict[str, Any]] = []
    for account in manifest.operational_accounts():
        try:
            config = load_config(account.env_path)
        except Exception:
            continue
        if not config.state_file.exists():
            continue
        parts.append(summarize_hedge_pnl_for_state(config.state_file, since_ms=since_ms))
    if not parts:
        return Decimal("0")
    merged = merge_hedge_pnl_summaries(parts)
    return to_decimal(merged.get("net_pnl_usdc", 0))


def build_realized_trading_pnl(
    investor: str,
    *,
    repo_root: Any,
    start_ms: int,
    end_ms: int,
    index_by_ccy: dict[str, Decimal],
) -> tuple[RealizedTradingPnl, RealizedTradingPnl, tuple[dict[str, Any], ...]]:
    """Return (period, lifetime, period closed trade rows) using dashboard rules."""
    spot_index = _spot_index_from_end(index_by_ccy)
    fill_stats = fetch_investor_premium_sweep_fill_stats(investor, repo_root=repo_root)
    period_groups = fetch_period_closed_group_dicts(
        investor,
        repo_root=repo_root,
        start_ms=start_ms,
        end_ms=end_ms,
        index_by_ccy=index_by_ccy,
    )
    lifetime_groups = fetch_period_closed_group_dicts(
        investor,
        repo_root=repo_root,
        start_ms=0,
        end_ms=end_ms,
        index_by_ccy=index_by_ccy,
    )
    period_trades = fetch_period_closed_trades(
        investor,
        repo_root=repo_root,
        start_ms=start_ms,
        end_ms=end_ms,
        index_by_ccy=index_by_ccy,
    )
    period = RealizedTradingPnl(
        options_pnl_usdc=sum_realized_options_pnl_dashboard(
            period_groups,
            spot_index=spot_index,
            fill_stats=fill_stats,
        ),
        hedge_pnl_usdc=fetch_investor_hedge_pnl_usdc(investor, repo_root=repo_root, since_ms=start_ms),
        closed_count=len(period_trades),
    )
    lifetime = RealizedTradingPnl(
        options_pnl_usdc=sum_realized_options_pnl_dashboard(
            lifetime_groups,
            spot_index=spot_index,
            fill_stats=fill_stats,
        ),
        hedge_pnl_usdc=fetch_investor_hedge_pnl_usdc(investor, repo_root=repo_root, since_ms=None),
        closed_count=len(lifetime_groups),
    )
    return period, lifetime, period_trades


def sum_realized_native_by_book_from_groups(
    closed_group_rows: tuple[dict[str, Any], ...],
    *,
    spot_index: dict[str, Decimal],
    fill_stats: dict[str, dict[str, str]] | None = None,
    hedge_pnl_usdc: Decimal = Decimal("0"),
) -> dict[str, Decimal]:
    """Per-book native delta from closed groups (dashboard swap disposition rules)."""
    from .realized_summary import _aggregate_profit_disposition, _summarize_profit_disposition

    out = {book: Decimal("0") for book in _BOOKS}
    disposition = _aggregate_profit_disposition(list(closed_group_rows))
    if disposition is not None:
        summary = _summarize_profit_disposition(
            disposition,
            spot_index=spot_index,
            fill_stats=fill_stats,
        )
        for book in ("BTC", "ETH"):
            out[book] += summary["spot_held"].get(book, Decimal("0"))
            out[book] += summary["spot_pending"].get(book, Decimal("0"))
            out["USDT"] += summary["spot_sold_quote"].get(book, Decimal("0"))
        out["USDC"] += disposition["held_native"].get("USDC", Decimal("0"))
    if hedge_pnl_usdc != 0:
        out["USDC"] += hedge_pnl_usdc
    return out


def _build_realized_closing_view(
    *,
    opening_native: dict[str, Decimal],
    net_flow: dict[str, Decimal],
    period_realized_native: dict[str, Decimal],
    index_end: dict[str, Decimal],
    period_end_ms: int,
    mtm_snapshot_ts_ms: int,
) -> PeriodBookSnapshot:
    closing_native = {
        book: opening_native.get(book, Decimal("0"))
        + net_flow.get(book, Decimal("0"))
        + period_realized_native.get(book, Decimal("0"))
        for book in _BOOKS
    }
    closing_usdc = {book: native_book_amount_to_usdc(closing_native[book], book, index_end) for book in _BOOKS}
    return PeriodBookSnapshot(
        label=_day_label(period_end_ms),
        ts_ms=mtm_snapshot_ts_ms,
        native=closing_native,
        usdc_equiv=closing_usdc,
        total_equity_usdc=sum(closing_usdc.values(), Decimal("0")),
        uses_realized_balance=True,
    )


def compute_equity_return_pct(
    *,
    equity_start: Decimal,
    equity_change: Decimal,
) -> Decimal | None:
    if equity_start <= 0:
        return None
    return (equity_change / equity_start) * Decimal("100")


def compute_trade_summary(closed_trades: tuple[dict[str, Any], ...]) -> PeriodTradeSummary:
    total_pnl = Decimal("0")
    win_count = 0
    for row in closed_trades:
        pnl = to_decimal(row.get("realized_pnl_usdc", 0))
        total_pnl += pnl
        if pnl > 0:
            win_count += 1
    return PeriodTradeSummary(
        closed_count=len(closed_trades),
        win_count=win_count,
        total_pnl_usdc=total_pnl,
    )


def end_allocation_rows(report: InvestorPeriodReport) -> list[tuple[str, Decimal, Decimal]]:
    total = _usdc_equiv_total(report.day_b.usdc_equiv)
    if total <= 0:
        return []
    rows: list[tuple[str, Decimal, Decimal]] = []
    for book in ("BTC", "ETH", "USDC", "USDT"):
        usdc = report.day_b.usdc_equiv[book]
        if usdc == 0:
            continue
        pct = (usdc / total) * Decimal("100")
        rows.append((book, usdc, pct))
    return rows


def _day_label(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _snapshot_to_period_view(snap: dict[str, Any] | None, *, fallback_label: str) -> PeriodBookSnapshot:
    if not snap:
        empty = {book: Decimal("0") for book in _BOOKS}
        return PeriodBookSnapshot(
            label=fallback_label,
            ts_ms=0,
            native=empty,
            usdc_equiv=empty,
            total_equity_usdc=Decimal("0"),
            uses_realized_balance=False,
        )
    native = {book: _native_from_snapshot(snap, book) for book in _BOOKS}
    usdc = {book: _usdc_from_snapshot(snap, book) for book in _BOOKS}
    total = to_decimal(snap.get("total_equity_usdc", 0))
    return PeriodBookSnapshot(
        label=_day_label(int(snap["ts_ms"])),
        ts_ms=int(snap["ts_ms"]),
        native=native,
        usdc_equiv=usdc,
        total_equity_usdc=total,
        uses_realized_balance=False,
    )


def _native_from_snapshot(snap: dict[str, Any] | None, book: str) -> Decimal:
    if not snap:
        return Decimal("0")
    native = snap.get("equity_native_by_book") or {}
    if book in native:
        return to_decimal(native[book])
    usdc = to_decimal((snap.get("equity_usdc_by_book") or snap.get("equity_by_book") or {}).get(book, 0))
    if book in ("USDC", "USDT"):
        return usdc
    idx = to_decimal(snap.get("index_btc_usd" if book == "BTC" else "index_eth_usd", 0))
    return usdc / idx if idx > 0 else Decimal("0")


def _usdc_from_snapshot(snap: dict[str, Any] | None, book: str) -> Decimal:
    if not snap:
        return Decimal("0")
    usdc = snap.get("equity_usdc_by_book") or snap.get("equity_by_book") or {}
    return to_decimal(usdc.get(book, 0))


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


def _iter_period_closed_groups(
    investor: str,
    *,
    repo_root: Any,
    start_ms: int,
    end_ms: int,
    index_by_ccy: dict[str, Decimal],
) -> Iterator[tuple[TradeGroup, str, Path]]:
    root = find_repo_root(repo_root)
    if root is None:
        return
    try:
        manifest = load_investor_manifest(investor, repo_root=root)
    except ConfigurationError:
        return

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
            yield group, account_label, state_path


def fetch_period_closed_group_dicts(
    investor: str,
    *,
    repo_root: Any,
    start_ms: int,
    end_ms: int,
    index_by_ccy: dict[str, Decimal],
) -> tuple[dict[str, Any], ...]:
    rows = [
        group.to_dict()
        for group, _account, _state_path in _iter_period_closed_groups(
            investor,
            repo_root=repo_root,
            start_ms=start_ms,
            end_ms=end_ms,
            index_by_ccy=index_by_ccy,
        )
    ]
    rows.sort(key=lambda item: int(item.get("closed_timestamp_ms") or 0))
    return tuple(rows)


def fetch_period_closed_trades(
    investor: str,
    *,
    repo_root: Any,
    start_ms: int,
    end_ms: int,
    index_by_ccy: dict[str, Decimal],
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    spot_index = {k: v for k, v in index_by_ccy.items() if k in ("BTC", "ETH") and v > 0}

    for group, account_label, _state_path in _iter_period_closed_groups(
        investor,
        repo_root=repo_root,
        start_ms=start_ms,
        end_ms=end_ms,
        index_by_ccy=index_by_ccy,
    ):
        closed_ms = int(group.closed_timestamp_ms or 0)
        book = (group.collateral_currency or group.currency or "USDC").upper()
        spot = index_by_ccy.get(book, Decimal("1") if book == "USDC" else Decimal("0"))
        pnl_native = group.realized_pnl_collateral_native or Decimal("0")
        from .realized_summary import realized_pnl_usdc_at_spot

        pnl_usdc = realized_pnl_usdc_at_spot(group.to_dict(), spot_index or None)
        if pnl_usdc is None:
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
    deposit, withdraw, net_flow = _split_period_flows(included)

    index_end = {
        "BTC": to_decimal((ctx.end_snapshot or {}).get("index_btc_usd", ctx.index_by_ccy["BTC"])),
        "ETH": to_decimal((ctx.end_snapshot or {}).get("index_eth_usd", ctx.index_by_ccy["ETH"])),
        "USDC": Decimal("1"),
        "USDT": Decimal("1"),
    }

    period_groups = fetch_period_closed_group_dicts(
        ctx.investor_id,
        repo_root=repo_root,
        start_ms=start_ms,
        end_ms=end_ms,
        index_by_ccy=index_end,
    )
    spot_index = _spot_index_from_end(index_end)
    fill_stats = fetch_investor_premium_sweep_fill_stats(ctx.investor_id, repo_root=repo_root)
    realized_trading_pnl, lifetime_realized, closed_trades_tuple = build_realized_trading_pnl(
        ctx.investor_id,
        repo_root=repo_root,
        start_ms=start_ms,
        end_ms=end_ms,
        index_by_ccy=index_end,
    )
    period_realized_native = sum_realized_native_by_book_from_groups(
        period_groups,
        spot_index=spot_index,
        fill_stats=fill_stats,
        hedge_pnl_usdc=realized_trading_pnl.hedge_pnl_usdc,
    )
    mtm_snapshot_ts_ms = int((ctx.end_snapshot or {}).get("ts_ms", 0) or 0)
    day_b_view = _build_realized_closing_view(
        opening_native=day_a_view.native,
        net_flow=net_flow,
        period_realized_native=period_realized_native,
        index_end=index_end,
        period_end_ms=end_ms,
        mtm_snapshot_ts_ms=mtm_snapshot_ts_ms,
    )
    earned = period_realized_native
    earned_usdc = {book: native_book_amount_to_usdc(earned[book], book, index_end) for book in _BOOKS}

    lifetime_closed_trades = fetch_period_closed_trades(
        ctx.investor_id,
        repo_root=repo_root,
        start_ms=0,
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
    mtm_equity_start = to_decimal((ctx.start_snapshot or {}).get("total_equity_usdc", 0))
    mtm_equity_end = to_decimal((ctx.end_snapshot or {}).get("total_equity_usdc", 0))
    total_equity_change = day_b_view.total_equity_usdc - day_a_view.total_equity_usdc
    usdc_equiv_change = {book: day_b_view.usdc_equiv[book] - day_a_view.usdc_equiv[book] for book in _BOOKS}
    net_flow_usdc = to_decimal(s["net_flow_usdc"])
    net_flow_usdc_raw = to_decimal(s["net_flow_usdc_raw"]) if s.get("net_flow_usdc_raw") is not None else None
    fee_payment_usdc_excluded = to_decimal(s.get("fee_payment_usdc_excluded", 0))
    avg_aum_mgmt = to_decimal(s.get("avg_aum_mgmt", 0))
    aum_mgmt_start = to_decimal((ctx.start_snapshot or {}).get("aum_mgmt", 0))
    aum_mgmt_end = to_decimal((ctx.end_snapshot or {}).get("aum_mgmt", 0))
    if aum_mgmt_start == 0 and ctx.start_snapshot:
        aum_mgmt_start = report_nav_perf_plus_spot(
            to_decimal(ctx.start_snapshot.get("nav_perf", nav_perf_start)),
            collateral_spot_start,
        )
    if aum_mgmt_end == 0 and ctx.end_snapshot:
        aum_mgmt_end = report_nav_perf_plus_spot(nav_perf_end, collateral_spot_end)
    strategy_pnl = to_decimal(s.get("period_nav_perf_pnl", 0))
    fee_basis = fee_basis_trading_profit_usdc(realized_trading_pnl, lifetime_realized)
    distributable_profit, performance_fee = compute_trading_profit_performance_fee(
        fee_basis,
        performance_fee_rate=to_decimal(ctx.fee_config["performance_fee_rate"]),
    )
    management_fee = to_decimal(s["management_fee"])
    generated_at_ms = int(getattr(ctx, "generated_at_ms", 0) or s.get("settled_at_ms") or end_ms)
    trade_summary = compute_trade_summary(closed_trades_tuple)
    period_return_pct = compute_period_return_pct(
        nav_perf_start=nav_perf_start,
        net_flow_usdc=net_flow_usdc,
        realized_trading_pnl=realized_trading_pnl.total_pnl_usdc,
    )
    equity_return_pct = compute_equity_return_pct(
        equity_start=day_a_view.total_equity_usdc,
        equity_change=total_equity_change,
    )

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
        total_usdc_earned=strategy_pnl,
        performance_fee=performance_fee,
        management_fee=management_fee,
        performance_fee_rate=to_decimal(ctx.fee_config["performance_fee_rate"]),
        management_fee_annual_rate=to_decimal(ctx.fee_config["management_fee_annual_rate"]),
        nav_perf_start=nav_perf_start,
        nav_perf_end=nav_perf_end,
        nav_perf_change=nav_perf_end - nav_perf_start,
        collateral_spot_start=collateral_spot_start,
        collateral_spot_end=collateral_spot_end,
        total_equity_change=total_equity_change,
        mtm_equity_start_usdc=mtm_equity_start,
        mtm_equity_end_usdc=mtm_equity_end,
        mtm_snapshot_end_ts_ms=mtm_snapshot_ts_ms,
        usdc_equiv_change=usdc_equiv_change,
        net_flow_usdc=net_flow_usdc,
        net_flow_usdc_raw=net_flow_usdc_raw,
        fee_payment_usdc_excluded=fee_payment_usdc_excluded,
        distributable_profit=distributable_profit,
        index_end=index_end,
        period_flow_lines=included,
        closed_trades=closed_trades_tuple,
        lifetime_closed_trades=lifetime_closed_trades,
        hwm_start=to_decimal(s["hwm_start"]),
        hwm_end=to_decimal(s["hwm_end"]),
        avg_aum_mgmt=avg_aum_mgmt,
        aum_mgmt_start=aum_mgmt_start,
        aum_mgmt_end=aum_mgmt_end,
        generated_at_ms=generated_at_ms,
        period_return_pct=period_return_pct,
        equity_return_pct=equity_return_pct,
        total_fees_due=performance_fee + management_fee,
        trade_summary=trade_summary,
        realized_trading_pnl=realized_trading_pnl,
        lifetime_realized_trading_pnl=lifetime_realized,
        dashboard_total_profit_usdc=lifetime_realized.total_pnl_usdc,
    )


def report_nav_perf_plus_spot(nav_perf: Decimal, collateral_spot_usdc: Decimal) -> Decimal:
    return nav_perf + collateral_spot_usdc


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


def _pct(amount: Decimal | None, *, places: int = 1) -> str:
    if amount is None:
        return "—"
    return f"{amount:.{places}f}%"


def render_period_executive_summary_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.append(f"# {period_report_title(report)}")
    lines.append("")
    lines.extend(render_period_header_lines(report))
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    equity_note = (
        f" (realized, through {report.period_end_boundary_label})"
        if report.day_b.uses_realized_balance
        else (f" (NAV snapshot {report.closing_mtm_snapshot_label})" if report.shows_closing_nav_snapshot else "")
    )
    lines.append(f"| Ending total equity{equity_note} | **`${_money(report.day_b.total_equity_usdc)}`** |")
    profit = report.executive_profit
    lines.append(f"| **Total profit** | **`${_signed_money(profit.total_pnl_usdc)}`** |")
    lines.append(f"| — Closed options (incl. profit-sweep USDT) | `${_signed_money(profit.options_pnl_usdc)}` |")
    lines.append(f"| — Perp hedge (net) | `${_signed_money(profit.hedge_pnl_usdc)}` |")
    lines.append(
        f"| Performance fee ({float(report.performance_fee_rate) * 100:.0f}% × Total profit) | "
        f"`${_money(report.performance_fee)}` |"
    )
    if report.management_fee > 0:
        lines.append(f"| Management fee | `${_money(report.management_fee)}` |")
    lines.append(f"| **Total fees due** | **`${_money(report.total_fees_due)}`** |")
    lines.append("")
    return lines


def render_period_comparison_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.append("## Period comparison")
    lines.append("")
    lines.append("| | Opening NAV | Closing NAV | Change |")
    lines.append("|---|-------------|-------------|--------|")
    lines.append(
        f"| **Total equity (USDC)** | `${_money(report.day_a.total_equity_usdc)}` | "
        f"`${_money(report.day_b.total_equity_usdc)}` | **`${_signed_money(report.total_equity_change)}`** |"
    )
    for book in _BOOKS:
        native_change = report.day_b.native[book] - report.day_a.native[book]
        usdc_change = report.usdc_equiv_change[book]
        lines.append(
            f"| {book} (native) | `{_native(report.day_a.native[book], book)}` | "
            f"`{_native(report.day_b.native[book], book)}` | `{_signed_native(native_change, book)}` |"
        )
        lines.append(
            f"| {book} (USDC equiv.) | `${_money(report.day_a.usdc_equiv[book])}` | "
            f"`${_money(report.day_b.usdc_equiv[book])}` | `${_signed_money(usdc_change)}` |"
        )
    lines.append("")
    return lines


def render_period_transfers_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.append("## Transfers (period)")
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
    lines.append(f"- Net subscription total: **`${_money(report.net_flow_usdc)}`** USDC equivalent")
    if report.fee_payment_usdc_excluded > 0 and report.net_flow_usdc_raw is not None:
        lines.append(
            f"- Deribit log net: `${_money(report.net_flow_usdc_raw)}`; "
            f"fee payment excluded: `${_money(report.fee_payment_usdc_excluded)}`"
        )
    lines.append("")
    return lines


def render_period_summary_md(report: InvestorPeriodReport) -> list[str]:
    lines: list[str] = []
    lines.extend(render_period_executive_summary_md(report))
    lines.extend(render_period_transfers_md(report))
    lines.append("## Detailed account balances")
    if report.balance_table_uses_realized_only:
        lines.append("")
        lines.append(
            "_Closing balance = opening + period transfers + realized P&L from closed trades "
            "(incl. premium sweeps). Excludes open-position unrealized marks._"
        )
    lines.append("")
    lines.append("| | BTC | ETH | USDC | USDT |")
    lines.append("|---|-----|-----|------|------|")
    lines.append(
        f"| **Day A** — {report.opening_nav_row_label} | "
        f"`{_native(report.day_a.native['BTC'], 'BTC')}` | "
        f"`{_native(report.day_a.native['ETH'], 'ETH')}` | "
        f"`{_native(report.day_a.native['USDC'], 'USDC')}` | "
        f"`{_native(report.day_a.native['USDT'], 'USDT')}` |"
    )
    lines.append(
        f"| **Day B** — {report.closing_nav_row_label} | "
        f"`{_native(report.day_b.native['BTC'], 'BTC')}` | "
        f"`{_native(report.day_b.native['ETH'], 'ETH')}` | "
        f"`{_native(report.day_b.native['USDC'], 'USDC')}` | "
        f"`{_native(report.day_b.native['USDT'], 'USDT')}` |"
    )
    lines.append(
        f"| **Period deposits** | "
        f"`{_native(report.deposit_native['BTC'], 'BTC')}` | "
        f"`{_native(report.deposit_native['ETH'], 'ETH')}` | "
        f"`{_native(report.deposit_native['USDC'], 'USDC')}` | "
        f"`{_native(report.deposit_native['USDT'], 'USDT')}` |"
    )
    lines.append(
        f"| **Period withdrawals** | "
        f"`{_native(report.withdraw_native['BTC'], 'BTC')}` | "
        f"`{_native(report.withdraw_native['ETH'], 'ETH')}` | "
        f"`{_native(report.withdraw_native['USDC'], 'USDC')}` | "
        f"`{_native(report.withdraw_native['USDT'], 'USDT')}` |"
    )
    lines.append(
        f"| **Realized earned** (closed trades) | "
        f"`{_signed_native(report.earned_native['BTC'], 'BTC')}` | "
        f"`{_signed_native(report.earned_native['ETH'], 'ETH')}` | "
        f"`{_signed_native(report.earned_native['USDC'], 'USDC')}` | "
        f"`{_signed_native(report.earned_native['USDT'], 'USDT')}` |"
    )
    lines.append(
        f"| **USDC equivalent** (at period-end index) | "
        f"`{_money(report.earned_usdc['BTC'])}` | "
        f"`{_money(report.earned_usdc['ETH'])}` | "
        f"`{_money(report.earned_usdc['USDC'])}` | "
        f"`{_money(report.earned_usdc['USDT'])}` |"
    )
    lines.append("")
    lines.extend(render_period_trades_md(report))
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
    scope = "lifetime, matches Total profit" if report.uses_lifetime_profit else "period"
    lines.append(f"## Closed option trades ({scope})")
    lines.append("")
    summary = report.statement_trade_summary
    profit = report.executive_profit
    if summary.closed_count:
        win_rate = _pct(summary.win_rate_pct)
        lines.append(
            f"- Closed groups ({scope}): `{summary.closed_count}` · Win rate: `{win_rate}` · "
            f"Options P&L: **`${_signed_money(profit.options_pnl_usdc)}`** · "
            f"Hedge P&L: **`${_signed_money(profit.hedge_pnl_usdc)}`**"
        )
    else:
        lines.append("- No closed option groups.")
    lines.append("")
    lines.append("| Closed (UTC) | Account | Instrument | Collateral | Qty | PnL (native) | PnL (USD) | Reason |")
    lines.append("|--------------|---------|------------|------------|-----|--------------|------------|--------|")
    if not report.statement_closed_trades:
        lines.append("| — | — | — | — | — | — | — | — |")
    for row in report.statement_closed_trades:
        book = str(row["collateral"])
        pnl_native = to_decimal(row["realized_pnl_native"])
        lines.append(
            f"| {_ts_fmt(int(row['closed_timestamp_ms']))} | {row['account']} | "
            f"`{row['short_instrument']}` | {book} | {_native(to_decimal(row['quantity']), book)} | "
            f"`{_signed_native(pnl_native, book)}` | `{_money(row['realized_pnl_usdc'])}` | "
            f"{row.get('close_reason') or '—'} |"
        )
    lines.append("")
    return lines
