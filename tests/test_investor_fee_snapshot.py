from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from deribit_demo.fee_snapshot_store import FeeSnapshotStore, FlowBaselineRow
from deribit_demo.investor_cash_flow import (
    CumulativeNetFlow,
    initial_hwm_from_net_flow,
    initial_spot_deduction_usdc,
)
from deribit_demo.investor_fee_config import InvestorFeeConfig
from deribit_demo.investor_nav_snapshot import (
    InvestorNavCapture,
    average_aum_mgmt,
    capture_investor_nav,
    collateral_spot_usdc,
    maybe_bootstrap_hwm_from_deposits,
    nav_from_equity,
    parse_fee_timestamp,
    parse_quarter_period,
    period_label_from_ms,
    quarter_end_settlement_ts_ms,
    resolve_hwm,
    settle_period,
    settle_quarter,
    store_nav_capture,
)


def test_initial_hwm_from_net_flow() -> None:
    assert initial_hwm_from_net_flow(Decimal("100000"), Decimal("20000")) == Decimal("80000")
    assert initial_hwm_from_net_flow(Decimal("10000"), Decimal("20000")) == Decimal("0")


def test_initial_spot_deduction_usdc() -> None:
    native = {"BTC": Decimal("0.1"), "ETH": Decimal("2"), "USDC": Decimal("5000")}
    index = {"BTC": Decimal("70000"), "ETH": Decimal("3000"), "USDC": Decimal("1")}
    btc_n, eth_n, spot, hwm = initial_spot_deduction_usdc(native, index_by_ccy=index)
    assert btc_n == Decimal("0.1")
    assert eth_n == Decimal("2")
    assert spot == Decimal("7000") + Decimal("6000")
    assert hwm == Decimal("18000") - spot


def test_resolve_hwm_uses_flow_baseline(tmp_path: Path) -> None:
    store = FeeSnapshotStore(tmp_path / "snapshots.db")
    store.save_flow_baseline(
        investor_id="An",
        cumulative_net_flow_usdc=Decimal("100000"),
        initial_hwm_nav_perf=Decimal("80000"),
        net_flow_native_by_book={"USDC": Decimal("100000")},
        start_timestamp_ms=0,
        end_timestamp_ms=1,
        entry_count=3,
        bootstrapped_at_ms=1,
        source="transaction_log",
    )
    cfg = InvestorFeeConfig(
        collateral_spot_btc=Decimal("0"),
        collateral_spot_eth=Decimal("0"),
        performance_fee_rate=Decimal("0.10"),
        management_fee_annual_rate=Decimal("0.01"),
        initial_hwm_nav_perf=None,
    )
    assert resolve_hwm(store, "An", cfg) == Decimal("80000")


def test_capture_investor_nav_aligns_usdc_native_with_portfolio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    investor_dir = tmp_path / "config" / "investors" / "demo"
    investor_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        '[[accounts]]\nname = "naked"\nenv_file = "accounts/.env.naked"\nenabled = true\n',
        encoding="utf-8",
    )
    (investor_dir / ".env.investor").write_text(
        "INVESTOR_ID=demo\nPERFORMANCE_FEE_RATE=0.10\nMANAGEMENT_FEE_ANNUAL_RATE=0\n",
        encoding="utf-8",
    )

    def _fake_aggregate(accounts, *, exchange_prefetch_cache):
        return {
            "portfolio": {
                "total_equity_usdc": "5974.51",
                "equity_by_book": {"USDC": "5974.51"},
            },
            "underlying_index_usd": {"BTC": "70000", "ETH": "3000"},
            "accounts": {"USDC": {"equity": "5994.48"}},
        }

    monkeypatch.setattr(
        "deribit_demo.frontend_server._aggregate_status",
        _fake_aggregate,
    )
    monkeypatch.setattr(
        "deribit_demo.frontend_server._make_dashboard_accounts",
        lambda **kwargs: [],
    )

    class _Manifest:
        investor_id = "demo"
        root = investor_dir

        def account_env_files(self, *, require_creds: bool = True):
            return [investor_dir / "accounts/.env.naked"]

    monkeypatch.setattr(
        "deribit_demo.investor_nav_snapshot.load_investor_manifest",
        lambda investor, repo_root=None: _Manifest(),
    )

    capture = capture_investor_nav("demo", repo_root=tmp_path)
    assert capture.equity_native_by_book["USDC"] == Decimal("5974.51")
    assert capture.equity_by_book["USDC"] == Decimal("5974.51")


def test_store_nav_capture_writes_capture_nav_on_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    ledger = tmp_path / "data" / "fee_ledger" / "demo" / "snapshots.db"
    capture = InvestorNavCapture(
        ts_ms=1000,
        investor_id="demo",
        investor_dir=tmp_path / "config" / "investors" / "demo",
        total_equity_usdc=Decimal("25000"),
        collateral_spot_usdc=Decimal("0"),
        nav_perf=Decimal("25000"),
        aum_mgmt=Decimal("25000"),
        index_btc_usd=Decimal("60000"),
        index_eth_usd=Decimal("2000"),
        equity_by_book={"USDC": Decimal("25000")},
        equity_native_by_book={"BTC": Decimal("0"), "ETH": Decimal("0"), "USDC": Decimal("25000")},
        fee_config=InvestorFeeConfig(
            collateral_spot_btc=Decimal("0"),
            collateral_spot_eth=Decimal("0"),
            performance_fee_rate=Decimal("0.10"),
            management_fee_annual_rate=Decimal("0.01"),
            initial_hwm_nav_perf=None,
        ),
    )

    def _fake_fetch(*_args, **_kwargs) -> CumulativeNetFlow:
        return CumulativeNetFlow(
            cumulative_net_flow_usdc=Decimal("21800.31"),
            net_flow_native_by_book={
                "BTC": Decimal("0.09754091"),
                "ETH": Decimal("3.019628"),
                "USDC": Decimal("6299.2"),
            },
            start_timestamp_ms=0,
            end_timestamp_ms=1000,
            entry_count=2,
        )

    monkeypatch.setattr(
        "deribit_demo.investor_nav_snapshot.fetch_cumulative_net_flow_usdc",
        _fake_fetch,
    )
    monkeypatch.setattr(
        "deribit_demo.investor_fee_report.write_initial_fee_report",
        lambda *_a, **_k: None,
    )
    row_id, bootstrap = store_nav_capture(capture, repo_root=tmp_path, snapshot_kind="manual")
    assert bootstrap is not None
    store = FeeSnapshotStore(ledger)
    snap = store.latest_snapshot("demo")
    assert snap is not None
    assert snap.nav_perf == capture.nav_perf
    assert snap.collateral_spot_usdc == capture.collateral_spot_usdc
    assert row_id == snap.id


def test_bootstrap_hwm_from_transaction_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FeeSnapshotStore(tmp_path / "snapshots.db")
    capture = InvestorNavCapture(
        ts_ms=1000,
        investor_id="demo",
        investor_dir=tmp_path / "config" / "investors" / "demo",
        total_equity_usdc=Decimal("106000"),
        collateral_spot_usdc=Decimal("0"),
        nav_perf=Decimal("106000"),
        aum_mgmt=Decimal("106000"),
        index_btc_usd=Decimal("50000"),
        index_eth_usd=Decimal("3000"),
        equity_by_book={"USDC": Decimal("106000")},
        equity_native_by_book={"BTC": Decimal("0"), "ETH": Decimal("0"), "USDC": Decimal("106000")},
        fee_config=InvestorFeeConfig(
            collateral_spot_btc=Decimal("0"),
            collateral_spot_eth=Decimal("0"),
            performance_fee_rate=Decimal("0.10"),
            management_fee_annual_rate=Decimal("0.01"),
            initial_hwm_nav_perf=None,
        ),
    )

    def _fake_fetch(*_args, **_kwargs) -> CumulativeNetFlow:
        return CumulativeNetFlow(
            cumulative_net_flow_usdc=Decimal("100000"),
            net_flow_native_by_book={"USDC": Decimal("100000")},
            start_timestamp_ms=0,
            end_timestamp_ms=1000,
            entry_count=2,
        )

    monkeypatch.setattr(
        "deribit_demo.investor_nav_snapshot.fetch_cumulative_net_flow_usdc",
        _fake_fetch,
    )
    result = maybe_bootstrap_hwm_from_deposits(capture, repo_root=tmp_path, store=store)
    assert result is not None
    assert result["source"] == "transaction_log"
    assert result["initial_hwm_nav_perf"] == "100000"
    assert store.load_hwm("demo") == Decimal("100000")
    assert store.load_flow_baseline("demo") is not None


def test_nav_from_equity_excludes_collateral_spot() -> None:
    cfg = InvestorFeeConfig(
        collateral_spot_btc=Decimal("1"),
        collateral_spot_eth=Decimal("10"),
        performance_fee_rate=Decimal("0.10"),
        management_fee_annual_rate=Decimal("0.01"),
        initial_hwm_nav_perf=None,
    )
    spot, nav, aum = nav_from_equity(
        Decimal("200000"),
        cfg,
        index_btc_usd=Decimal("50000"),
        index_eth_usd=Decimal("3000"),
    )
    assert spot == Decimal("80000")
    assert nav == Decimal("120000")
    assert aum == Decimal("200000")


def test_collateral_spot_usdc() -> None:
    cfg = InvestorFeeConfig(
        collateral_spot_btc=Decimal("0.5"),
        collateral_spot_eth=Decimal("0"),
        performance_fee_rate=Decimal("0.10"),
        management_fee_annual_rate=Decimal("0.01"),
        initial_hwm_nav_perf=None,
    )
    assert collateral_spot_usdc(cfg, index_btc_usd=Decimal("40000"), index_eth_usd=Decimal("0")) == Decimal("20000")


def test_parse_quarter_period_q1() -> None:
    start, end = parse_quarter_period("2026-Q1")
    assert start == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 31, 23, 59, 59, 999999, tzinfo=UTC)
    assert quarter_end_settlement_ts_ms("2026-Q1") == int(
        datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC).timestamp() * 1000
    )


def test_fee_snapshot_store_roundtrip(tmp_path: Path) -> None:
    store = FeeSnapshotStore(tmp_path / "snapshots.db")
    row_id = store.append_snapshot(
        ts_ms=1,
        investor_id="An",
        snapshot_kind="manual",
        total_equity_usdc=Decimal("100000"),
        collateral_spot_usdc=Decimal("20000"),
        nav_perf=Decimal("80000"),
        aum_mgmt=Decimal("100000"),
        index_btc_usd=Decimal("50000"),
        index_eth_usd=Decimal("3000"),
        collateral_spot_btc=Decimal("0.4"),
        collateral_spot_eth=Decimal("0"),
        equity_by_book={"USDC": Decimal("100000")},
    )
    assert row_id == 1
    latest = store.latest_snapshot("An")
    assert latest is not None
    assert latest.nav_perf == Decimal("80000")
    assert latest.equity_by_book["USDC"] == Decimal("100000")


def test_resolve_hwm_prefers_store_then_config(tmp_path: Path) -> None:
    store = FeeSnapshotStore(tmp_path / "snapshots.db")
    cfg = InvestorFeeConfig(
        collateral_spot_btc=Decimal("0"),
        collateral_spot_eth=Decimal("0"),
        performance_fee_rate=Decimal("0.10"),
        management_fee_annual_rate=Decimal("0.01"),
        initial_hwm_nav_perf=Decimal("50000"),
    )
    assert resolve_hwm(store, "An", cfg) == Decimal("50000")
    store.save_hwm(investor_id="An", hwm_nav_perf=Decimal("90000"), updated_at_ms=1)
    assert resolve_hwm(store, "An", cfg) == Decimal("90000")


def test_average_aum_mgmt(tmp_path: Path) -> None:
    store = FeeSnapshotStore(tmp_path / "snapshots.db")
    cfg = InvestorFeeConfig(
        collateral_spot_btc=Decimal("0"),
        collateral_spot_eth=Decimal("0"),
        performance_fee_rate=Decimal("0.10"),
        management_fee_annual_rate=Decimal("0.01"),
        initial_hwm_nav_perf=None,
    )
    for ts, aum in ((100, "100"), (200, "200")):
        store.append_snapshot(
            ts_ms=ts,
            investor_id="An",
            snapshot_kind="scheduled",
            total_equity_usdc=Decimal(aum),
            collateral_spot_usdc=Decimal("0"),
            nav_perf=Decimal(aum),
            aum_mgmt=Decimal(aum),
            index_btc_usd=Decimal("0"),
            index_eth_usd=Decimal("0"),
            collateral_spot_btc=Decimal("0"),
            collateral_spot_eth=Decimal("0"),
            equity_by_book={},
        )
    avg = average_aum_mgmt(store, "An", start_ms=0, end_ms=300, fee_config=cfg, flow_baseline=None)
    assert avg == Decimal("150")


def test_nav_from_equity_uses_bootstrap_spot_when_config_zero() -> None:
    cfg = InvestorFeeConfig(
        collateral_spot_btc=Decimal("0"),
        collateral_spot_eth=Decimal("0"),
        performance_fee_rate=Decimal("0.10"),
        management_fee_annual_rate=Decimal("0"),
        initial_hwm_nav_perf=None,
    )
    baseline = FlowBaselineRow(
        investor_id="youming",
        cumulative_net_flow_usdc=Decimal("20000"),
        initial_hwm_nav_perf=Decimal("6000"),
        net_flow_native_by_book={
            "BTC": Decimal("0.1"),
            "ETH": Decimal("3"),
            "USDC": Decimal("6000"),
        },
        start_timestamp_ms=0,
        end_timestamp_ms=1,
        entry_count=1,
        bootstrapped_at_ms=1,
        source="transaction_log",
    )
    spot, nav, aum = nav_from_equity(
        Decimal("25000"),
        cfg,
        index_btc_usd=Decimal("70000"),
        index_eth_usd=Decimal("3000"),
        flow_baseline=baseline,
    )
    assert spot == Decimal("7000") + Decimal("9000")
    assert nav == Decimal("9000")
    assert aum == Decimal("25000")


def test_settle_quarter_computes_performance_fee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "demo"
display_name = "demo"

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (investor_dir / ".env.investor").write_text(
        "INITIAL_HWM_NAV_PERF=100000\nPERFORMANCE_FEE_RATE=0.10\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    store = FeeSnapshotStore(tmp_path / "data" / "fee_ledger" / "demo" / "snapshots.db")
    end_ms = quarter_end_settlement_ts_ms("2026-Q1")
    store.append_snapshot(
        ts_ms=end_ms,
        investor_id="demo",
        snapshot_kind="settlement",
        total_equity_usdc=Decimal("116000"),
        collateral_spot_usdc=Decimal("0"),
        nav_perf=Decimal("116000"),
        aum_mgmt=Decimal("116000"),
        index_btc_usd=Decimal("0"),
        index_eth_usd=Decimal("0"),
        collateral_spot_btc=Decimal("0"),
        collateral_spot_eth=Decimal("0"),
        equity_by_book={"USDC": Decimal("116000")},
    )

    def _fake_capture(*_args, **_kwargs):
        raise AssertionError("live capture should not run when end snapshot exists")

    monkeypatch.setattr("deribit_demo.investor_nav_snapshot.capture_investor_nav", _fake_capture)

    result = settle_quarter("demo", "2026-Q1", repo_root=tmp_path)
    assert Decimal(result["distributable_profit"]) == Decimal("16000")
    assert Decimal(result["performance_fee"]) == Decimal("1600")
    assert Decimal(result["hwm_end"]) == Decimal("114400")
    assert store.load_hwm("demo") == Decimal("114400")


def test_parse_fee_timestamp_date_boundaries() -> None:
    end_ms = parse_fee_timestamp("2026-05-21", boundary="end")
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    assert end_dt == datetime(2026, 5, 21, 23, 59, 59, tzinfo=UTC)
    start_ms = parse_fee_timestamp("2026-05-21", boundary="start")
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    assert start_dt == datetime(2026, 5, 21, 0, 0, 0, tzinfo=UTC)


def test_period_label_from_ms() -> None:
    label = period_label_from_ms(1_000, 2_000)
    assert label.endswith("Z")
    assert "_" in label


def test_settle_period_since_last_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "demo"
display_name = "demo"

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (investor_dir / ".env.investor").write_text(
        "INITIAL_HWM_NAV_PERF=100000\nPERFORMANCE_FEE_RATE=0.10\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    store = FeeSnapshotStore(tmp_path / "data" / "fee_ledger" / "demo" / "snapshots.db")
    store.save_hwm(investor_id="demo", hwm_nav_perf=Decimal("100000"), updated_at_ms=1)
    for ts, nav in ((1_000, "100000"), (2_000, "110000"), (3_000, "115000")):
        store.append_snapshot(
            ts_ms=ts,
            investor_id="demo",
            snapshot_kind="scheduled",
            total_equity_usdc=Decimal(nav),
            collateral_spot_usdc=Decimal("0"),
            nav_perf=Decimal(nav),
            aum_mgmt=Decimal(nav),
            index_btc_usd=Decimal("50000"),
            index_eth_usd=Decimal("3000"),
            collateral_spot_btc=Decimal("0"),
            collateral_spot_eth=Decimal("0"),
            equity_by_book={"USDC": Decimal(nav)},
        )

    def _fake_flow(*_args, **_kwargs) -> CumulativeNetFlow:
        return CumulativeNetFlow(
            cumulative_net_flow_usdc=Decimal("2000"),
            net_flow_native_by_book={"USDC": Decimal("2000")},
            start_timestamp_ms=2_000,
            end_timestamp_ms=3_000,
            entry_count=1,
        )

    def _fake_flow_lines(*_args, **_kwargs):
        from deribit_demo.investor_cash_flow import SubscriptionFlowLine

        return [
            SubscriptionFlowLine(
                identity_label="naked",
                client_id="id",
                book="USDC",
                timestamp_ms=2_500,
                flow_type="deposit",
                amount_native=Decimal("2000"),
                usdc_equiv=Decimal("2000"),
                included_in_subscription=True,
            )
        ]

    monkeypatch.setattr(
        "deribit_demo.investor_cash_flow.fetch_subscription_flow_lines",
        _fake_flow_lines,
    )

    def _no_capture(*_args, **_kwargs):
        raise AssertionError("no live capture")

    monkeypatch.setattr("deribit_demo.investor_nav_snapshot.capture_investor_nav", _no_capture)

    result = settle_period(
        "demo",
        end_ms=3_000,
        repo_root=tmp_path,
        persist=False,
        write_report=False,
    )
    assert result["nav_perf_start"] == "110000"
    assert result["nav_perf_end"] == "115000"
    assert result["net_flow_usdc"] == "2000"
    assert Decimal(result["distributable_profit"]) == Decimal("13000")
    assert Decimal(result["performance_fee"]) == Decimal("1300")
    assert store.load_hwm("demo") == Decimal("100000")
