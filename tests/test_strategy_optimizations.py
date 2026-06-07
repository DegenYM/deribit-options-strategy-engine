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
from deribit_engine.vol_metrics import (
    dvol_iv_rank_from_daily_rows,
    iv_rank,
    passes_iv_entry_gate,
    realized_vol_annualized,
    trend_signal_vs_ma,
)


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


def test_dvol_iv_rank_from_daily_rows_uses_candle_high_low_range():
    rows = [
        [1_000, 40.0, 45.0, 35.0, 40.0],
        [2_000, 50.0, 55.0, 45.0, 50.0],
        [3_000, 60.0, 70.0, 50.0, 55.0],
    ]
    rank = dvol_iv_rank_from_daily_rows(rows, ts_ms=3_000, lookback_days=3)
    # official high/low method: (55 - 35) / (70 - 35) = 20/35
    assert rank == Decimal("20") / Decimal("35")
    # Close-only ranks the latest close against close min/max only:
    # (55 - 40) / (55 - 40) = 1, which overstates rank vs the high/low method.
    close_only = iv_rank(Decimal("55"), lookback=[Decimal("40"), Decimal("50"), Decimal("55")])
    assert close_only == Decimal("1")
    assert close_only != rank


def test_iv_entry_gate_fail_open_when_metrics_missing():
    assert passes_iv_entry_gate(
        iv_rank_value=None,
        iv_minus_rv=None,
        min_iv_rank=Decimal("0.35"),
        max_iv_rank=Decimal("1"),
        min_iv_minus_rv=Decimal("0.02"),
        gate_enabled=True,
    )
    assert not passes_iv_entry_gate(
        iv_rank_value=Decimal("0.10"),
        iv_minus_rv=Decimal("0.05"),
        min_iv_rank=Decimal("0.35"),
        max_iv_rank=Decimal("1"),
        min_iv_minus_rv=Decimal("0.02"),
        gate_enabled=True,
    )


def test_iv_entry_gate_uses_currency_specific_bounds(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(
        tmp_path,
        enable_iv_entry_gate=True,
        min_iv_rank=Decimal("0.30"),
        btc_min_iv_rank=Decimal("0.25"),
        eth_min_iv_rank=Decimal("0.18"),
    )
    selector = StrategySelector(config)
    selector.update_vol_entry_context(
        iv_rank_by_currency={"BTC": Decimal("0.22"), "ETH": Decimal("0.22")},
        iv_minus_rv_by_currency={"BTC": Decimal("0.05"), "ETH": Decimal("0.05")},
    )

    assert selector._iv_entry_rejection_reason("BTC") == "iv_entry_gate"
    assert selector._iv_entry_rejection_reason("ETH") is None


def test_index_chart_close_series_accepts_two_column_rows():
    from deribit_engine.vol_metrics import index_chart_close_series

    series = index_chart_close_series([[1_700_000_000_000, 70000.5], [1_700_086_400_000, 71000.0]])
    assert series == [(1_700_000_000_000, Decimal("70000.5")), (1_700_086_400_000, Decimal("71000.0"))]


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


def _naked_candidate(
    *,
    option_type: str,
    delta: Decimal,
    iv: Decimal,
    apr: Decimal = Decimal("0.20"),
    currency: str = "BTC",
    strike: Decimal | None = None,
    index_price: Decimal = Decimal("70000"),
):
    from deribit_engine.models import NakedPutCandidate, RiskRegime, SpreadLeg

    if strike is None:
        strike = Decimal("63000") if option_type == "put" else Decimal("77000")
    suffix = "C" if option_type == "call" else "P"
    short = SpreadLeg(
        instrument_name=f"{currency}_USDC-30JUN26-{int(strike)}-{suffix}",
        strike=strike,
        quantity=Decimal("1"),
        min_trade_amount=Decimal("0.01"),
        contract_size=Decimal("0.01"),
        entry_price=Decimal("500"),
        target_price=Decimal("500"),
        best_bid_price=Decimal("500"),
        best_ask_price=Decimal("505"),
        delta=delta,
        tick_size=Decimal("2.5"),
        tick_size_steps=(),
        expiration_timestamp_ms=1_800_000_000_000,
        index_price=index_price,
        quote_currency="USDC",
        settlement_currency="USDC",
        instrument_type="linear",
    )
    return NakedPutCandidate(
        currency=currency,
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
        option_type=option_type,
        short_iv=iv,
    )


def test_dynamic_target_delta_shifts_with_vrp(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(
        tmp_path,
        enable_dynamic_target_delta=True,
        dynamic_target_delta_vrp_ref=Decimal("0.05"),
        dynamic_target_delta_strength=Decimal("1"),
    )
    selector = StrategySelector(config)
    pdmin, pdmax = config.preferred_put_delta_bounds("BTC")
    center = (pdmin + pdmax) / Decimal("2")

    # No VRP reading -> static midpoint.
    assert selector._preferred_target_delta("BTC", "put") == center

    # Rich vol (VRP above reference) pushes the target toward the lower-delta edge.
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0.10")})
    rich = selector._preferred_target_delta("BTC", "put")
    assert rich < center
    assert rich >= pdmin

    # Thin vol (VRP below reference) pushes the target toward the higher-delta edge.
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0.0")})
    thin = selector._preferred_target_delta("BTC", "put")
    assert thin > center
    assert thin <= pdmax


def test_dynamic_target_delta_disabled_is_static(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path)  # enable_dynamic_target_delta defaults to False
    selector = StrategySelector(config)
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0.10")})
    pdmin, pdmax = config.preferred_put_delta_bounds("BTC")
    assert selector._preferred_target_delta("BTC", "put") == (pdmin + pdmax) / Decimal("2")


def test_skew_side_selection_prefers_richer_wing(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(
        tmp_path,
        enable_weighted_candidate_scoring=True,
        enable_skew_side_selection=True,
        score_weight_skew=Decimal("10"),
        skew_side_min_rr=Decimal("0.02"),
    )
    selector = StrategySelector(config)
    put_pref = sum(config.preferred_put_delta_bounds("BTC")) / Decimal("2")
    call_pref = sum(config.preferred_call_delta_bounds("BTC")) / Decimal("2")

    # Puts priced richer than calls (positive risk reversal) -> put should win.
    put = _naked_candidate(option_type="put", delta=-put_pref, iv=Decimal("0.80"))
    call = _naked_candidate(option_type="call", delta=call_pref, iv=Decimal("0.60"))
    ranked = selector.take_top_scan_candidates([call, put], limit=5)
    assert ranked[0] is put

    # Flip the skew: calls richer -> call should win.
    put2 = _naked_candidate(option_type="put", delta=-put_pref, iv=Decimal("0.60"))
    call2 = _naked_candidate(option_type="call", delta=call_pref, iv=Decimal("0.80"))
    ranked2 = selector.take_top_scan_candidates([put2, call2], limit=5)
    assert ranked2[0] is call2


def test_skew_side_selection_disabled_no_tilt(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path, enable_weighted_candidate_scoring=True)
    selector = StrategySelector(config)
    put = _naked_candidate(option_type="put", delta=Decimal("-0.11"), iv=Decimal("0.80"))
    assert selector._skew_score_term(put) == Decimal("0")


def test_trend_signal_vs_ma_bullish_and_bearish():
    base = [Decimal("100")] * 19
    bullish = trend_signal_vs_ma(base + [Decimal("106")], ma_window=20, ref_pct=Decimal("0.05"))
    assert bullish is not None
    assert bullish > Decimal("0.5")
    bearish = trend_signal_vs_ma(base + [Decimal("94")], ma_window=20, ref_pct=Decimal("0.05"))
    assert bearish is not None
    assert bearish < Decimal("-0.5")


def test_trend_side_bias_prefers_put_in_bullish_tape(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path, enable_trend_side_bias=True, score_weight_trend=Decimal("10"))
    selector = StrategySelector(config)
    selector.update_vol_entry_context(trend_by_currency={"BTC": Decimal("0.8")})
    put = _naked_candidate(option_type="put", delta=Decimal("-0.11"), iv=Decimal("0.70"))
    call = _naked_candidate(option_type="call", delta=Decimal("0.11"), iv=Decimal("0.70"))
    ranked = selector.take_top_scan_candidates([call, put], limit=5)
    assert ranked[0] is put


def test_trend_side_bias_prefers_call_in_bearish_tape(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path, enable_trend_side_bias=True, score_weight_trend=Decimal("10"))
    selector = StrategySelector(config)
    selector.update_vol_entry_context(trend_by_currency={"BTC": Decimal("-0.8")})
    put = _naked_candidate(option_type="put", delta=Decimal("-0.11"), iv=Decimal("0.70"))
    call = _naked_candidate(option_type="call", delta=Decimal("0.11"), iv=Decimal("0.70"))
    ranked = selector.take_top_scan_candidates([put, call], limit=5)
    assert ranked[0] is call


def test_trend_side_bias_lexicographic_sort(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path, enable_trend_side_bias=True, enable_weighted_candidate_scoring=False)
    selector = StrategySelector(config)
    selector.update_vol_entry_context(trend_by_currency={"BTC": Decimal("0.8")})
    put = _naked_candidate(option_type="put", delta=Decimal("-0.11"), iv=Decimal("0.70"))
    call = _naked_candidate(option_type="call", delta=Decimal("0.11"), iv=Decimal("0.70"))
    ranked = sorted([call, put], key=selector.naked_put_sort_key)
    assert ranked[0] is put


def test_trend_side_bias_disabled_is_neutral(tmp_path):
    from deribit_engine.strategy import StrategySelector

    config = make_config(tmp_path, enable_trend_side_bias=False)
    selector = StrategySelector(config)
    selector.update_vol_entry_context(trend_by_currency={"BTC": Decimal("0.8")})
    put = _naked_candidate(option_type="put", delta=Decimal("-0.11"), iv=Decimal("0.70"))
    assert selector._trend_score_term(put) == Decimal("0")
    assert selector._trend_side_sort_tier(put) == 0


def test_config_defaults_trend_side_bias_on(tmp_path):
    config = make_config(tmp_path)
    assert config.enable_trend_side_bias is True
