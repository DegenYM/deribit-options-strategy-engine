from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from deribit_engine.fee_snapshot_store import FeeSnapshotStore, FlowBaselineRow, fee_ledger_db_path
from deribit_engine.investor_cash_flow import SubscriptionFlowLine
from deribit_engine.investor_fee_report import (
    InitialFeeReportContext,
    SettlementFeeReportContext,
    _equity_native_for_book,
    initial_report_path,
    render_initial_fee_report_md,
    render_settlement_fee_report_md,
    settlement_report_path,
    write_initial_fee_report,
)
from deribit_engine.investor_fee_report_csv import write_initial_fee_report_csv
from deribit_engine.investor_fee_report_pdf import write_initial_fee_report_pdf


def test_report_paths_use_initial_and_date_folders(tmp_path: Path) -> None:
    ts_ms = int(datetime(2026, 5, 21, 15, 59, tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime(2026, 5, 21, 16, 14, 20, tzinfo=UTC).timestamp() * 1000)
    initial = initial_report_path(tmp_path, "youming", ts_ms=ts_ms)
    settlement = settlement_report_path(
        tmp_path,
        "youming",
        "20260521T000000Z_20260521T161420Z",
        period_end_ms=end_ms,
    )
    assert initial == tmp_path / "data/fee_ledger/youming/reports/initial/initial-20260521.md"
    assert settlement == (
        tmp_path / "data/fee_ledger/youming/reports/2026-05-21/settlement-20260521T000000Z_20260521T161420Z.md"
    )


def _baseline() -> FlowBaselineRow:
    return FlowBaselineRow(
        investor_id="demo",
        cumulative_net_flow_usdc=Decimal("21800.31"),
        initial_hwm_nav_perf=Decimal("6299.2"),
        net_flow_native_by_book={
            "BTC": Decimal("0.09754091"),
            "ETH": Decimal("3.019628"),
            "USDC": Decimal("6299.2"),
        },
        start_timestamp_ms=0,
        end_timestamp_ms=int(datetime(2026, 5, 20, tzinfo=UTC).timestamp() * 1000),
        entry_count=9,
        bootstrapped_at_ms=int(datetime(2026, 5, 20, tzinfo=UTC).timestamp() * 1000),
        source="transaction_log",
    )


def _flow_line(**kwargs) -> SubscriptionFlowLine:
    defaults = {
        "identity_label": "covered_call",
        "client_id": "abc",
        "book": "ETH",
        "timestamp_ms": int(datetime(2026, 4, 16, 10, 30, tzinfo=UTC).timestamp() * 1000),
        "flow_type": "deposit",
        "amount_native": Decimal("0.25"),
        "usdc_equiv": Decimal("500"),
        "included_in_subscription": True,
    }
    defaults.update(kwargs)
    return SubscriptionFlowLine(**defaults)


def _initial_ctx() -> InitialFeeReportContext:
    return InitialFeeReportContext(
        investor_id="demo",
        display_name="Demo",
        generated_at_ms=int(datetime(2026, 5, 20, tzinfo=UTC).timestamp() * 1000),
        baseline=_baseline(),
        flow_lines=(
            _flow_line(),
            _flow_line(
                flow_type="withdrawal",
                amount_native=Decimal("-0.25"),
                usdc_equiv=Decimal("-500"),
            ),
            _flow_line(
                flow_type="transfer_out",
                amount_native=Decimal("-1"),
                usdc_equiv=Decimal("-2000"),
                included_in_subscription=True,
            ),
        ),
        fee_config={
            "collateral_spot_btc": "0",
            "collateral_spot_eth": "0",
            "performance_fee_rate": "0.10",
            "management_fee_annual_rate": "0.01",
        },
        collateral_spot_usdc=Decimal("0"),
        index_by_ccy={"BTC": Decimal("60000"), "ETH": Decimal("2000"), "USDC": Decimal("1")},
        live_nav={
            "total_equity_usdc": "25000",
            "equity_by_book": {"BTC": "10000", "ETH": "8000", "USDC": "7000"},
            "equity_native_by_book": {
                "BTC": "0.16666667",
                "ETH": "4",
                "USDC": "7000",
            },
            "ts_ms": "1",
        },
    )


def test_equity_native_for_book_usdc_matches_portfolio() -> None:
    native = _equity_native_for_book(
        "USDC",
        equity_native_by_book={"USDC": Decimal("5994.48")},
        equity_by_book={"USDC": Decimal("5974.51")},
        index_by_ccy={"USDC": Decimal("1")},
    )
    assert native == Decimal("5974.51")


def test_render_initial_fee_report_md_english() -> None:
    md = render_initial_fee_report_md(_initial_ctx())
    assert "Investor Initial Fee Baseline" in md
    assert "6299.2" in md or "USDC net subscription" in md
    assert "Net subscription & initial spot" in md
    assert "USDC equivalent" in md
    assert "Initial spot to deduct (BTC)" in md
    assert "0.09754091" in md
    assert "Initial spot to deduct (ETH)" in md
    assert "| **Total** |" in md
    assert "| BTC |" in md
    assert "Native equity" in md
    assert "| **Net subscription total** |" in md
    assert "Counted toward subscription" in md
    assert "NAV snapshot at bootstrap" not in md
    assert "## 4." in md
    assert "投資人" not in md


def test_render_settlement_fee_report_md_english(tmp_path: Path) -> None:
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    ctx = SettlementFeeReportContext(
        investor_id="demo",
        display_name="Demo",
        period="2026-Q1",
        generated_at_ms=int(datetime(2026, 4, 1, tzinfo=UTC).timestamp() * 1000),
        settlement={
            "period": "2026-Q1",
            "period_start_ms": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000),
            "period_end_ms": int(datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC).timestamp() * 1000),
            "hwm_start": "10000",
            "nav_perf_start": "10000",
            "nav_perf_end": "12000",
            "period_nav_perf_pnl": "1500",
            "net_flow_usdc": "500",
            "distributable_profit": "1500",
            "performance_fee": "150",
            "hwm_end": "11850",
            "avg_aum_mgmt": "11000",
            "management_fee": "27.5",
            "settled_at_ms": int(datetime(2026, 4, 2, tzinfo=UTC).timestamp() * 1000),
        },
        start_snapshot={
            "ts_ms": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000),
            "nav_perf": "10000",
            "total_equity_usdc": "12000",
            "aum_mgmt": "12000",
            "index_btc_usd": "60000",
            "index_eth_usd": "2000",
            "equity_native_by_book": {"BTC": "0.1", "ETH": "1", "USDC": "5000"},
            "equity_usdc_by_book": {"BTC": "6000", "ETH": "2000", "USDC": "5000"},
        },
        end_snapshot={
            "ts_ms": int(datetime(2026, 3, 31, tzinfo=UTC).timestamp() * 1000),
            "nav_perf": "12000",
            "total_equity_usdc": "14000",
            "aum_mgmt": "14000",
            "index_btc_usd": "60000",
            "index_eth_usd": "2000",
            "equity_native_by_book": {"BTC": "0.1", "ETH": "1.2", "USDC": "6000"},
            "equity_usdc_by_book": {"BTC": "6000", "ETH": "2400", "USDC": "6000"},
        },
        quarter_flow_lines=(_flow_line(book="USDC", amount_native=Decimal("500"), usdc_equiv=Decimal("500")),),
        fee_config={
            "collateral_spot_btc": "0",
            "collateral_spot_eth": "0",
            "performance_fee_rate": "0.10",
            "management_fee_annual_rate": "0.01",
        },
        index_by_ccy={"BTC": Decimal("60000"), "ETH": Decimal("2000"), "USDC": Decimal("1")},
    )
    md = render_settlement_fee_report_md(ctx, repo_root=tmp_path)
    assert "Period Statement" in md
    assert "**Day A**" in md
    assert "**Day B**" in md
    assert "Period deposits" in md
    assert "USDC-equivalent balances" in md
    assert "Change (B − A)" in md
    assert "NAV & performance fee (USDC)" in md
    assert "NAV_perf at Day A" in md
    assert "Distributable profit (above HWM)" in md
    assert "Strategy P&L (period)" in md
    assert "Performance fee" in md
    assert "Period cash movements" in md
    assert "Closed option trades" in md


def test_write_initial_fee_report_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "initial.pdf"
    write_initial_fee_report_pdf(_initial_ctx(), pdf_path)
    assert pdf_path.exists()
    assert pdf_path.read_bytes()[:4] == b"%PDF"


def test_write_initial_fee_report_csv(tmp_path: Path) -> None:
    ctx = _initial_ctx()
    flows, summary = write_initial_fee_report_csv(
        ctx,
        flows_path=tmp_path / "initial-flows.csv",
        summary_path=tmp_path / "initial-summary.csv",
    )
    flows_text = flows.read_text(encoding="utf-8")
    summary_text = summary.read_text(encoding="utf-8")
    assert "timestamp_utc,account,type,currency" in flows_text
    assert "withdrawal" in flows_text
    assert "field,value" in summary_text.replace(" ", "")
    assert "initial_hwm_nav_perf" in summary_text


def test_write_initial_fee_report_outputs(tmp_path: Path, monkeypatch) -> None:
    investor_dir = tmp_path / "config" / "investors" / "demo"
    investor_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "demo"
display_name = "Demo"

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    store = FeeSnapshotStore(fee_ledger_db_path(tmp_path, "demo"))
    ts = int(datetime(2026, 5, 20, tzinfo=UTC).timestamp() * 1000)
    store.save_flow_baseline(
        investor_id="demo",
        cumulative_net_flow_usdc=Decimal("1000"),
        initial_hwm_nav_perf=Decimal("800"),
        net_flow_native_by_book={"USDC": Decimal("1000")},
        start_timestamp_ms=0,
        end_timestamp_ms=ts,
        entry_count=1,
        bootstrapped_at_ms=ts,
        source="transaction_log",
    )

    def _fake_capture(*args, **kwargs):
        from deribit_engine.investor_fee_config import InvestorFeeConfig
        from deribit_engine.investor_nav_snapshot import InvestorNavCapture

        return InvestorNavCapture(
            ts_ms=ts,
            investor_id="demo",
            investor_dir=investor_dir,
            total_equity_usdc=Decimal("1000"),
            collateral_spot_usdc=Decimal("0"),
            nav_perf=Decimal("1000"),
            aum_mgmt=Decimal("1000"),
            index_btc_usd=Decimal("60000"),
            index_eth_usd=Decimal("2000"),
            equity_by_book={"USDC": Decimal("1000")},
            equity_native_by_book={"BTC": Decimal("0"), "ETH": Decimal("0"), "USDC": Decimal("1000")},
            fee_config=InvestorFeeConfig(
                collateral_spot_btc=Decimal("0"),
                collateral_spot_eth=Decimal("0"),
                performance_fee_rate=Decimal("0.10"),
                management_fee_annual_rate=Decimal("0.01"),
                initial_hwm_nav_perf=None,
            ),
        )

    def _fake_lines(*args, **kwargs):
        return [_flow_line(book="USDC", amount_native=Decimal("1000"), usdc_equiv=Decimal("1000"))]

    monkeypatch.setattr("deribit_engine.investor_fee_report.capture_investor_nav", _fake_capture)
    monkeypatch.setattr("deribit_engine.investor_fee_report.fetch_subscription_flow_lines", _fake_lines)

    out = write_initial_fee_report(
        investor_dir,
        repo_root=tmp_path,
        write_csv=True,
    )
    assert out.markdown is not None and out.markdown.exists()
    assert out.pdf is not None and out.pdf.exists()
    assert out.flows_csv is not None and out.flows_csv.exists()
    assert out.summary_csv is not None and out.summary_csv.exists()
    assert "Investor Initial Fee Baseline" in out.markdown.read_text(encoding="utf-8")
    assert out.pdf.read_bytes()[:4] == b"%PDF"

    out_csv_only = write_initial_fee_report(
        investor_dir,
        repo_root=tmp_path,
        write_pdf=False,
        write_markdown=False,
        write_csv=True,
        output_path=tmp_path / "reports" / "custom.csv",
    )
    assert out_csv_only.pdf is None
    assert out_csv_only.markdown is None
    assert out_csv_only.flows_csv is not None
