"""Per-currency entry gates: BTC and ETH regimes are evaluated independently."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from conftest import future_expiry

from deribit_engine.entry_gates import (
    append_underlying_regime_halt_reasons_for_usdc_book,
    build_halt_new_entries_by_currency,
    candidate_entry_halted,
    underlying_entry_halted,
)
from deribit_engine.models import NakedPutCandidate, PortfolioSnapshot, RiskRegime, SpreadLeg


def _minimal_snapshot(**kwargs) -> PortfolioSnapshot:
    base = dict(
        total_equity_usdc=Decimal("1000"),
        day_start_equity_usdc=Decimal("1000"),
        day_net_flow_usdc=Decimal("0"),
        day_pnl_usdc_ex_flow=Decimal("0"),
        day_drawdown_pct=Decimal("0"),
        open_max_loss=Decimal("0"),
        open_max_loss_pct=Decimal("0"),
        initial_margin_ratio=Decimal("0"),
        maintenance_margin_ratio=Decimal("0"),
        projected_max_profit_run_rate_usdc=Decimal("0"),
        projected_max_profit_apr=Decimal("0"),
        target_progress_ratio=Decimal("0"),
        regime=RiskRegime.ELEVATED,
        halt_new_entries=False,
        hard_derisk=False,
        cooldown_until_ms=None,
        cooling_down=False,
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.ELEVATED},
        regime_detail_by_currency={
            "BTC": ("market_conditions_normal",),
            "ETH": ("index_drawdown_elevated",),
        },
    )
    base.update(kwargs)
    return PortfolioSnapshot(**base)


def test_halt_new_entries_by_currency_splits_btc_eth():
    by_ccy = build_halt_new_entries_by_currency(
        managed_currencies=("BTC", "ETH"),
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": (), "ETH": ("index_drawdown_elevated",)},
        crisis_currencies_with_open_groups=set(),
        hard_derisk_on_crisis_open_group=True,
        portfolio_blocks_all=False,
    )
    assert by_ccy["BTC"] is False
    assert by_ccy["ETH"] is True


def _btc_candidate() -> NakedPutCandidate:
    short = SpreadLeg(
        instrument_name="BTC_USDC-14APR26-63000-P",
        strike=Decimal("63000"),
        quantity=Decimal("0.01"),
        min_trade_amount=Decimal("0.01"),
        contract_size=Decimal("0.01"),
        entry_price=Decimal("500"),
        target_price=Decimal("500"),
        best_bid_price=Decimal("500"),
        best_ask_price=Decimal("520"),
        delta=Decimal("-0.10"),
        tick_size=Decimal("2.5"),
        tick_size_steps=(),
        expiration_timestamp_ms=future_expiry(14),
        index_price=Decimal("70000"),
        quote_currency="USDC",
        settlement_currency="USDC",
        instrument_type="linear",
    )
    return NakedPutCandidate(
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("0.01"),
        dte_days=Decimal("14"),
        short_leg=short,
        screening_bid=Decimal("500"),
        screening_mark=Decimal("500"),
        target_limit_price=Decimal("500"),
        net_premium_native=Decimal("500"),
        fee_native=Decimal("1"),
        net_apr=Decimal("0.15"),
        margin_efficiency=Decimal("0.2"),
        estimated_im_total=Decimal("2000"),
        estimated_mm_total=Decimal("1500"),
        regime=RiskRegime.NORMAL,
        preferred_delta=True,
        preferred_otm=True,
        in_target_apr_band=True,
    )


def test_usdc_book_gets_underlying_regime_halt_reasons():
    reasons: dict[str, list[str]] = {"USDC": []}
    append_underlying_regime_halt_reasons_for_usdc_book(
        reasons,
        scan_underlyings=("BTC", "ETH"),
        halt_new_entries_by_currency={"BTC": False, "ETH": True},
        regime_by_currency={"BTC": RiskRegime.NORMAL, "ETH": RiskRegime.ELEVATED},
    )
    assert any("underlying ETH" in line for line in reasons["USDC"])
    assert not any("underlying BTC" in line for line in reasons["USDC"])


def test_underlying_entry_halted_blocks_eth_usdc_path():
    snap = _minimal_snapshot(
        halt_new_entries_by_currency={"BTC": False, "ETH": True},
    )
    assert underlying_entry_halted(snap, "ETH") is True
    assert underlying_entry_halted(snap, "BTC") is False


def test_candidate_entry_halted_respects_underlying_not_sibling():
    snap = _minimal_snapshot(
        halt_new_entries_by_currency={"BTC": False, "ETH": True},
        halt_entries_by_book={"USDC": False},
    )
    btc_candidate = _btc_candidate()
    eth_candidate = replace(_btc_candidate(), currency="ETH")
    assert candidate_entry_halted(snap, btc_candidate) is False
    assert candidate_entry_halted(snap, eth_candidate) is True
