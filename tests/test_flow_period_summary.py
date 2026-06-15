from __future__ import annotations

from decimal import Decimal

from deribit_engine.investor_cash_flow import (
    SubscriptionFlowLine,
    period_flow_report_dict,
    summarize_subscription_flow_lines,
)


def _line(
    *,
    flow_type: str,
    amount_native: Decimal,
    usdc_equiv: Decimal | None = None,
) -> SubscriptionFlowLine:
    return SubscriptionFlowLine(
        identity_label="covered_call",
        client_id="cid",
        book="USDC",
        timestamp_ms=1_700_000_000_000,
        flow_type=flow_type,
        amount_native=amount_native,
        usdc_equiv=usdc_equiv if usdc_equiv is not None else amount_native,
        included_in_subscription=True,
    )


def test_summarize_subscription_flow_lines_splits_types() -> None:
    lines = (
        _line(flow_type="deposit", amount_native=Decimal("1000")),
        _line(flow_type="withdrawal", amount_native=Decimal("-300")),
        _line(flow_type="transfer_in", amount_native=Decimal("50")),
        _line(flow_type="transfer_out", amount_native=Decimal("-20")),
    )
    summary = summarize_subscription_flow_lines(lines)
    assert summary.deposit_usdc == Decimal("1000")
    assert summary.withdrawal_usdc == Decimal("300")
    assert summary.transfer_in_usdc == Decimal("50")
    assert summary.transfer_out_usdc == Decimal("20")
    assert summary.net_subscription_usdc == Decimal("730")
    assert summary.deposit_count == 1
    assert summary.withdrawal_count == 1
    assert summary.transfer_count == 2


def test_period_flow_report_dict_includes_summary() -> None:
    lines = (_line(flow_type="deposit", amount_native=Decimal("500")),)
    report = period_flow_report_dict(
        lines,
        start_timestamp_ms=1,
        end_timestamp_ms=2,
    )
    assert report["summary"]["net_subscription_usdc"] == "500"
    assert len(report["lines"]) == 1
    assert "External wallet fee payments" in report["note"]
