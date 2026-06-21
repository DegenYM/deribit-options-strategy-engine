"""English investor fee reports (PDF, CSV, Markdown) for bootstrap and quarterly settlements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .env_layout import find_repo_root, load_investor_manifest, resolve_investor_env_path
from .fee_snapshot_store import FeeSnapshotStore, FlowBaselineRow, fee_ledger_db_path
from .investor_cash_flow import (
    SubscriptionFlowLine,
    effective_fee_flow_start_ms,
    fetch_subscription_flow_lines,
    initial_spot_deduction_usdc,
    native_book_amount_to_usdc,
    ordered_net_flow_books,
    parse_fee_flow_start_ms,
)
from .investor_fee_config import load_investor_fee_config
from .investor_nav_snapshot import (
    InvestorNavCapture,
    _snapshot_dict,
    capture_investor_nav,
    is_quarter_period,
    parse_quarter_period,
)
from .utils import to_decimal


@dataclass(frozen=True)
class FeeReportOutput:
    """Paths written by ``write_*_fee_report``."""

    markdown: Path | None
    pdf: Path | None
    flows_csv: Path | None
    summary_csv: Path | None
    trades_csv: Path | None = None

    @property
    def primary(self) -> Path:
        """Preferred path for investors (PDF, else summary CSV)."""
        if self.pdf is not None:
            return self.pdf
        if self.summary_csv is not None:
            return self.summary_csv
        if self.flows_csv is not None:
            return self.flows_csv
        if self.markdown is not None:
            return self.markdown
        if self.trades_csv is not None:
            return self.trades_csv
        raise RuntimeError("No report files were written")


def fee_reports_dir(repo_root: Path, investor_id: str) -> Path:
    return repo_root / "data" / "fee_ledger" / investor_id / "reports"


def initial_report_dir(repo_root: Path, investor_id: str) -> Path:
    return fee_reports_dir(repo_root, investor_id) / "initial"


def initial_report_path(repo_root: Path, investor_id: str, *, ts_ms: int | None = None) -> Path:
    stamp = _file_stamp(ts_ms)
    return initial_report_dir(repo_root, investor_id) / f"initial-{stamp}.md"


def settlement_report_dir(
    repo_root: Path,
    investor_id: str,
    *,
    period_end_ms: int,
) -> Path:
    day = datetime.fromtimestamp(period_end_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
    return fee_reports_dir(repo_root, investor_id) / day


def settlement_report_path(
    repo_root: Path,
    investor_id: str,
    period: str,
    *,
    period_end_ms: int,
) -> Path:
    return (
        settlement_report_dir(
            repo_root,
            investor_id,
            period_end_ms=period_end_ms,
        )
        / f"settlement-{period}.md"
    )


def _file_stamp(ts_ms: int | None) -> str:
    if ts_ms is None:
        return datetime.now(tz=UTC).strftime("%Y%m%d")
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y%m%d")


def _ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def settlement_period_heading(period: str) -> str:
    if is_quarter_period(period):
        return "Investor Quarterly Settlement"
    return "Investor Period Settlement"


def settlement_period_range_label(
    period: str,
    *,
    period_start_ms: int,
    period_end_ms: int,
) -> str:
    if is_quarter_period(period):
        q_start, q_end = parse_quarter_period(period)
        return f"{period} ({q_start.date()} – {q_end.date()} UTC)"
    return f"{_ts_fmt(period_start_ms)} – {_ts_fmt(period_end_ms)} UTC ({period})"


def settlement_period_flow_label(period: str) -> str:
    return "quarter" if is_quarter_period(period) else "period"


def _money(x: Decimal | str, *, places: int = 2) -> str:
    value = to_decimal(x)
    fmt = f"{{0:.{places}f}}"
    return fmt.format(value)


def _native(x: Decimal | str, book: str) -> str:
    places = 8 if book in {"BTC", "ETH"} else 2
    return _money(x, places=places)


def _net_flow_book_breakdown(
    native_by_book: dict[str, Decimal],
    *,
    index_by_ccy: dict[str, Decimal],
) -> list[tuple[str, Decimal, Decimal]]:
    """Return (book, native net flow, USDC equivalent) rows in display order."""
    rows: list[tuple[str, Decimal, Decimal]] = []
    for book in ordered_net_flow_books(native_by_book):
        native = native_by_book[book]
        rows.append((book, native, native_book_amount_to_usdc(native, book, index_by_ccy)))
    return rows


def _equity_native_for_book(
    book: str,
    *,
    equity_native_by_book: dict[str, Decimal],
    equity_by_book: dict[str, Decimal],
    index_by_ccy: dict[str, Decimal],
) -> Decimal:
    if book == "USDC":
        return equity_by_book.get(book, equity_native_by_book.get(book, Decimal("0")))
    native = equity_native_by_book.get(book)
    if native is not None:
        return native
    usdc = equity_by_book.get(book, Decimal("0"))
    if book == "USDC":
        return usdc
    idx = index_by_ccy.get(book, Decimal("0"))
    if idx <= 0:
        return Decimal("0")
    return usdc / idx


def _append_current_equity_md(
    lines: list[str],
    *,
    equity_by_book: dict[str, Decimal],
    equity_native_by_book: dict[str, Decimal],
    total_equity_usdc: Decimal,
    index_by_ccy: dict[str, Decimal],
) -> None:
    lines.append("| Currency | Native equity | USDC equivalent |")
    lines.append("|----------|---------------|-----------------|")
    for book in ("BTC", "ETH", "USDC"):
        native = _equity_native_for_book(
            book,
            equity_native_by_book=equity_native_by_book,
            equity_by_book=equity_by_book,
            index_by_ccy=index_by_ccy,
        )
        usdc = equity_by_book.get(book, Decimal("0"))
        unit = book if book == "USDC" else book
        lines.append(f"| {book} | `{_native(native, book)}` {unit} | `{_money(usdc)}` |")
    lines.append(f"| **Total** | — | **`{_money(total_equity_usdc)}`** |")
    lines.append("")


def _append_initial_hwm_breakdown_md(
    lines: list[str],
    *,
    native_by_book: dict[str, Decimal],
    index_by_ccy: dict[str, Decimal],
    cumulative_net_flow_usdc: Decimal,
    initial_hwm_nav_perf: Decimal,
    index_footer: bool = True,
) -> None:
    btc_native = native_by_book.get("BTC", Decimal("0"))
    eth_native = native_by_book.get("ETH", Decimal("0"))
    btc_usdc = native_book_amount_to_usdc(btc_native, "BTC", index_by_ccy)
    eth_usdc = native_book_amount_to_usdc(eth_native, "ETH", index_by_ccy)
    lines.append("### Net subscription & initial spot → Initial HWM")
    lines.append("")
    lines.append("| Line | Native | USDC equivalent |")
    lines.append("|------|--------|-----------------|")
    usdc_native = native_by_book.get("USDC", Decimal("0"))
    lines.append(
        f"| USDC net subscription | `{_native(usdc_native, 'USDC')}` | "
        f"`{_money(native_book_amount_to_usdc(usdc_native, 'USDC', index_by_ccy))}` |"
    )
    lines.append(f"| Initial spot to deduct (BTC) | `{_native(btc_native, 'BTC')}` | `-{_money(btc_usdc)}` |")
    lines.append(f"| Initial spot to deduct (ETH) | `{_native(eth_native, 'ETH')}` | `-{_money(eth_usdc)}` |")
    lines.append(f"| **Net subscription total** | — | **`{_money(cumulative_net_flow_usdc)}`** |")
    lines.append(f"| **Initial HWM (NAV_perf)** | — | **`{_money(initial_hwm_nav_perf)}`** |")
    lines.append("")
    if index_footer:
        lines.append(
            f"- Index prices (BTC/ETH → USDC): BTC `{_money(index_by_ccy['BTC'])}` / "
            f"ETH `{_money(index_by_ccy['ETH'])}`"
        )
    lines.append(
        "- Net subscription total = sum of BTC/ETH/USDC deposit, withdrawal, and transfer "
        "(internal sub-account transfers net to zero)."
    )
    lines.append(
        "- Initial spot rows use the **BTC/ETH net subscription** from the transaction log; "
        "these amounts are deducted from the total to set Initial HWM (option margin base)."
    )
    lines.append("- Formula: `Initial HWM = max(0, net subscription total USDC − BTC spot USDC − ETH spot USDC)`")
    lines.append("")


def _append_snapshot_equity_book_md(
    lines: list[str],
    *,
    heading: str,
    snap: dict[str, Any] | None,
) -> None:
    lines.append(f"### {heading}")
    lines.append("")
    if not snap:
        lines.append("- (No snapshot on file.)")
        lines.append("")
        return
    lines.append(f"- Snapshot time: `{_ts_fmt(int(snap['ts_ms']))}`")
    if snap.get("index_btc_usd") is not None and snap.get("index_eth_usd") is not None:
        lines.append(
            f"- Index prices: BTC `{_money(snap['index_btc_usd'])}` / ETH `{_money(snap['index_eth_usd'])}` USDC"
        )
    lines.append("")
    native = snap.get("equity_native_by_book") or {}
    usdc = snap.get("equity_usdc_by_book") or snap.get("equity_by_book") or {}
    lines.append("| Currency | Native balance | USDC equivalent |")
    lines.append("|----------|----------------|-----------------|")
    for book in ("BTC", "ETH", "USDC"):
        lines.append(f"| {book} | `{_native(to_decimal(native.get(book, 0)), book)}` | `{_money(usdc.get(book, 0))}` |")
    lines.append(f"| **Total equity** | — | **`{_money(snap['total_equity_usdc'])}`** |")
    btc_spot = to_decimal(snap.get("agreed_spot_btc_native", 0))
    eth_spot = to_decimal(snap.get("agreed_spot_eth_native", 0))
    if btc_spot > 0 or eth_spot > 0:
        idx_btc = to_decimal(snap.get("index_btc_usd", 0))
        idx_eth = to_decimal(snap.get("index_eth_usd", 0))
        lines.append(f"| Agreed spot deducted (BTC) | `{_native(btc_spot, 'BTC')}` | `-{_money(btc_spot * idx_btc)}` |")
        lines.append(f"| Agreed spot deducted (ETH) | `{_native(eth_spot, 'ETH')}` | `-{_money(eth_spot * idx_eth)}` |")
    if snap.get("collateral_spot_usdc") is not None:
        lines.append(f"| **Collateral spot (total)** | — | **`-{_money(snap['collateral_spot_usdc'])}`** |")
    lines.append(f"| **NAV_perf** (fee basis) | — | **`{_money(snap['nav_perf'])}`** |")
    lines.append("")


def _append_settlement_fee_walkthrough_md(
    lines: list[str],
    *,
    settlement: dict[str, Any],
    start_snapshot: dict[str, Any] | None,
    end_snapshot: dict[str, Any] | None,
    fee_config: dict[str, str],
    flow_baseline: FlowBaselineRow | None,
) -> None:
    s = settlement
    perf_pct = float(fee_config["performance_fee_rate"]) * 100
    lines.append("## 2. Equity & NAV_perf walkthrough (USDC)")
    lines.append("")
    if end_snapshot:
        source = end_snapshot.get("agreed_spot_source", "bootstrap_deposits")
        lines.append(
            f"- **Agreed collateral spot** (excluded from NAV_perf, not manager P&L): "
            f"BTC `{_native(to_decimal(end_snapshot.get('agreed_spot_btc_native', 0)), 'BTC')}`, "
            f"ETH `{_native(to_decimal(end_snapshot.get('agreed_spot_eth_native', 0)), 'ETH')}` "
            f"(source: `{source}`)."
        )
        if flow_baseline is not None and source == "bootstrap_deposits":
            lines.append(
                f"- Matches initial bootstrap: BTC/ETH net subscriptions from the transaction log "
                f"(initial HWM `{_money(flow_baseline.initial_hwm_nav_perf)}` USDC)."
            )
    lines.append("- **NAV_perf** at each date = total equity − agreed spot (USDC equivalent at that day's index).")
    lines.append("- **Strategy P&L (USDC)** for the period = NAV_perf at end − NAV_perf at start − net subscription.")
    lines.append("")

    _append_snapshot_equity_book_md(lines, heading="2.1 Period start", snap=start_snapshot)
    _append_snapshot_equity_book_md(lines, heading="2.2 Period end", snap=end_snapshot)

    lines.append("### 2.3 Performance fee calculation (USDC)")
    lines.append("")
    lines.append("| Step | Amount (USDC) |")
    lines.append("|------|---------------|")
    if start_snapshot:
        lines.append(f"| NAV_perf at period start | `{_money(start_snapshot['nav_perf'])}` |")
    if end_snapshot:
        lines.append(f"| NAV_perf at period end | `{_money(end_snapshot['nav_perf'])}` |")
    period_pnl = to_decimal(s.get("period_nav_perf_pnl", 0))
    net_flow = to_decimal(s["net_flow_usdc"])
    lines.append(f"| Period NAV_perf change (before subscription) | `{_money(period_pnl + net_flow)}` |")
    lines.append(f"| Net subscription in period | `{_money(net_flow)}` |")
    lines.append(f"| **Strategy P&L (NAV_perf, USDC)** | `{_money(period_pnl)}` |")
    lines.append(f"| HWM at period start | `{_money(s['hwm_start'])}` |")
    lines.append(f"| **Distributable profit** | `{_money(s['distributable_profit'])}` |")
    lines.append(f"| **Performance fee ({perf_pct:.0f}%)** | `{_money(s['performance_fee'])}` |")
    lines.append(f"| HWM after fee | `{_money(s['hwm_end'])}` |")
    lines.append("")
    lines.append("- Distributable profit = `max(0, NAV_perf at end − HWM at start − net subscription in period)`.")
    lines.append("")


@dataclass(frozen=True)
class InitialFeeReportContext:
    investor_id: str
    display_name: str
    generated_at_ms: int
    baseline: FlowBaselineRow | None
    flow_lines: tuple[SubscriptionFlowLine, ...]
    fee_config: dict[str, str]
    collateral_spot_usdc: Decimal
    index_by_ccy: dict[str, Decimal]
    live_nav: dict[str, str] | None


@dataclass(frozen=True)
class SettlementFeeReportContext:
    investor_id: str
    display_name: str
    period: str
    generated_at_ms: int
    settlement: dict[str, Any]
    start_snapshot: dict[str, Any] | None
    end_snapshot: dict[str, Any] | None
    quarter_flow_lines: tuple[SubscriptionFlowLine, ...]
    fee_config: dict[str, str]
    index_by_ccy: dict[str, Decimal]
    flow_baseline: FlowBaselineRow | None = None


def build_initial_report_context(
    investor: str | Path,
    *,
    repo_root: Path | str,
    capture: InvestorNavCapture | None = None,
) -> InitialFeeReportContext:
    root = find_repo_root(repo_root)
    if root is None:
        raise RuntimeError("Cannot locate repository root")
    manifest = load_investor_manifest(investor, repo_root=root)
    fee_config = load_investor_fee_config(manifest.root)
    store = FeeSnapshotStore(fee_ledger_db_path(root, manifest.investor_id))
    baseline = store.load_flow_baseline(manifest.investor_id)

    if capture is None:
        capture = capture_investor_nav(investor, repo_root=root)

    index_by_ccy = {
        "BTC": capture.index_btc_usd,
        "ETH": capture.index_eth_usd,
        "USDC": Decimal("1"),
    }
    from dotenv import dotenv_values

    investor_env = resolve_investor_env_path(manifest.root)
    env_values = dict(dotenv_values(investor_env)) if investor_env is not None else {}
    if baseline is not None:
        start_ms = effective_fee_flow_start_ms(baseline.start_timestamp_ms)
    else:
        start_ms = parse_fee_flow_start_ms(env_values)
    end_ms = baseline.end_timestamp_ms if baseline is not None else capture.ts_ms
    flow_lines = tuple(
        fetch_subscription_flow_lines(
            manifest.root,
            repo_root=root,
            index_by_ccy=index_by_ccy,
            start_timestamp_ms=start_ms,
            end_timestamp_ms=end_ms,
        )
    )
    return InitialFeeReportContext(
        investor_id=manifest.investor_id,
        display_name=manifest.display_name,
        generated_at_ms=capture.ts_ms,
        baseline=baseline,
        flow_lines=flow_lines,
        fee_config={
            "collateral_spot_btc": str(fee_config.collateral_spot_btc),
            "collateral_spot_eth": str(fee_config.collateral_spot_eth),
            "performance_fee_rate": str(fee_config.performance_fee_rate),
            "management_fee_annual_rate": str(fee_config.management_fee_annual_rate),
        },
        collateral_spot_usdc=capture.collateral_spot_usdc,
        index_by_ccy=index_by_ccy,
        live_nav={
            "total_equity_usdc": str(capture.total_equity_usdc),
            "equity_by_book": {k: str(v) for k, v in capture.equity_by_book.items()},
            "equity_native_by_book": {k: str(v) for k, v in capture.equity_native_by_book.items()},
            "ts_ms": str(capture.ts_ms),
        },
    )


def build_settlement_report_context(
    investor: str | Path,
    period: str,
    *,
    repo_root: Path | str,
    settlement_payload: dict[str, Any] | None = None,
    period_flow_lines: tuple[SubscriptionFlowLine, ...] | list[SubscriptionFlowLine] | None = None,
    index_by_ccy: dict[str, Decimal] | None = None,
) -> SettlementFeeReportContext:
    root = find_repo_root(repo_root)
    if root is None:
        raise RuntimeError("Cannot locate repository root")
    manifest = load_investor_manifest(investor, repo_root=root)
    fee_config = load_investor_fee_config(manifest.root)
    store = FeeSnapshotStore(fee_ledger_db_path(root, manifest.investor_id))

    flow_baseline = store.load_flow_baseline(manifest.investor_id)
    if settlement_payload is None:
        row = store.settlement_for_period(manifest.investor_id, period)
        if row is None:
            raise RuntimeError(f"No settlement stored for period {period!r}")
        settlement_payload = {
            "period": row.period,
            "period_start_ms": row.period_start_ms,
            "period_end_ms": row.period_end_ms,
            "hwm_start": str(row.hwm_start),
            "nav_perf_start": str(row.nav_perf_start),
            "nav_perf_end": str(row.nav_perf_end),
            "net_flow_usdc": str(row.net_flow_usdc),
            "distributable_profit": str(row.distributable_profit),
            "performance_fee": str(row.performance_fee),
            "hwm_end": str(row.hwm_end),
            "avg_aum_mgmt": str(row.avg_aum_mgmt),
            "management_fee": str(row.management_fee),
            "settled_at_ms": row.settled_at_ms,
        }
    if settlement_payload.get("start_snapshot") is None:
        start_row = store.snapshot_nearest(
            manifest.investor_id,
            target_ts_ms=int(settlement_payload["period_start_ms"]),
            max_delta_ms=7 * 86_400_000,
        )
        settlement_payload = {
            **settlement_payload,
            "start_snapshot": _snapshot_dict(start_row, fee_config=fee_config, flow_baseline=flow_baseline),
        }
    if settlement_payload.get("end_snapshot") is None:
        end_row = store.snapshot_nearest(
            manifest.investor_id,
            target_ts_ms=int(settlement_payload["period_end_ms"]),
            max_delta_ms=7 * 86_400_000,
        )
        settlement_payload = {
            **settlement_payload,
            "end_snapshot": _snapshot_dict(end_row, fee_config=fee_config, flow_baseline=flow_baseline),
        }

    if index_by_ccy is None:
        end_snapshot = settlement_payload.get("end_snapshot") or {}
        index_by_ccy = {
            "BTC": to_decimal(end_snapshot.get("index_btc_usd") or "0"),
            "ETH": to_decimal(end_snapshot.get("index_eth_usd") or "0"),
            "USDC": Decimal("1"),
        }
        if index_by_ccy["BTC"] <= 0 or index_by_ccy["ETH"] <= 0:
            capture = capture_investor_nav(investor, repo_root=root)
            index_by_ccy = {
                "BTC": capture.index_btc_usd,
                "ETH": capture.index_eth_usd,
                "USDC": Decimal("1"),
            }
        else:
            capture = None
    else:
        capture = None

    start_ms = int(settlement_payload["period_start_ms"])
    end_ms = int(settlement_payload["period_end_ms"])
    if period_flow_lines is None:
        cached = settlement_payload.get("period_flow_lines")
        if cached:
            period_flow_lines = tuple(cached)
    if period_flow_lines is None:
        quarter_lines = tuple(
            fetch_subscription_flow_lines(
                manifest.root,
                repo_root=root,
                index_by_ccy=index_by_ccy,
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
            )
        )
    else:
        quarter_lines = tuple(period_flow_lines)

    generated_at_ms = int(settlement_payload.get("settled_at_ms") or end_ms)
    if capture is not None:
        generated_at_ms = capture.ts_ms

    return SettlementFeeReportContext(
        investor_id=manifest.investor_id,
        display_name=manifest.display_name,
        period=period,
        generated_at_ms=generated_at_ms,
        settlement=settlement_payload,
        start_snapshot=settlement_payload.get("start_snapshot"),
        end_snapshot=settlement_payload.get("end_snapshot"),
        quarter_flow_lines=quarter_lines,
        flow_baseline=store.load_flow_baseline(manifest.investor_id),
        fee_config={
            "collateral_spot_btc": str(fee_config.collateral_spot_btc),
            "collateral_spot_eth": str(fee_config.collateral_spot_eth),
            "performance_fee_rate": str(fee_config.performance_fee_rate),
            "management_fee_annual_rate": str(fee_config.management_fee_annual_rate),
        },
        index_by_ccy=index_by_ccy,
    )


def render_initial_fee_report_md(ctx: InitialFeeReportContext) -> str:
    lines: list[str] = []
    lines.append(f"# Investor Initial Fee Baseline — {ctx.display_name} ({ctx.investor_id})")
    lines.append("")
    lines.append(f"- Generated: `{_ts_fmt(ctx.generated_at_ms)}`")
    lines.append("- Report type: strategy inception / first HWM bootstrap")
    lines.append("")

    lines.append("## 1. Current equity (at generation time)")
    if ctx.live_nav:
        equity_by_book = {str(k).upper(): to_decimal(v) for k, v in (ctx.live_nav.get("equity_by_book") or {}).items()}
        equity_native_by_book = {
            str(k).upper(): to_decimal(v) for k, v in (ctx.live_nav.get("equity_native_by_book") or {}).items()
        }
        _append_current_equity_md(
            lines,
            equity_by_book=equity_by_book,
            equity_native_by_book=equity_native_by_book,
            total_equity_usdc=to_decimal(ctx.live_nav["total_equity_usdc"]),
            index_by_ccy=ctx.index_by_ccy,
        )
    else:
        lines.append("")

    lines.append("## 2. Fee rates")
    lines.append(f"- Performance fee: `{float(ctx.fee_config['performance_fee_rate']) * 100:.1f}%`")
    lines.append(f"- Management fee (annual): `{float(ctx.fee_config['management_fee_annual_rate']) * 100:.2f}%`")
    lines.append("")

    lines.append("## 3. Cumulative net subscriptions & initial HWM")
    if ctx.baseline is not None:
        lines.append(f"- Data source: `{ctx.baseline.source}`")
        scan_start_ms = effective_fee_flow_start_ms(ctx.baseline.start_timestamp_ms)
        lines.append(f"- Scan window: `{_ts_fmt(scan_start_ms)}` → `{_ts_fmt(ctx.baseline.end_timestamp_ms)}`")
        lines.append(f"- Entry count (deposit + withdrawal): `{ctx.baseline.entry_count}`")
        lines.append(f"- Bootstrap time: `{_ts_fmt(ctx.baseline.bootstrapped_at_ms)}`")
        lines.append("")
        _append_initial_hwm_breakdown_md(
            lines,
            native_by_book=ctx.baseline.net_flow_native_by_book,
            index_by_ccy=ctx.index_by_ccy,
            cumulative_net_flow_usdc=ctx.baseline.cumulative_net_flow_usdc,
            initial_hwm_nav_perf=ctx.baseline.initial_hwm_nav_perf,
            index_footer=False,
        )
    else:
        lines.append("- (Flow baseline not stored; figures below are from the current Deribit log.)")
        lines.append("")
        native_by_book: dict[str, Decimal] = {}
        for line in ctx.flow_lines:
            if not line.included_in_subscription:
                continue
            native_by_book[line.book] = native_by_book.get(line.book, Decimal("0")) + line.amount_native
        cumulative = sum(
            (native_book_amount_to_usdc(amount, book, ctx.index_by_ccy) for book, amount in native_by_book.items()),
            Decimal("0"),
        )
        _btc, _eth, _spot, initial_hwm = initial_spot_deduction_usdc(native_by_book, index_by_ccy=ctx.index_by_ccy)
        _append_initial_hwm_breakdown_md(
            lines,
            native_by_book=native_by_book,
            index_by_ccy=ctx.index_by_ccy,
            cumulative_net_flow_usdc=cumulative,
            initial_hwm_nav_perf=initial_hwm,
        )
    lines.append("")

    lines.extend(_render_flow_tables(ctx.flow_lines, section="4"))
    lines.append("## Notes")
    lines.append(
        "- Net subscription = sum of `deposit`, `withdrawal`, and `transfer` (USDC equiv.) "
        "across all configured sub-account APIs; internal sub transfers cancel in the total."
    )
    lines.append("- Main-account funding without a main API appears as inbound `transfer` on the sub-account.")
    lines.append("- Generated by `fee-report --kind initial` or automatically on first `fee-snapshot` bootstrap.")
    return "\n".join(lines) + "\n"


def render_settlement_fee_report_md(
    ctx: SettlementFeeReportContext,
    *,
    repo_root: Path | str,
) -> str:
    from .investor_fee_report_period import (
        build_investor_period_report,
        render_period_summary_md,
    )

    report = build_investor_period_report(ctx, repo_root=repo_root)
    lines: list[str] = []
    lines.extend(render_period_summary_md(report))
    lines.append("---")
    lines.append("*Generated by `fee-settle-period` / `fee-report --kind settlement`.*")
    lines.append("")
    return "\n".join(lines)


def _render_flow_tables(
    flow_lines: tuple[SubscriptionFlowLine, ...],
    *,
    section: str,
    period_label: str | None = None,
) -> list[str]:
    title = f"## {section}. Cash flow detail"
    if period_label:
        title += f" ({period_label})"
    lines: list[str] = [title, ""]

    included = [row for row in flow_lines if row.included_in_subscription]
    excluded = [row for row in flow_lines if not row.included_in_subscription]

    subtotal_usdc = sum((row.usdc_equiv for row in included), Decimal("0"))
    lines.append(f"### {section}.1 Counted toward subscription")
    lines.append(f"- Subtotal (USDC equivalent): **{_money(subtotal_usdc)}**")
    lines.append("")
    lines.append("| Time (UTC) | Account | Type | Currency | Amount | USDC equiv. |")
    lines.append("|------------|---------|------|----------|--------|-------------|")
    if not included:
        lines.append("| — | — | — | — | — | — |")
    for row in included:
        lines.append(
            f"| {_ts_fmt(row.timestamp_ms)} | {row.identity_label} | {row.flow_type} | "
            f"{row.book} | `{_signed_native(row.amount_native, row.book)}` | `{_money(row.usdc_equiv)}` |"
        )
    lines.append("")

    if excluded:
        lines.append(f"### {section}.2 Audit only (excluded from net flow)")
        lines.append("| Time (UTC) | Account | Type | Currency | Amount | USDC equiv. |")
        lines.append("|------------|---------|------|----------|--------|-------------|")
        for row in excluded:
            lines.append(
                f"| {_ts_fmt(row.timestamp_ms)} | {row.identity_label} | {row.flow_type} | "
                f"{row.book} | `{_signed_native(row.amount_native, row.book)}` | `{_money(row.usdc_equiv)}` |"
            )
        lines.append("")
    return lines


def _signed_native(amount: Decimal, book: str) -> str:
    text = _native(amount, book)
    if amount > 0 and not text.startswith("+"):
        return f"+{text}"
    return text


def _snapshot_row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "ts_ms": row.ts_ms,
        "snapshot_kind": row.snapshot_kind,
        "total_equity_usdc": str(row.total_equity_usdc),
        "collateral_spot_usdc": str(row.collateral_spot_usdc),
        "collateral_spot_btc": str(row.collateral_spot_btc),
        "collateral_spot_eth": str(row.collateral_spot_eth),
        "nav_perf": str(row.nav_perf),
        "aum_mgmt": str(row.aum_mgmt),
        "index_btc_usd": str(row.index_btc_usd),
        "index_eth_usd": str(row.index_eth_usd),
        "equity_by_book": {k: str(v) for k, v in row.equity_by_book.items()},
        "notes": row.notes,
    }


def _report_stem_path(output_path: Path | None, default_md: Path) -> Path:
    if output_path is None:
        return default_md
    suffix = output_path.suffix.lower()
    if suffix in {".md", ".pdf", ".csv"}:
        return output_path.with_suffix(".md")
    return output_path / default_md.name if output_path.is_dir() else output_path.with_suffix(".md")


def write_initial_fee_report(
    investor: str | Path,
    *,
    repo_root: Path | str,
    capture: InvestorNavCapture | None = None,
    output_path: Path | None = None,
    write_pdf: bool = True,
    write_markdown: bool = True,
    write_csv: bool = False,
) -> FeeReportOutput:
    ctx = build_initial_report_context(investor, repo_root=repo_root, capture=capture)
    root = find_repo_root(repo_root)
    assert root is not None
    default_md = initial_report_path(
        root, ctx.investor_id, ts_ms=ctx.baseline.bootstrapped_at_ms if ctx.baseline else ctx.generated_at_ms
    )
    md_path = _report_stem_path(output_path, default_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    stem = md_path.with_suffix("")
    pdf_path = stem.with_suffix(".pdf")
    flows_csv = stem.with_name(f"{stem.name}-flows").with_suffix(".csv")
    summary_csv = stem.with_name(f"{stem.name}-summary").with_suffix(".csv")

    written_md: Path | None = None
    written_pdf: Path | None = None
    written_flows: Path | None = None
    written_summary: Path | None = None

    if write_markdown:
        md_path.write_text(render_initial_fee_report_md(ctx), encoding="utf-8")
        written_md = md_path
    if write_pdf:
        from .investor_fee_report_pdf import write_initial_fee_report_pdf

        write_initial_fee_report_pdf(ctx, pdf_path)
        written_pdf = pdf_path
    if write_csv:
        from .investor_fee_report_csv import write_initial_fee_report_csv

        written_flows, written_summary = write_initial_fee_report_csv(
            ctx, flows_path=flows_csv, summary_path=summary_csv
        )
    return FeeReportOutput(
        markdown=written_md,
        pdf=written_pdf,
        flows_csv=written_flows,
        summary_csv=written_summary,
    )


def write_settlement_fee_report(
    investor: str | Path,
    period: str,
    *,
    repo_root: Path | str,
    settlement_payload: dict[str, Any] | None = None,
    period_flow_lines: tuple[SubscriptionFlowLine, ...] | list[SubscriptionFlowLine] | None = None,
    index_by_ccy: dict[str, Decimal] | None = None,
    output_path: Path | None = None,
    write_pdf: bool = True,
    write_markdown: bool = True,
    write_csv: bool = False,
) -> FeeReportOutput:
    ctx = build_settlement_report_context(
        investor,
        period,
        repo_root=repo_root,
        settlement_payload=settlement_payload,
        period_flow_lines=period_flow_lines,
        index_by_ccy=index_by_ccy,
    )
    root = find_repo_root(repo_root)
    assert root is not None
    default_md = settlement_report_path(
        root,
        ctx.investor_id,
        period,
        period_end_ms=int(ctx.settlement["period_end_ms"]),
    )
    md_path = _report_stem_path(output_path, default_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    stem = md_path.with_suffix("")
    pdf_path = stem.with_suffix(".pdf")
    flows_csv = stem.with_name(f"{stem.name}-flows").with_suffix(".csv")
    summary_csv = stem.with_name(f"{stem.name}-summary").with_suffix(".csv")
    trades_csv = stem.with_name(f"{stem.name}-trades").with_suffix(".csv")

    written_md: Path | None = None
    written_pdf: Path | None = None
    written_flows: Path | None = None
    written_summary: Path | None = None
    written_trades: Path | None = None

    if write_markdown:
        md_path.write_text(render_settlement_fee_report_md(ctx, repo_root=root), encoding="utf-8")
        written_md = md_path
    if write_pdf:
        from .investor_fee_report_pdf import write_settlement_fee_report_pdf

        write_settlement_fee_report_pdf(ctx, pdf_path, repo_root=root)
        written_pdf = pdf_path
    if write_csv:
        from .investor_fee_report_csv import write_settlement_fee_report_csv

        written_flows, written_summary, written_trades = write_settlement_fee_report_csv(
            ctx,
            repo_root=root,
            flows_path=flows_csv,
            summary_path=summary_csv,
            trades_path=trades_csv,
        )
    return FeeReportOutput(
        markdown=written_md,
        pdf=written_pdf,
        flows_csv=written_flows,
        summary_csv=written_summary,
        trades_csv=written_trades,
    )
