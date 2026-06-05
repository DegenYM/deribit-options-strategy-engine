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
