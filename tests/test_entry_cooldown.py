from __future__ import annotations

from decimal import Decimal

from conftest import future_expiry, make_config

from deribit_engine.engine.bot import DeribitOptionTrialBot
from deribit_engine.models import NakedPutCandidate, RiskRegime, SpreadLeg, TradeGroup
from deribit_engine.utils import utc_now_ms


def _sample_candidate() -> NakedPutCandidate:
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
        option_type="put",
    )


def test_entry_cooldown_skips_enter_best(tmp_path, fake_client):
    config = make_config(tmp_path, entry_cooldown_minutes=60)
    engine = DeribitOptionTrialBot(config, fake_client)
    state = engine.state_store.load()
    state.groups.append(
        TradeGroup(
            group_id="g-existing",
            currency="BTC",
            collateral_currency="USDC",
            quantity=Decimal("0.01"),
            entry_timestamp_ms=utc_now_ms() - 5 * 60_000,
            expiration_timestamp_ms=future_expiry(14),
            short_instrument_name="BTC_USDC-14APR26-63000-P",
            short_strike=Decimal("63000"),
            entry_credit=Decimal("500"),
            original_entry_credit=Decimal("500"),
            max_loss=Decimal("5000"),
            regime_at_entry="normal",
            status="closed",
            closed_timestamp_ms=utc_now_ms(),
        )
    )
    engine.state_store.save(state)
    context = engine._load_runtime()
    result = engine._enter_best_from_candidates(context, candidates=[_sample_candidate()], live=False)
    assert result["action"] == "entry_skipped"
    assert result["reason"] == "entry_cooldown_active"
