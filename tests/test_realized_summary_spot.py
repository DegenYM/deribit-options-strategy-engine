from decimal import Decimal

from deribit_engine.realized_summary import (
    patch_realized_report_spot_pnl,
    realized_pnl_usdc_at_spot,
    realized_summary_from_closed,
)


def test_realized_pnl_usdc_at_spot_uses_native_times_live_index() -> None:
    row = {
        "status": "closed",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "realized_pnl": "100",
        "realized_pnl_collateral_native": "0.001",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    at_close = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("100000")})
    at_live = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("120000")})
    assert at_close == Decimal("100")
    assert at_live == Decimal("120")


def test_realized_pnl_usdc_at_spot_uses_usdt_when_profit_swept() -> None:
    row = {
        "status": "closed",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "realized_pnl": "100",
        "realized_pnl_collateral_native": "0.001",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.001",
        "profit_sweep_quote_proceeds": "146.2",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    at_live = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("120000")})
    assert at_live == Decimal("146.2")


def test_profit_sweep_lifetime_uses_quote_usdt_not_option_pnl() -> None:
    row = {
        "status": "closed",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "realized_pnl": "38",
        "realized_pnl_collateral_native": "0.00048072",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.00048072",
        "profit_sweep_quote_proceeds": "0.08355164",
        "profit_sweep_quote_proceeds_lifetime": "0.08355164",
        "profit_sweep_reason": "proceeds_reconciled",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    at_live = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("120000")})
    assert at_live == Decimal("0.08355164")


def test_profit_sweep_realized_usdt_prefers_fill_quote_over_lifetime() -> None:
    row = {
        "status": "closed",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "realized_pnl": "1.26",
        "realized_pnl_collateral_native": "0.00002",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.00002",
        "profit_sweep_quote_proceeds": "0.90955257",
        "profit_sweep_quote_proceeds_lifetime": "1.23016371",
        "profit_sweep_reason": "take_profit; dust_pool_sweep; proceeds_reconciled",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    at_live = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("120000")})
    assert at_live == Decimal("0.90955257")


def test_realized_sample_days_uses_live_now_not_last_close() -> None:
    entry_ms = 1_699_000_000_000
    closed_ms = 1_700_000_000_000
    now_ms = closed_ms + 10 * 24 * 3600 * 1000
    rows = [
        {
            "status": "closed",
            "realized_pnl": "10",
            "closed_timestamp_ms": closed_ms,
            "entry_timestamp_ms": entry_ms,
        }
    ]
    open_rows = [
        {
            "status": "open",
            "entry_timestamp_ms": entry_ms - 5 * 24 * 3600 * 1000,
        }
    ]
    summary = realized_summary_from_closed(
        rows,
        effective_capital_usdc=Decimal("10000"),
        target_portfolio_apr=Decimal("0"),
        open_rows=open_rows,
        now_ms=now_ms,
    )
    expected_days = Decimal(str(now_ms - (entry_ms - 5 * 24 * 3600 * 1000))) / Decimal("86400000")
    assert abs(Decimal(summary["lifetime_sample_days"]) - expected_days) < Decimal("1e-6")
    assert Decimal(summary["lifetime_sample_days"]) > Decimal(str(closed_ms - entry_ms)) / Decimal("86400000")


def test_realized_summary_from_closed_sums_at_spot() -> None:
    rows = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "50",
            "realized_pnl_collateral_native": "0.001",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        },
        {
            "status": "closed",
            "collateral_currency": "USDC",
            "currency": "USDC",
            "realized_pnl": "10",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        },
    ]
    stored = realized_summary_from_closed(
        rows,
        effective_capital_usdc=Decimal("10000"),
        target_portfolio_apr=Decimal("0"),
        spot_index=None,
    )
    live = realized_summary_from_closed(
        rows,
        effective_capital_usdc=Decimal("10000"),
        target_portfolio_apr=Decimal("0"),
        spot_index={"BTC": Decimal("120000")},
    )
    assert Decimal(stored["realized_pnl_usdc"]) == Decimal("60")
    assert Decimal(live["realized_pnl_usdc"]) == Decimal("130")


def test_patch_realized_report_spot_pnl_updates_cached_summary() -> None:
    report = {
        "summary": {
            "effective_capital_usdc": "10000",
            "target_portfolio_apr": "0",
            "window_days_requested": "30",
            "realized_pnl_usdc": "50",
        }
    }
    rows = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "50",
            "realized_pnl_collateral_native": "0.001",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        }
    ]
    patch_realized_report_spot_pnl(
        report,
        rows,
        spot_index={"BTC": Decimal("120000")},
        window_days=30,
    )
    assert Decimal(report["summary"]["realized_pnl_usdc"]) == Decimal("120")
