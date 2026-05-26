"""Capture investor-level NAV_perf / AUM_mgmt snapshots for performance-fee billing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from .env_layout import find_repo_root, load_investor_manifest
from .fee_snapshot_store import (
    FeeSnapshotStore,
    FlowBaselineRow,
    NavSnapshotRow,
    fee_ledger_db_path,
)
from .investor_cash_flow import (
    fetch_cumulative_net_flow_usdc,
    flow_report_dict,
    initial_spot_deduction_usdc,
    native_book_amount_to_usdc,
)
from .investor_fee_config import InvestorFeeConfig, load_investor_fee_config
from .utils import utc_now_ms

# Reuse exchange prefetch per API login across fee CLI bursts (fee-settle-period, etc.).
_FEE_EXCHANGE_PREFETCH_CACHE: Any = None


def _fee_exchange_prefetch_cache() -> Any:
    global _FEE_EXCHANGE_PREFETCH_CACHE
    if _FEE_EXCHANGE_PREFETCH_CACHE is None:
        from .frontend_server import _TtlCache

        _FEE_EXCHANGE_PREFETCH_CACHE = _TtlCache(10.0)
    return _FEE_EXCHANGE_PREFETCH_CACHE


LOGGER = logging.getLogger(__name__)

_QUARTER_RE = re.compile(r"^(?P<year>\d{4})-Q(?P<quarter>[1-4])$")
_MS_EPOCH_THRESHOLD = 1_000_000_000_000
_DEFAULT_SNAPSHOT_MAX_DELTA_MS = 7 * 86_400_000
_LIVE_END_SLACK_MS = 15 * 60 * 1000


@dataclass(frozen=True)
class InvestorNavCapture:
    ts_ms: int
    investor_id: str
    investor_dir: Path
    total_equity_usdc: Decimal
    collateral_spot_usdc: Decimal
    nav_perf: Decimal
    aum_mgmt: Decimal
    index_btc_usd: Decimal
    index_eth_usd: Decimal
    equity_by_book: dict[str, Decimal]
    equity_native_by_book: dict[str, Decimal]
    fee_config: InvestorFeeConfig


def collateral_spot_usdc(
    fee_config: InvestorFeeConfig,
    *,
    index_btc_usd: Decimal,
    index_eth_usd: Decimal,
) -> Decimal:
    return fee_config.collateral_spot_btc * index_btc_usd + fee_config.collateral_spot_eth * index_eth_usd


def resolve_agreed_spot_native(
    fee_config: InvestorFeeConfig,
    flow_baseline: FlowBaselineRow | None,
) -> tuple[Decimal, Decimal, str]:
    """Agreed investor collateral spot inventory (native BTC/ETH) and its source label."""
    if fee_config.collateral_spot_btc > 0 or fee_config.collateral_spot_eth > 0:
        return (
            fee_config.collateral_spot_btc,
            fee_config.collateral_spot_eth,
            "config",
        )
    if flow_baseline is not None:
        native = flow_baseline.net_flow_native_by_book
        return (
            max(Decimal("0"), native.get("BTC", Decimal("0"))),
            max(Decimal("0"), native.get("ETH", Decimal("0"))),
            "bootstrap_deposits",
        )
    return Decimal("0"), Decimal("0"), "none"


def agreed_spot_usdc(
    btc_native: Decimal,
    eth_native: Decimal,
    *,
    index_btc_usd: Decimal,
    index_eth_usd: Decimal,
) -> Decimal:
    return native_book_amount_to_usdc(
        btc_native, "BTC", {"BTC": index_btc_usd, "ETH": index_eth_usd}
    ) + native_book_amount_to_usdc(eth_native, "ETH", {"BTC": index_btc_usd, "ETH": index_eth_usd})


def snapshot_equity_native_by_book(row: NavSnapshotRow) -> dict[str, Decimal]:
    """Derive native book balances from stored USDC-equivalent equity and index prices."""
    out: dict[str, Decimal] = {}
    for book in ("BTC", "ETH", "USDC"):
        usdc = row.equity_by_book.get(book, Decimal("0"))
        if book == "USDC":
            out[book] = usdc
            continue
        idx = row.index_btc_usd if book == "BTC" else row.index_eth_usd
        out[book] = usdc / idx if idx > 0 else Decimal("0")
    return out


def financial_breakdown_from_snapshot(
    row: NavSnapshotRow,
    *,
    fee_config: InvestorFeeConfig,
    flow_baseline: FlowBaselineRow | None,
) -> dict[str, Any]:
    """Recompute NAV_perf using agreed spot (config or bootstrap BTC/ETH deposits)."""
    btc_spot, eth_spot, spot_source = resolve_agreed_spot_native(fee_config, flow_baseline)
    spot_usdc = agreed_spot_usdc(
        btc_spot,
        eth_spot,
        index_btc_usd=row.index_btc_usd,
        index_eth_usd=row.index_eth_usd,
    )
    nav_perf = row.total_equity_usdc - spot_usdc
    aum_mgmt = nav_perf + spot_usdc
    equity_native = snapshot_equity_native_by_book(row)
    index_by_ccy = {
        "BTC": row.index_btc_usd,
        "ETH": row.index_eth_usd,
        "USDC": Decimal("1"),
    }
    equity_usdc = {
        book: (
            row.equity_by_book.get(book, Decimal("0"))
            if book == "USDC"
            else native_book_amount_to_usdc(equity_native[book], book, index_by_ccy)
        )
        for book in ("BTC", "ETH", "USDC")
    }
    return {
        "ts_ms": row.ts_ms,
        "index_btc_usd": row.index_btc_usd,
        "index_eth_usd": row.index_eth_usd,
        "equity_native_by_book": equity_native,
        "equity_usdc_by_book": equity_usdc,
        "total_equity_usdc": row.total_equity_usdc,
        "agreed_spot_btc_native": btc_spot,
        "agreed_spot_eth_native": eth_spot,
        "agreed_spot_source": spot_source,
        "collateral_spot_usdc": spot_usdc,
        "nav_perf": nav_perf,
        "aum_mgmt": aum_mgmt,
    }


def nav_from_equity(
    total_equity_usdc: Decimal,
    fee_config: InvestorFeeConfig,
    *,
    index_btc_usd: Decimal,
    index_eth_usd: Decimal,
    flow_baseline: FlowBaselineRow | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (collateral_spot_usdc, nav_perf, aum_mgmt)."""
    btc_spot, eth_spot, _source = resolve_agreed_spot_native(fee_config, flow_baseline)
    spot = agreed_spot_usdc(
        btc_spot,
        eth_spot,
        index_btc_usd=index_btc_usd,
        index_eth_usd=index_eth_usd,
    )
    nav_perf = total_equity_usdc - spot
    aum_mgmt = nav_perf + spot
    return spot, nav_perf, aum_mgmt


def parse_quarter_period(period: str) -> tuple[datetime, datetime]:
    match = _QUARTER_RE.match(period.strip())
    if not match:
        raise ValueError(f"Invalid quarter period {period!r}; expected YYYY-Q[1-4]")
    year = int(match.group("year"))
    quarter = int(match.group("quarter"))
    start_month = (quarter - 1) * 3 + 1
    start = datetime(year, start_month, 1, 0, 0, 0, tzinfo=UTC)
    if quarter == 4:
        end = datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(microseconds=1)
    else:
        end = datetime(year, start_month + 3, 1, tzinfo=UTC) - timedelta(microseconds=1)
    return start, end


def quarter_end_settlement_ts_ms(period: str) -> int:
    """23:59:59 UTC on the last day of the quarter."""
    _, end = parse_quarter_period(period)
    settlement = end.replace(hour=23, minute=59, second=59, microsecond=0)
    return int(settlement.timestamp() * 1000)


def quarter_start_ts_ms(period: str) -> int:
    start, _ = parse_quarter_period(period)
    return int(start.timestamp() * 1000)


def is_quarter_period(period: str) -> bool:
    return bool(_QUARTER_RE.match(period.strip()))


def parse_fee_timestamp(
    value: str,
    *,
    boundary: Literal["start", "end"] = "start",
) -> int:
    """Parse CLI/API timestamps: unix ms/sec, ISO datetime, YYYY-MM-DD, or ``now``."""
    raw = value.strip()
    if not raw:
        raise ValueError("empty timestamp")
    lowered = raw.lower()
    if lowered in {"now", "utcnow"}:
        return utc_now_ms()

    if raw.isdigit():
        n = int(raw)
        return n if n >= _MS_EPOCH_THRESHOLD else n * 1000

    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
        if boundary == "end":
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=0)
        return int(dt.timestamp() * 1000)

    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp {value!r}; use YYYY-MM-DD, ISO-8601, unix ms, or now") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return int(dt.timestamp() * 1000)


def period_label_from_ms(start_ms: int, end_ms: int) -> str:
    """Filesystem-safe settlement id for a custom [start, end] window."""
    start = datetime.fromtimestamp(start_ms / 1000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{start}_{end}"


def period_duration_years(start_ms: int, end_ms: int) -> Decimal:
    if end_ms <= start_ms:
        raise ValueError(f"period end {end_ms} must be after start {start_ms}")
    return Decimal(end_ms - start_ms) / Decimal("31557600000")  # 365.25 days in ms


def capture_investor_nav(
    investor: str | Path,
    *,
    repo_root: Path | str | None = None,
) -> InvestorNavCapture:
    """Fetch live consolidated equity for all enabled sub-accounts."""
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise RuntimeError("Cannot locate repository root (missing deribit_engine/)")
    manifest = load_investor_manifest(investor, repo_root=root)
    fee_config = load_investor_fee_config(manifest.root)
    store = FeeSnapshotStore(fee_ledger_db_path(root, manifest.investor_id))
    flow_baseline = store.load_flow_baseline(manifest.investor_id)

    from .frontend_server import (  # local import keeps FastAPI optional for pure math tests
        _aggregate_status,
        _dec,
        _make_dashboard_accounts,
    )

    env_files = manifest.account_env_files(require_creds=True)
    if not env_files:
        raise RuntimeError(f"No enabled accounts with API credentials for investor {manifest.investor_id!r}")

    accounts = _make_dashboard_accounts(
        env_file=str(env_files[0]),
        account_env_files=tuple(str(path) for path in env_files),
    )
    status = _aggregate_status(accounts, exchange_prefetch_cache=_fee_exchange_prefetch_cache())
    portfolio = status.get("portfolio") or {}
    index_map = status.get("underlying_index_usd") or {}
    index_btc = _dec(index_map.get("BTC") or index_map.get("btc") or "0")
    index_eth = _dec(index_map.get("ETH") or index_map.get("eth") or "0")
    total_equity = _dec(portfolio.get("total_equity_usdc"))
    equity_by_book = {str(k).upper(): _dec(v) for k, v in (portfolio.get("equity_by_book") or {}).items()}
    equity_native_by_book = {
        str(book).upper(): _dec((row or {}).get("equity") or "0")
        for book, row in (status.get("accounts") or {}).items()
    }
    for book in ("BTC", "ETH", "USDC"):
        equity_native_by_book.setdefault(book, Decimal("0"))
    # Portfolio equity_by_book only includes books each bot tracks; raw API USDC
    # balances can differ when a sub-account omits USDC from traded_collaterals.
    if "USDC" in equity_by_book:
        equity_native_by_book["USDC"] = equity_by_book["USDC"]
    spot, nav_perf, aum_mgmt = nav_from_equity(
        total_equity,
        fee_config,
        index_btc_usd=index_btc,
        index_eth_usd=index_eth,
        flow_baseline=flow_baseline,
    )
    return InvestorNavCapture(
        ts_ms=utc_now_ms(),
        investor_id=manifest.investor_id,
        investor_dir=manifest.root,
        total_equity_usdc=total_equity,
        collateral_spot_usdc=spot,
        nav_perf=nav_perf,
        aum_mgmt=aum_mgmt,
        index_btc_usd=index_btc,
        index_eth_usd=index_eth,
        equity_by_book=equity_by_book,
        equity_native_by_book=equity_native_by_book,
        fee_config=fee_config,
    )


def store_nav_capture(
    capture: InvestorNavCapture,
    *,
    repo_root: Path | str,
    snapshot_kind: str = "manual",
    notes: str | None = None,
    bootstrap_hwm: bool = True,
    force_bootstrap: bool = False,
) -> tuple[int, dict[str, Any] | None]:
    store = FeeSnapshotStore(fee_ledger_db_path(Path(repo_root), capture.investor_id))
    flow_baseline = store.load_flow_baseline(capture.investor_id)
    btc_spot, eth_spot, _spot_source = resolve_agreed_spot_native(capture.fee_config, flow_baseline)
    bootstrap = (
        maybe_bootstrap_hwm_from_deposits(
            capture,
            repo_root=Path(repo_root),
            store=store,
            force=force_bootstrap,
        )
        if bootstrap_hwm
        else None
    )
    row_id = store.append_snapshot(
        ts_ms=capture.ts_ms,
        investor_id=capture.investor_id,
        snapshot_kind=snapshot_kind,
        total_equity_usdc=capture.total_equity_usdc,
        collateral_spot_usdc=capture.collateral_spot_usdc,
        nav_perf=capture.nav_perf,
        aum_mgmt=capture.aum_mgmt,
        index_btc_usd=capture.index_btc_usd,
        index_eth_usd=capture.index_eth_usd,
        collateral_spot_btc=btc_spot,
        collateral_spot_eth=eth_spot,
        equity_by_book=capture.equity_by_book,
        notes=notes,
    )
    return row_id, bootstrap


def maybe_bootstrap_hwm_from_deposits(
    capture: InvestorNavCapture,
    *,
    repo_root: Path,
    store: FeeSnapshotStore | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """On first run, derive initial HWM from Deribit cumulative net deposits."""
    store = store or FeeSnapshotStore(fee_ledger_db_path(repo_root, capture.investor_id))
    if not force:
        if store.load_hwm(capture.investor_id) is not None:
            return None
        if store.load_flow_baseline(capture.investor_id) is not None:
            return None

    if capture.fee_config.initial_hwm_nav_perf is not None:
        initial_hwm = capture.fee_config.initial_hwm_nav_perf
        store.save_hwm(
            investor_id=capture.investor_id,
            hwm_nav_perf=initial_hwm,
            updated_at_ms=capture.ts_ms,
        )
        return {
            "source": "config",
            "initial_hwm_nav_perf": str(initial_hwm),
            "cumulative_net_flow_usdc": None,
        }

    index_by_ccy = {
        "BTC": capture.index_btc_usd,
        "ETH": capture.index_eth_usd,
        "USDC": Decimal("1"),
    }
    flow = fetch_cumulative_net_flow_usdc(
        capture.investor_dir,
        repo_root=repo_root,
        index_by_ccy=index_by_ccy,
    )
    _btc_native, _eth_native, spot_deduction_usdc, initial_hwm = initial_spot_deduction_usdc(
        flow.net_flow_native_by_book,
        index_by_ccy=index_by_ccy,
    )
    flow_detail = flow_report_dict(flow, index_by_ccy=index_by_ccy)
    flow_detail["initial_spot_btc_native"] = str(_btc_native)
    flow_detail["initial_spot_eth_native"] = str(_eth_native)
    flow_detail["initial_spot_deduction_usdc"] = str(spot_deduction_usdc)
    store.save_flow_baseline(
        investor_id=capture.investor_id,
        cumulative_net_flow_usdc=flow.cumulative_net_flow_usdc,
        initial_hwm_nav_perf=initial_hwm,
        net_flow_native_by_book=flow.net_flow_native_by_book,
        start_timestamp_ms=flow.start_timestamp_ms,
        end_timestamp_ms=flow.end_timestamp_ms,
        entry_count=flow.entry_count,
        bootstrapped_at_ms=capture.ts_ms,
        source="transaction_log",
    )
    store.save_hwm(
        investor_id=capture.investor_id,
        hwm_nav_perf=initial_hwm,
        updated_at_ms=capture.ts_ms,
    )
    bootstrap_payload = {
        "source": "transaction_log",
        "cumulative_net_flow_usdc": str(flow.cumulative_net_flow_usdc),
        "initial_hwm_nav_perf": str(initial_hwm),
        **flow_detail,
    }
    try:
        from .investor_fee_report import write_initial_fee_report

        report_out = write_initial_fee_report(
            capture.investor_dir,
            repo_root=repo_root,
            capture=capture,
            write_csv=True,
        )
        bootstrap_payload["report_path"] = str(report_out.primary)
        if report_out.pdf is not None:
            bootstrap_payload["report_pdf_path"] = str(report_out.pdf)
        if report_out.markdown is not None:
            bootstrap_payload["report_markdown_path"] = str(report_out.markdown)
        if report_out.flows_csv is not None:
            bootstrap_payload["report_flows_csv_path"] = str(report_out.flows_csv)
        if report_out.summary_csv is not None:
            bootstrap_payload["report_summary_csv_path"] = str(report_out.summary_csv)
    except Exception as exc:
        LOGGER.warning("initial_fee_report_failed investor=%s err=%s", capture.investor_id, exc)
    return bootstrap_payload


def resolve_hwm(
    store: FeeSnapshotStore,
    investor_id: str,
    fee_config: InvestorFeeConfig,
) -> Decimal:
    current = store.load_hwm(investor_id)
    if current is not None:
        return current
    if fee_config.initial_hwm_nav_perf is not None:
        return fee_config.initial_hwm_nav_perf
    baseline = store.load_flow_baseline(investor_id)
    if baseline is not None:
        return baseline.initial_hwm_nav_perf
    earliest = store.latest_snapshot(investor_id)
    if earliest is not None:
        return earliest.nav_perf
    return Decimal("0")


def average_aum_mgmt(
    store: FeeSnapshotStore,
    investor_id: str,
    *,
    start_ms: int,
    end_ms: int,
    fee_config: InvestorFeeConfig,
    flow_baseline: FlowBaselineRow | None,
) -> Decimal:
    rows = store.snapshots_in_range(investor_id, start_ms=start_ms, end_ms=end_ms)
    if not rows:
        return Decimal("0")
    total = sum(
        (
            financial_breakdown_from_snapshot(row, fee_config=fee_config, flow_baseline=flow_baseline)["aum_mgmt"]
            for row in rows
        ),
        Decimal("0"),
    )
    return total / Decimal(len(rows))


def _resolve_period_end_snapshot(
    investor: str | Path,
    *,
    manifest_investor_id: str,
    end_ms: int,
    store: FeeSnapshotStore,
    repo_root: Path,
    period: str,
    max_delta_ms: int,
    capture_end_if_missing: bool,
) -> NavSnapshotRow:
    end_snap = store.snapshot_nearest(
        manifest_investor_id,
        target_ts_ms=end_ms,
        max_delta_ms=max_delta_ms,
    )
    now_ms = utc_now_ms()
    end_is_live = end_ms >= now_ms - _LIVE_END_SLACK_MS
    if end_snap is None and capture_end_if_missing and end_is_live:
        live = capture_investor_nav(investor, repo_root=repo_root)
        store_nav_capture(
            live,
            repo_root=repo_root,
            snapshot_kind="settlement",
            notes=f"auto {period}",
        )
        end_snap = store.latest_snapshot(manifest_investor_id)
    if end_snap is None:
        raise RuntimeError(
            f"No NAV snapshot within {max_delta_ms // 86_400_000}d of period end "
            f"({_ts_fmt(end_ms)}); run fee-snapshot or use --to now"
        )
    return end_snap


def _ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def settle_period(
    investor: str | Path,
    *,
    end_ms: int,
    start_ms: int | None = None,
    period: str | None = None,
    net_flow_usdc: Decimal | None = None,
    repo_root: Path | str | None = None,
    persist: bool = True,
    force: bool = False,
    max_delta_ms: int = _DEFAULT_SNAPSHOT_MAX_DELTA_MS,
    capture_end_if_missing: bool = True,
    write_report: bool = True,
    allow_missing_start_snapshot: bool = False,
) -> dict[str, Any]:
    """Settle fees for [start_ms, end_ms]. If start_ms is None, use latest stored snapshot."""
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise RuntimeError("Cannot locate repository root (missing deribit_engine/)")
    manifest = load_investor_manifest(investor, repo_root=root)
    fee_config = load_investor_fee_config(manifest.root)
    store = FeeSnapshotStore(fee_ledger_db_path(root, manifest.investor_id))

    start_snap: NavSnapshotRow | None
    if start_ms is None:
        start_snap = store.latest_snapshot_before(manifest.investor_id, before_ts_ms=end_ms)
        if start_snap is None:
            only = store.latest_snapshot(manifest.investor_id)
            if only is not None and only.ts_ms >= end_ms:
                raise RuntimeError("No NAV snapshot before period end; pass --from or use an earlier --to")
            raise RuntimeError("No NAV snapshots on file; run fee-snapshot first or pass --from")
        start_ms = start_snap.ts_ms
    else:
        if start_ms >= end_ms:
            raise ValueError(f"period start ({_ts_fmt(start_ms)}) must be before end ({_ts_fmt(end_ms)})")
        latest = store.latest_snapshot(manifest.investor_id)
        start_snap = (
            latest
            if latest is not None and latest.ts_ms == start_ms
            else store.snapshot_nearest(
                manifest.investor_id,
                target_ts_ms=start_ms,
                max_delta_ms=max_delta_ms,
            )
        )
        if start_snap is None and not allow_missing_start_snapshot:
            raise RuntimeError(
                f"No NAV snapshot within {max_delta_ms // 86_400_000}d of period start "
                f"({_ts_fmt(start_ms)}); run fee-snapshot near that time"
            )

    if start_snap is not None and start_snap.ts_ms > end_ms:
        raise ValueError(f"period start snapshot ({_ts_fmt(start_snap.ts_ms)}) is after end ({_ts_fmt(end_ms)})")

    period_key = period or period_label_from_ms(start_ms, end_ms)
    end_snap = _resolve_period_end_snapshot(
        investor,
        manifest_investor_id=manifest.investor_id,
        end_ms=end_ms,
        store=store,
        repo_root=root,
        period=period_key,
        max_delta_ms=max_delta_ms,
        capture_end_if_missing=capture_end_if_missing,
    )
    if persist:
        existing = store.settlement_for_period(manifest.investor_id, period_key)
        if existing is not None and not force:
            raise RuntimeError(f"Settlement for {period_key!r} already exists; pass force=True to overwrite")

    index_by_ccy = {
        "BTC": end_snap.index_btc_usd,
        "ETH": end_snap.index_eth_usd,
        "USDC": Decimal("1"),
    }
    period_flow_lines: tuple[Any, ...] | None = None
    if net_flow_usdc is None:
        from .investor_cash_flow import fetch_subscription_flow_lines

        lines = fetch_subscription_flow_lines(
            manifest.root,
            repo_root=root,
            index_by_ccy=index_by_ccy,
            start_timestamp_ms=start_ms,
            end_timestamp_ms=end_ms,
        )
        period_flow_lines = tuple(lines)
        net_flow_usdc = sum(
            (row.usdc_equiv for row in lines if row.included_in_subscription),
            Decimal("0"),
        )
        net_flow_source = "transaction_log"
    else:
        net_flow_source = "manual"

    flow_baseline = store.load_flow_baseline(manifest.investor_id)
    start_bd = (
        financial_breakdown_from_snapshot(start_snap, fee_config=fee_config, flow_baseline=flow_baseline)
        if start_snap is not None
        else None
    )
    end_bd = financial_breakdown_from_snapshot(end_snap, fee_config=fee_config, flow_baseline=flow_baseline)

    hwm_start = resolve_hwm(store, manifest.investor_id, fee_config)
    nav_perf_start = start_bd["nav_perf"] if start_bd is not None else hwm_start
    nav_perf_end = end_bd["nav_perf"]
    period_nav_perf_pnl = nav_perf_end - nav_perf_start - net_flow_usdc

    distributable = max(
        Decimal("0"),
        nav_perf_end - hwm_start - net_flow_usdc,
    )
    performance_fee = distributable * fee_config.performance_fee_rate
    hwm_end = nav_perf_end - performance_fee
    avg_aum = average_aum_mgmt(
        store,
        manifest.investor_id,
        start_ms=start_ms,
        end_ms=end_ms,
        fee_config=fee_config,
        flow_baseline=flow_baseline,
    )
    if avg_aum <= 0:
        avg_aum = end_bd["aum_mgmt"]
    years = period_duration_years(start_ms, end_ms)
    management_fee = avg_aum * fee_config.management_fee_annual_rate * years

    settled_at_ms = utc_now_ms()
    payload = {
        "investor_id": manifest.investor_id,
        "period": period_key,
        "period_start_ms": start_ms,
        "period_end_ms": end_ms,
        "hwm_start": hwm_start,
        "nav_perf_start": nav_perf_start,
        "nav_perf_end": nav_perf_end,
        "period_nav_perf_pnl": period_nav_perf_pnl,
        "net_flow_usdc": net_flow_usdc,
        "net_flow_source": net_flow_source,
        "distributable_profit": distributable,
        "performance_fee": performance_fee,
        "hwm_end": hwm_end,
        "avg_aum_mgmt": avg_aum,
        "management_fee": management_fee,
        "settled_at_ms": settled_at_ms,
        "persisted": persist,
    }
    if persist:
        store.save_settlement(
            {
                **payload,
                "hwm_start": str(hwm_start),
                "nav_perf_start": str(nav_perf_start),
                "nav_perf_end": str(nav_perf_end),
                "net_flow_usdc": str(net_flow_usdc),
                "distributable_profit": str(distributable),
                "performance_fee": str(performance_fee),
                "hwm_end": str(hwm_end),
                "avg_aum_mgmt": str(avg_aum),
                "management_fee": str(management_fee),
            }
        )
        store.save_hwm(
            investor_id=manifest.investor_id,
            hwm_nav_perf=hwm_end,
            updated_at_ms=settled_at_ms,
            last_settlement_period=period_key,
        )

    result = {
        **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in payload.items()},
        "start_snapshot": _snapshot_dict(start_snap, fee_config=fee_config, flow_baseline=flow_baseline),
        "end_snapshot": _snapshot_dict(end_snap, fee_config=fee_config, flow_baseline=flow_baseline),
        "agreed_spot_btc_native": str(end_bd["agreed_spot_btc_native"]),
        "agreed_spot_eth_native": str(end_bd["agreed_spot_eth_native"]),
        "agreed_spot_source": end_bd["agreed_spot_source"],
        "fee_config": {
            "performance_fee_rate": str(fee_config.performance_fee_rate),
            "management_fee_annual_rate": str(fee_config.management_fee_annual_rate),
            "collateral_spot_btc": str(end_bd["agreed_spot_btc_native"]),
            "collateral_spot_eth": str(end_bd["agreed_spot_eth_native"]),
        },
    }
    if write_report:
        try:
            from .investor_fee_report import write_settlement_fee_report

            report_out = write_settlement_fee_report(
                investor,
                period_key,
                repo_root=root,
                settlement_payload=result,
                period_flow_lines=period_flow_lines,
                index_by_ccy=index_by_ccy,
                write_csv=True,
            )
            result["report_path"] = str(report_out.primary)
            if report_out.pdf is not None:
                result["report_pdf_path"] = str(report_out.pdf)
            if report_out.markdown is not None:
                result["report_markdown_path"] = str(report_out.markdown)
            if report_out.flows_csv is not None:
                result["report_flows_csv_path"] = str(report_out.flows_csv)
            if report_out.summary_csv is not None:
                result["report_summary_csv_path"] = str(report_out.summary_csv)
        except Exception as exc:
            LOGGER.warning(
                "settlement_fee_report_failed investor=%s period=%s err=%s",
                manifest.investor_id,
                period_key,
                exc,
            )
    if period_flow_lines is not None:
        result["period_flow_lines"] = period_flow_lines
    return result


def settle_quarter(
    investor: str | Path,
    period: str,
    *,
    net_flow_usdc: Decimal | None = None,
    repo_root: Path | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Compute quarterly performance + management fees and persist settlement."""
    return settle_period(
        investor,
        start_ms=quarter_start_ts_ms(period),
        end_ms=quarter_end_settlement_ts_ms(period),
        period=period,
        net_flow_usdc=net_flow_usdc if net_flow_usdc is not None else Decimal("0"),
        repo_root=repo_root,
        persist=True,
        force=force,
        max_delta_ms=_DEFAULT_SNAPSHOT_MAX_DELTA_MS,
        capture_end_if_missing=True,
        write_report=True,
        allow_missing_start_snapshot=True,
    )


def fee_status(
    investor: str | Path,
    *,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise RuntimeError("Cannot locate repository root (missing deribit_engine/)")
    manifest = load_investor_manifest(investor, repo_root=root)
    fee_config = load_investor_fee_config(manifest.root)
    store = FeeSnapshotStore(fee_ledger_db_path(root, manifest.investor_id))
    latest = store.latest_snapshot(manifest.investor_id)
    hwm = resolve_hwm(store, manifest.investor_id, fee_config)
    baseline = store.load_flow_baseline(manifest.investor_id)
    latest_snapshot = _snapshot_dict(
        latest,
        fee_config=fee_config,
        flow_baseline=baseline,
    )
    nav_perf = Decimal(latest_snapshot["nav_perf"]) if latest_snapshot else None
    return {
        "investor_id": manifest.investor_id,
        "display_name": manifest.display_name,
        "db_path": str(fee_ledger_db_path(root, manifest.investor_id)),
        "hwm_nav_perf": str(hwm),
        "distributable_above_hwm": (str(max(Decimal("0"), nav_perf - hwm)) if nav_perf is not None else None),
        "flow_baseline": _flow_baseline_dict(baseline),
        "latest_snapshot": latest_snapshot,
        "fee_config": {
            "collateral_spot_btc": str(fee_config.collateral_spot_btc),
            "collateral_spot_eth": str(fee_config.collateral_spot_eth),
            "performance_fee_rate": str(fee_config.performance_fee_rate),
            "management_fee_annual_rate": str(fee_config.management_fee_annual_rate),
            "initial_hwm_nav_perf": (
                str(fee_config.initial_hwm_nav_perf) if fee_config.initial_hwm_nav_perf is not None else None
            ),
        },
        "settlements": [
            {
                "period": row.period,
                "performance_fee": str(row.performance_fee),
                "management_fee": str(row.management_fee),
                "hwm_end": str(row.hwm_end),
                "settled_at_ms": row.settled_at_ms,
            }
            for row in store.list_settlements(manifest.investor_id)
        ],
    }


def _flow_baseline_dict(row: FlowBaselineRow | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "cumulative_net_flow_usdc": str(row.cumulative_net_flow_usdc),
        "initial_hwm_nav_perf": str(row.initial_hwm_nav_perf),
        "net_flow_native_by_book": {k: str(v) for k, v in row.net_flow_native_by_book.items()},
        "entry_count": row.entry_count,
        "start_timestamp_ms": row.start_timestamp_ms,
        "end_timestamp_ms": row.end_timestamp_ms,
        "bootstrapped_at_ms": row.bootstrapped_at_ms,
        "source": row.source,
    }


def _snapshot_dict(
    row: NavSnapshotRow | None,
    *,
    fee_config: InvestorFeeConfig | None = None,
    flow_baseline: FlowBaselineRow | None = None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {
        "id": row.id,
        "ts_ms": row.ts_ms,
        "snapshot_kind": row.snapshot_kind,
        "total_equity_usdc": str(row.total_equity_usdc),
        "collateral_spot_usdc": str(row.collateral_spot_usdc),
        "nav_perf": str(row.nav_perf),
        "aum_mgmt": str(row.aum_mgmt),
        "index_btc_usd": str(row.index_btc_usd),
        "index_eth_usd": str(row.index_eth_usd),
        "equity_by_book": {k: str(v) for k, v in row.equity_by_book.items()},
        "notes": row.notes,
    }
    if fee_config is not None:
        bd = financial_breakdown_from_snapshot(row, fee_config=fee_config, flow_baseline=flow_baseline)
        out.update(
            {
                "collateral_spot_usdc": str(bd["collateral_spot_usdc"]),
                "nav_perf": str(bd["nav_perf"]),
                "aum_mgmt": str(bd["aum_mgmt"]),
                "equity_native_by_book": {k: str(v) for k, v in bd["equity_native_by_book"].items()},
                "equity_usdc_by_book": {k: str(v) for k, v in bd["equity_usdc_by_book"].items()},
                "agreed_spot_btc_native": str(bd["agreed_spot_btc_native"]),
                "agreed_spot_eth_native": str(bd["agreed_spot_eth_native"]),
                "agreed_spot_source": bd["agreed_spot_source"],
            }
        )
    return out
