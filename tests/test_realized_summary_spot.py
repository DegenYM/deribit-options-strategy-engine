from decimal import Decimal

from deribit_engine.models import TradeGroup
from deribit_engine.realized_summary import (
    patch_realized_report_spot_pnl,
    realized_pnl_usdc_at_spot,
    realized_summary_from_closed,
    total_realized_usdc_from_swap_disposition,
)
from deribit_engine.spot_restore_ops import itm_spot_exit_net_usdt_for_total_profit


def test_total_realized_usdc_from_swap_disposition() -> None:
    """Total profit = swapped USDT + unswept native × live spot."""
    rows = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "100",
            "realized_pnl_collateral_native": "0.002",
            "profit_sweep_status": "filled",
            "profit_sweep_amount": "0.002",
            "profit_sweep_reason": "proceeds_reconciled",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        },
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "44.6",
            "realized_pnl_collateral_native": "0.00066",
            "closed_timestamp_ms": 1_700_100_000_000,
            "entry_timestamp_ms": 1_699_100_000_000,
        },
    ]
    fill_stats = {
        "BTC": {
            "display_native_sold": "0.0024",
            "display_usdt": "157.869",
            "net_native_sold": "0.0024",
            "net_usdt": "157.869",
        }
    }
    spot = {"BTC": Decimal("62608")}
    total = total_realized_usdc_from_swap_disposition(rows, spot_index=spot, fill_stats=fill_stats)
    assert total is not None
    expected = Decimal("157.869") + Decimal("0.00026") * Decimal("62608")
    assert abs(total - expected) < Decimal("0.01")


def test_realized_summary_from_closed_uses_swap_disposition_total() -> None:
    rows = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "realized_pnl": "144.6",
            "realized_pnl_collateral_native": "0.00266",
            "profit_sweep_status": "filled",
            "profit_sweep_amount": "0.0024",
            "profit_sweep_reason": "proceeds_reconciled",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        }
    ]
    fill_stats = {
        "BTC": {
            "display_native_sold": "0.0024",
            "display_usdt": "157.869",
            "net_native_sold": "0.0024",
            "net_usdt": "157.869",
        }
    }
    summary = realized_summary_from_closed(
        rows,
        effective_capital_usdc=Decimal("10000"),
        target_portfolio_apr=Decimal("0"),
        spot_index={"BTC": Decimal("62608")},
        fill_stats=fill_stats,
    )
    expected = Decimal("157.869") + Decimal("0.00026") * Decimal("62608")
    assert abs(Decimal(summary["realized_pnl_usdc"]) - expected) < Decimal("0.01")


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
        "profit_sweep_exchange_native": "0.00048072",
        "profit_sweep_exchange_quote_proceeds": "0.08355164",
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
        "profit_sweep_exchange_native": "0.00002",
        "profit_sweep_exchange_quote_proceeds": "0.90955257",
        "profit_sweep_quote_proceeds": "0.90955257",
        "profit_sweep_quote_proceeds_lifetime": "1.23016371",
        "profit_sweep_reason": "take_profit; dust_pool_sweep; proceeds_reconciled",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    at_live = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("120000")})
    assert at_live == Decimal("0.90955257")


def test_profit_sweep_display_ignores_dust_padded_journal_without_exchange() -> None:
    row = {
        "status": "closed",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "realized_pnl": "1.26",
        "realized_pnl_collateral_native": "0.00002",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.00002",
        "profit_sweep_quote_proceeds": "3.58959158",
        "profit_sweep_reason": "take_profit; dust_pool_sweep; proceeds_reconciled",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    at_live = realized_pnl_usdc_at_spot(row, {"BTC": Decimal("120000")})
    assert at_live == Decimal("0.00002") * Decimal("120000")


def test_profit_sweep_disposition_prefers_exchange_native_over_journal_amount() -> None:
    from deribit_engine.realized_summary import _profit_disposition_for_row

    row = {
        "status": "closed",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "realized_pnl": "20",
        "realized_pnl_collateral_native": "0.0003",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.0003",
        "profit_sweep_exchange_native": "0.0002",
        "profit_sweep_exchange_quote_proceeds": "12.5",
        "profit_sweep_quote_proceeds": "18.9",
        "profit_sweep_reason": "take_profit; dust_pool_sweep; proceeds_reconciled",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    disp = _profit_disposition_for_row(row)
    assert disp is not None
    assert disp["swept_native"] == Decimal("0.0002")
    assert disp["held"] == Decimal("0.0001")
    assert disp["swept_usdt"] == Decimal("12.5")


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


def test_itm_spot_exit_net_not_counted_before_restore() -> None:
    group = TradeGroup.from_dict(
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "quantity": "1",
            "covered_underlying_quantity": "1",
            "realized_pnl": "-100",
            "realized_pnl_collateral_native": "-0.006",
            "spot_exit_status": "filled",
            "spot_exit_amount": "0.9355",
            "spot_exit_quote_proceeds": "1763",
            "spot_exit_quote_proceeds_lifetime": "1763",
            "spot_exit_settlement_loss": "0.0645",
            "short_entry_average_price": "0.01",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        }
    )
    assert itm_spot_exit_net_usdt_for_total_profit(group) is None
    total = total_realized_usdc_from_swap_disposition(
        [group.to_dict()],
        spot_index={"BTC": Decimal("1885")},
    )
    # Before restore: do not treat cover sale proceeds as Total profit.
    assert total is None or abs(total) < Decimal("0.01")


def test_ym_style_unrestored_cover_sale_not_in_total_profit() -> None:
    """Two ITM cover sales with skipped/missing restore must not dominate Total profit."""
    rows = [
        {
            "group_id": "0095",
            "status": "closed",
            "strategy": "covered_call",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "quantity": "0.1",
            "covered_underlying_quantity": "0.1",
            "realized_pnl": "-660.72",
            "realized_pnl_collateral_native": "-0.00828875",
            "spot_exit_status": "filled",
            "spot_exit_amount": "0.0915",
            "spot_exit_instrument_name": "BTC_USDT",
            "spot_exit_quote_proceeds": "7294.5676",
            "spot_exit_quote_proceeds_lifetime": "7294.5676",
            "spot_exit_settlement_loss": "0.0084211",
            "spot_restore_status": "skipped",
            "short_entry_average_price": "0.0015",
            "entry_fee_collateral": "0.00001875",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        },
        {
            "group_id": "0096",
            "status": "closed",
            "strategy": "covered_call",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "quantity": "0.1",
            "covered_underlying_quantity": "0.1",
            "realized_pnl": "-542.94",
            "realized_pnl_collateral_native": "-0.00673",
            "spot_exit_status": "filled",
            "spot_exit_amount": "0.0929",
            "spot_exit_instrument_name": "BTC_USDC",
            "spot_exit_quote_proceeds": "7491.5317",
            "spot_exit_quote_proceeds_lifetime": "7491.5317",
            "spot_exit_settlement_loss": "0.00703342",
            "short_entry_average_price": "0.0045",
            "entry_fee_collateral": "0.00003",
            "closed_timestamp_ms": 1_700_100_000_000,
            "entry_timestamp_ms": 1_699_100_000_000,
        },
    ]
    g95 = TradeGroup.from_dict(rows[0])
    g96 = TradeGroup.from_dict(rows[1])
    parsed = [g95, g96]
    assert itm_spot_exit_net_usdt_for_total_profit(g95, parsed) is None
    assert itm_spot_exit_net_usdt_for_total_profit(g96, parsed) is None
    total = total_realized_usdc_from_swap_disposition(
        rows,
        spot_index={"BTC": Decimal("77014.57")},
    )
    assert total is None or abs(total) < Decimal("50")
    assert total is None or abs(total) < Decimal("7000")


def test_itm_spot_exit_net_when_both_legs_filled_despite_plan_gap() -> None:
    """Incomplete SWAP journals may overstate spot_exit_amount after restore."""
    from deribit_engine.spot_restore_ops import itm_spot_exit_net_usdt_for_total_profit

    group = TradeGroup.from_dict(
        {
            "status": "closed",
            "collateral_currency": "ETH",
            "currency": "ETH",
            "quantity": "1",
            "covered_underlying_quantity": "1",
            "realized_pnl": "-11",
            "realized_pnl_collateral_native": "-0.006",
            "spot_exit_status": "filled",
            "spot_exit_amount": "0.9937",
            "spot_exit_quote_proceeds": "1871.62",
            "spot_exit_quote_proceeds_lifetime": "1871.62",
            "spot_exit_settlement_loss": "0.01548",
            "spot_restore_status": "filled",
            "spot_restore_amount": "0.9441",
            "spot_restore_quote_spent": "1753.98",
            "spot_restore_quote_spent_lifetime": "1753.98",
            "short_entry_average_price": "0.0095",
            "entry_fee_collateral": "0.00027",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        }
    )
    net = itm_spot_exit_net_usdt_for_total_profit(group)
    # Legacy fold: premium USDT attributed to Profit swap, not ITM cover net.
    from deribit_engine.spot_restore_ops import itm_folded_premium_usdt

    folded = itm_folded_premium_usdt(group)
    assert folded > 0
    assert net == Decimal("1871.62") - Decimal("1753.98") - folded


def test_itm_spot_exit_net_counted_after_restore_in_total_profit() -> None:
    """Cover-only ITM: Total profit = exit − restore (no premium fold)."""
    rows = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "quantity": "1",
            "covered_underlying_quantity": "1",
            "realized_pnl": "50",
            "realized_pnl_collateral_native": "0.001",
            # cover−settle only (premium not folded)
            "spot_exit_status": "filled",
            "spot_exit_amount": "1",
            "spot_exit_quote_proceeds": "10000",
            "spot_exit_quote_proceeds_lifetime": "10000",
            "spot_exit_settlement_loss": "0",
            "spot_restore_status": "filled",
            "spot_restore_amount": "0.999",
            "spot_restore_quote_spent": "9900",
            "spot_restore_quote_spent_lifetime": "9900",
            "short_entry_average_price": "0.001",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        }
    ]
    group = TradeGroup.from_dict(rows[0])
    net = itm_spot_exit_net_usdt_for_total_profit(group)
    assert net == Decimal("100")
    total = total_realized_usdc_from_swap_disposition(
        rows,
        spot_index={"BTC": Decimal("10000")},
    )
    # ITM net 100 + unswept premium native 0.001 × 10000
    assert total == Decimal("110")


def test_itm_spot_exit_excludes_native_disposition_double_count() -> None:
    """Legacy folded premium: Profit swap gets premium USDT; ITM net excludes it."""
    rows = [
        {
            "status": "closed",
            "collateral_currency": "BTC",
            "currency": "BTC",
            "quantity": "1",
            "covered_underlying_quantity": "1",
            "realized_pnl": "100",
            "realized_pnl_collateral_native": "0.01",
            "spot_exit_status": "filled",
            "spot_exit_amount": "1.01",
            "spot_exit_quote_proceeds": "10100",
            "spot_exit_quote_proceeds_lifetime": "10100",
            "spot_exit_settlement_loss": "0",
            "spot_restore_status": "filled",
            "spot_restore_amount": "1",
            "spot_restore_quote_spent": "10000",
            "spot_restore_quote_spent_lifetime": "10000",
            "short_entry_average_price": "0.01",
            "closed_timestamp_ms": 1_700_000_000_000,
            "entry_timestamp_ms": 1_699_000_000_000,
        }
    ]
    group = TradeGroup.from_dict(rows[0])
    from deribit_engine.spot_restore_ops import itm_folded_premium_usdt

    folded = itm_folded_premium_usdt(group)
    assert folded > 0
    net = itm_spot_exit_net_usdt_for_total_profit(group)
    assert net is not None
    assert abs(net - (Decimal("100") - folded)) < Decimal("0.01")
    total = total_realized_usdc_from_swap_disposition(
        rows,
        spot_index={"BTC": Decimal("100000")},
    )
    # Total ≈ cover round-trip + premium Sold ≈ 100 (not + native×spot MTM).
    assert total is not None
    assert abs(total - Decimal("100")) < Decimal("0.01")


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


def test_fill_stats_sold_includes_itm_folded_premium() -> None:
    """AN #0035-style: fold goes to Profit swap Sold, not Remaining, and is not double-counted."""
    from deribit_engine.realized_summary import (
        _aggregate_profit_disposition,
        _summarize_profit_disposition,
    )
    from deribit_engine.spot_restore_ops import itm_folded_premium_usdt

    itm = {
        "status": "closed",
        "collateral_currency": "ETH",
        "currency": "ETH",
        "quantity": "1",
        "covered_underlying_quantity": "1",
        "realized_pnl": "-3.76",
        "realized_pnl_collateral_native": "0.00923",
        "spot_exit_status": "filled",
        "spot_exit_amount": "0.9937",
        "spot_exit_quote_proceeds": "1871.62",
        "spot_exit_quote_proceeds_lifetime": "1871.62",
        "spot_exit_settlement_loss": "0.01548",
        "spot_restore_status": "filled",
        "spot_restore_amount": "1.0002",
        "spot_restore_quote_spent": "1857.99",
        "spot_restore_quote_spent_lifetime": "1857.99",
        "short_entry_average_price": "0.0095",
        "entry_fee_collateral": "0.00027",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    leftover = {
        "status": "closed",
        "collateral_currency": "ETH",
        "currency": "ETH",
        "realized_pnl": "5",
        "realized_pnl_collateral_native": "0.002455",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.002455",
        "profit_sweep_exchange_native": "0.0002",
        "profit_sweep_exchange_quote_proceeds": "0.38",
        "profit_sweep_quote_proceeds": "0.38",
        "profit_sweep_reason": "take_profit; proceeds_reconciled",
        "closed_timestamp_ms": 1_700_100_000_000,
        "entry_timestamp_ms": 1_699_100_000_000,
    }
    swept = {
        "status": "closed",
        "collateral_currency": "ETH",
        "currency": "ETH",
        "realized_pnl": "8",
        "realized_pnl_collateral_native": "0.005",
        "profit_sweep_status": "filled",
        "profit_sweep_amount": "0.005",
        "profit_sweep_exchange_native": "0.005",
        "profit_sweep_exchange_quote_proceeds": "8",
        "profit_sweep_quote_proceeds": "8",
        "profit_sweep_reason": "take_profit; proceeds_reconciled",
        "closed_timestamp_ms": 1_700_200_000_000,
        "entry_timestamp_ms": 1_699_200_000_000,
    }
    rows = [itm, leftover, swept]
    group = TradeGroup.from_dict(itm)
    folded = itm_folded_premium_usdt(group)
    assert folded > 0
    fill_stats = {
        "ETH": {
            "display_native_sold": "0.0052",
            "display_usdt": "8.38",
            "net_native_sold": "0.0052",
            "net_usdt": "8.38",
        }
    }
    spot = {"ETH": Decimal("1878")}
    disposition = _aggregate_profit_disposition(rows)
    assert disposition is not None
    assert disposition["folded_swept_native"]["ETH"] == Decimal("0.00923")
    summary = _summarize_profit_disposition(disposition, spot_index=spot, fill_stats=fill_stats)
    assert summary["spot_sold"]["ETH"] == Decimal("0.0052") + Decimal("0.00923")
    assert abs(summary["spot_sold_quote"]["ETH"] - (Decimal("8.38") + folded)) < Decimal("0.01")
    # Remaining = leftover exchange shortfall, not the folded 0.00923.
    assert abs(summary["spot_held"]["ETH"] - Decimal("0.002255")) < Decimal("0.000001")

    itm_net = itm_spot_exit_net_usdt_for_total_profit(group)
    assert itm_net == Decimal("1871.62") - Decimal("1857.99") - folded
    total = total_realized_usdc_from_swap_disposition(rows, spot_index=spot, fill_stats=fill_stats)
    expected = Decimal("8.38") + folded + Decimal("0.002255") * Decimal("1878") + itm_net
    assert total is not None
    assert abs(total - expected) < Decimal("0.02")


def test_jack_0070_fold_stays_in_sold_not_remaining() -> None:
    """Jack #0070 entry premium folded into exit is Sold, not unswept Remaining."""
    from deribit_engine.realized_summary import (
        _aggregate_profit_disposition,
        _summarize_profit_disposition,
    )
    from deribit_engine.spot_restore_ops import itm_folded_premium_native, itm_folded_premium_usdt

    row = {
        "status": "closed",
        "collateral_currency": "ETH",
        "currency": "ETH",
        "quantity": "2",
        "covered_underlying_quantity": "2",
        "realized_pnl": "15",
        "realized_pnl_collateral_native": "0.00812",
        "short_entry_average_price": "0.01",
        "entry_fee_collateral": "0.00054",
        "spot_exit_status": "filled",
        "spot_exit_amount": "2.0156",
        "spot_exit_quote_proceeds": "3839.52123",
        "spot_exit_quote_proceeds_lifetime": "3839.52123",
        "spot_exit_settlement_loss": "0.00381376",
        "spot_restore_status": "filled",
        "spot_restore_amount": "1.9999",
        "spot_restore_quote_spent": "3809.60951",
        "spot_restore_quote_spent_lifetime": "3809.60951",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    group = TradeGroup.from_dict(row)
    folded_native = itm_folded_premium_native(group)
    folded_usdt = itm_folded_premium_usdt(group)
    assert abs(folded_native - Decimal("0.01946")) < Decimal("0.00001")
    assert folded_usdt > 0
    fill_stats = {
        "ETH": {
            "display_native_sold": "0.005",
            "display_usdt": "8",
            "net_native_sold": "0.005",
            "net_usdt": "8",
        }
    }
    disposition = _aggregate_profit_disposition([row])
    assert disposition is not None
    summary = _summarize_profit_disposition(disposition, spot_index={"ETH": Decimal("1878")}, fill_stats=fill_stats)
    assert abs(summary["spot_sold"]["ETH"] - (Decimal("0.005") + folded_native)) < Decimal("0.00001")
    assert abs(summary["spot_sold_quote"]["ETH"] - (Decimal("8") + folded_usdt)) < Decimal("0.01")
    assert summary["spot_held"]["ETH"] == Decimal("0")
    net = itm_spot_exit_net_usdt_for_total_profit(group)
    assert net == Decimal("3839.52123") - Decimal("3809.60951") - folded_usdt


def test_jack_0103_usdc_restore_without_quote_not_in_total() -> None:
    """Jack #0103: operator BTC_USDC restore is cover-complete but has no quote journal.

    Must not treat the USDT exit notional as Total profit.
    """
    from deribit_engine.spot_restore_ops import itm_spot_round_trip_complete

    row = {
        "group_id": "0103",
        "status": "closed",
        "strategy": "covered_call",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "quantity": "0.1",
        "covered_underlying_quantity": "0.1",
        "realized_pnl": "-260.88",
        "realized_pnl_collateral_native": "-0.00327275",
        "spot_exit_status": "filled",
        "spot_exit_amount": "0.0965",
        "spot_exit_instrument_name": "BTC_USDT",
        "spot_exit_quote_proceeds": "7676.4094",
        "spot_exit_quote_proceeds_lifetime": "7676.4094",
        "spot_exit_settlement_loss": "0.00340308",
        "spot_restore_status": "filled",
        "spot_restore_amount": "0.1",
        "spot_restore_instrument_name": "BTC_USDC",
        "spot_restore_reason": "operator_manual_spot_restore",
        "short_entry_average_price": "0.0014",
        "entry_fee_collateral": "0.00001575",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    group = TradeGroup.from_dict(row)
    assert itm_spot_round_trip_complete(group) is True
    assert itm_spot_exit_net_usdt_for_total_profit(group) is None
    total = total_realized_usdc_from_swap_disposition(
        [row],
        spot_index={"BTC": Decimal("77000")},
    )
    assert total is None or abs(total) < Decimal("300")
    assert total is None or abs(total) < Decimal("7000")


def test_eugene_0033_overbuy_not_in_parent_exit_restore_net() -> None:
    """Eugene #0022 already restored 0.1; #0033 extra 0.02 must not hit Total."""
    parent = {
        "group_id": "0022",
        "status": "closed",
        "strategy": "covered_call",
        "collateral_currency": "BTC",
        "currency": "BTC",
        "quantity": "0.1",
        "covered_underlying_quantity": "0.1",
        "realized_pnl": "-261.12",
        "realized_pnl_collateral_native": "-0.00327575",
        "spot_exit_status": "filled",
        "spot_exit_amount": "0.0965",
        "spot_exit_instrument_name": "BTC_USDT",
        "spot_exit_quote_proceeds": "7688.8399",
        "spot_exit_quote_proceeds_lifetime": "7688.8399",
        "spot_exit_settlement_loss": "0.00340308",
        "spot_restore_status": "filled",
        "spot_restore_amount": "0.1",
        "spot_restore_instrument_name": "BTC_USDC",
        "spot_restore_quote_spent": "7856.6957",
        "spot_restore_quote_spent_lifetime": "7856.6957",
        "cash_secured_group_id": "0033",
        "cash_secured_group_ids": ["0032", "0033"],
        "short_entry_average_price": "0.0014",
        "entry_fee_collateral": "0.00001575",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    child = {
        "group_id": "0033",
        "status": "closed",
        "strategy": "cash_secured",
        "collateral_currency": "USDC",
        "currency": "BTC",
        "quantity": "0.02",
        "realized_pnl": "-7.74017417",
        "realized_pnl_collateral_native": "-7.74017417",
        "spot_restore_status": "filled",
        "spot_restore_amount": "0.02",
        "spot_restore_instrument_name": "BTC_USDC",
        "spot_restore_quote_spent": "1539.26",
        "spot_restore_quote_spent_lifetime": "1539.26",
        "spot_restore_reason": "cash_secured_itm_assignment",
        "cash_secured_from_group_id": "0022",
        "closed_timestamp_ms": 1_700_100_000_000,
        "entry_timestamp_ms": 1_699_100_000_000,
    }
    g22 = TradeGroup.from_dict(parent)
    g33 = TradeGroup.from_dict(child)
    parsed = [g22, g33]
    net = itm_spot_exit_net_usdt_for_total_profit(g22, parsed)
    assert net is not None
    assert abs(net - (Decimal("7688.8399") - Decimal("7856.6957"))) < Decimal("0.02")
    assert abs(net) < Decimal("200")
    total = total_realized_usdc_from_swap_disposition(
        [parent, child],
        spot_index={"BTC": Decimal("77000")},
    )
    assert total is not None
    assert abs(total + Decimal("1707")) > Decimal("1000")
    assert abs(total) < Decimal("400")


def test_csp_premium_split_goes_to_spot_remaining_and_leftover_usdc() -> None:
    from deribit_engine.realized_summary import _aggregate_profit_disposition, _profit_disposition_for_row

    swapped = {
        "status": "closed",
        "strategy": "cash_secured",
        "collateral_currency": "USDC",
        "currency": "ETH",
        "cash_secured_from_group_id": "0095",
        "entry_credit": "6.035",
        "realized_pnl": "6.035",
        "realized_close_debit": "0",
        "realized_close_fee": "0",
        "csp_premium_swap_status": "filled",
        "csp_premium_swap_amount": "5.7845",
        "csp_premium_swap_native": "0.0023",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    skipped = {
        "status": "closed",
        "strategy": "cash_secured",
        "collateral_currency": "USDC",
        "currency": "BTC",
        "cash_secured_from_group_id": "0097",
        "entry_credit": "5.6875",
        "realized_pnl": "5.6875",
        "realized_close_debit": "0",
        "realized_close_fee": "0",
        "csp_premium_swap_status": "skipped",
        "csp_premium_swap_reason": "dust_below_min_omitted",
        "closed_timestamp_ms": 1_700_000_000_000,
        "entry_timestamp_ms": 1_699_000_000_000,
    }
    swapped_disp = _profit_disposition_for_row(swapped)
    assert swapped_disp is not None
    assert swapped_disp["held"] == Decimal("0.2505")
    assert swapped_disp["csp_spot_book"] == "ETH"
    assert swapped_disp["csp_spot_native"] == Decimal("0.0023")

    skipped_disp = _profit_disposition_for_row(skipped)
    assert skipped_disp is not None
    assert skipped_disp["held"] == Decimal("5.6875")
    assert skipped_disp.get("csp_spot_native", Decimal("0")) == Decimal("0")

    disposition = _aggregate_profit_disposition([swapped, skipped])
    assert disposition is not None
    assert disposition["held_native"]["USDC"] == Decimal("5.938")
    assert disposition["held_native"]["ETH"] == Decimal("0.0023")
    assert disposition["held_native"]["BTC"] == Decimal("0")

    eth = Decimal("2500")
    total = total_realized_usdc_from_swap_disposition(
        [swapped, skipped],
        spot_index={"BTC": Decimal("80000"), "ETH": eth},
    )
    expected = Decimal("5.938") + Decimal("0.0023") * eth
    assert total is not None
    assert abs(total - expected) < Decimal("0.001")
