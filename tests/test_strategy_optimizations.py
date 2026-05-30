from __future__ import annotations

from decimal import Decimal

from conftest import future_expiry, make_config

from deribit_engine.entry_gates import entry_cooldown_active, last_entry_timestamp_ms_by_book
from deribit_engine.exit_eval import (
    backtest_tp_target_premium,
    dynamic_tp_capture_pct,
    evaluate_early_exit_reason,
    exit_eval_context_from_config,
)
from deribit_engine.models import OrderBookSnapshot, TradeGroup
from deribit_engine.trade_apr import position_apr_capital_base, remaining_apr_for_group
from deribit_engine.vol_metrics import iv_rank, passes_iv_entry_gate, realized_vol_annualized


def _group(**kwargs) -> TradeGroup:
    expiry = future_expiry(10)
    base = dict(
        group_id="g1",
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("1"),
        entry_timestamp_ms=1_700_000_000_000,
        expiration_timestamp_ms=expiry,
        short_instrument_name="BTC_USDC-30JUN26-63000-P",
        short_strike=Decimal("63000"),
        entry_credit=Decimal("500"),
        original_entry_credit=Decimal("500"),
        max_loss=Decimal("5000"),
        regime_at_entry="normal",
        estimated_im_collateral=Decimal("2000"),
        entry_index_usd=Decimal("70000"),
        option_type="put",
        strategy="naked_short",
        current_debit=Decimal("50"),
        current_close_fee=Decimal("10"),
        profit_capture=Decimal("0.90"),
        short_delta=Decimal("0.10"),
    )
    base.update(kwargs)
    return TradeGroup(**base)


def test_entry_cooldown_blocks_recent_book_entry():
    group = _group(entry_timestamp_ms=1_000_000)
    last = last_entry_timestamp_ms_by_book([group])
    assert entry_cooldown_active(
        book="USDC", last_entry_by_book=last, now_ms=1_000_000 + 5 * 60_000, cooldown_minutes=20
    )
    assert not entry_cooldown_active(
        book="USDC", last_entry_by_book=last, now_ms=1_000_000 + 25 * 60_000, cooldown_minutes=20
    )


def test_remaining_apr_uses_position_capital_base(tmp_path):
    group = _group()
    capital = position_apr_capital_base(
        strategy=group.strategy,
        collateral_currency=group.collateral_currency,
        option_type=group.option_type,
        quantity=group.quantity,
        contract_size=Decimal("1"),
        strike=group.short_strike,
        index_price_usd=group.entry_index_usd,
        estimated_im_collateral=group.estimated_im_collateral,
        covered_underlying_quantity=group.covered_underlying_quantity,
    )
    assert capital == group.short_strike * group.quantity
    remaining = remaining_apr_for_group(
        remaining_credit=Decimal("190"),
        capital_base=capital,
        dte_days=Decimal("10"),
    )
    assert remaining > 0


def test_early_exit_uses_unified_capital_base(tmp_path):
    config = make_config(tmp_path)
    ctx = exit_eval_context_from_config(config)
    group = _group()
    book = OrderBookSnapshot(
        instrument_name=group.short_instrument_name,
        best_bid_price=Decimal("48"),
        best_bid_amount=Decimal("1"),
        best_ask_price=Decimal("50"),
        best_ask_amount=Decimal("1"),
        mark_price=Decimal("185"),
        index_price=Decimal("70000"),
        delta=Decimal("-0.10"),
        iv=Decimal("0.55"),
        open_interest=Decimal("100"),
    )
    reason = evaluate_early_exit_reason(group, book, ctx)
    assert reason == "early_exit_low_apr"


def test_dynamic_tp_threshold(tmp_path):
    config = make_config(tmp_path, enable_dynamic_tp=True, tp_capture_pct_dte_long=Decimal("0.40"))
    ctx = exit_eval_context_from_config(config)
    assert dynamic_tp_capture_pct(Decimal("20"), ctx) == Decimal("0.40")
    assert dynamic_tp_capture_pct(Decimal("5"), ctx) == config.tp_capture_pct_dte_short


def test_backtest_tp_target_respects_dynamic_threshold(tmp_path):
    config = make_config(tmp_path, enable_dynamic_tp=True, tp_capture_pct_dte_long=Decimal("0.40"))
    ctx = exit_eval_context_from_config(config)
    target = backtest_tp_target_premium(Decimal("1000"), Decimal("20"), ctx)
    assert target == Decimal("600")


def test_iv_rank_and_gate():
    lookback = [Decimal("0.40"), Decimal("0.50"), Decimal("0.60"), Decimal("0.55")]
    rank = iv_rank(Decimal("0.58"), lookback=lookback)
    assert rank is not None
    assert rank > Decimal("0.5")
    assert passes_iv_entry_gate(
        iv_rank_value=rank,
        iv_minus_rv=Decimal("0.05"),
        min_iv_rank=Decimal("0.30"),
        max_iv_rank=Decimal("1"),
        min_iv_minus_rv=Decimal("0.02"),
        gate_enabled=True,
    )


def test_realized_vol_positive():
    closes = [Decimal(str(100 + i)) for i in range(40)]
    rv = realized_vol_annualized(closes, window=30)
    assert rv is not None
    assert rv > 0


def test_weighted_scoring_prefers_higher_apr(tmp_path):
    from deribit_engine.models import NakedPutCandidate, RiskRegime, SpreadLeg
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path, enable_weighted_candidate_scoring=True)
    selector = StrategySelector(config)

    def leg(apr: Decimal, spread_ratio: Decimal) -> NakedPutCandidate:
        ask = Decimal("500") * (Decimal("1") + spread_ratio)
        short = SpreadLeg(
            instrument_name="BTC_USDC-30JUN26-63000-P",
            strike=Decimal("63000"),
            quantity=Decimal("1"),
            min_trade_amount=Decimal("0.01"),
            contract_size=Decimal("0.01"),
            entry_price=Decimal("500"),
            target_price=Decimal("500"),
            best_bid_price=Decimal("500"),
            best_ask_price=ask,
            delta=Decimal("-0.10"),
            tick_size=Decimal("2.5"),
            tick_size_steps=(),
            expiration_timestamp_ms=1_800_000_000_000,
            index_price=Decimal("70000"),
            quote_currency="USDC",
            settlement_currency="USDC",
            instrument_type="linear",
        )
        return NakedPutCandidate(
            currency="BTC",
            collateral_currency="USDC",
            quantity=Decimal("1"),
            dte_days=Decimal("14"),
            short_leg=short,
            screening_bid=Decimal("500"),
            screening_mark=Decimal("500"),
            target_limit_price=Decimal("500"),
            net_premium_native=Decimal("500"),
            fee_native=Decimal("1"),
            net_apr=apr,
            margin_efficiency=Decimal("0.2"),
            estimated_im_total=Decimal("2000"),
            estimated_mm_total=Decimal("1500"),
            regime=RiskRegime.NORMAL,
            preferred_delta=True,
            preferred_otm=True,
            in_target_apr_band=True,
            option_type="put",
        )

    high = leg(Decimal("0.20"), Decimal("0.10"))
    low = leg(Decimal("0.12"), Decimal("0.02"))
    ranked = sorted([low, high], key=selector.naked_put_sort_key)
    assert ranked[0] is high
