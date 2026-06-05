from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import OptionInstrument, TradeGroup


@pytest.fixture
def engine(tmp_path) -> DeribitOptionTrialBot:
    config = make_config(tmp_path)
    return DeribitOptionTrialBot(config, FakeClient())


def _eth_call_instrument() -> OptionInstrument:
    return OptionInstrument(
        instrument_name="ETH-29MAY26-2300-C",
        base_currency="ETH",
        quote_currency="",
        settlement_currency="ETH",
        instrument_type="reversed",
        strike=Decimal("2300"),
        expiration_timestamp_ms=0,
        option_type="call",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.0001"),
        tick_size_steps=(),
        min_trade_amount=Decimal("0.1"),
        instrument_state="open",
    )


def test_collateral_native_pnl_below_gross_premium_diff(engine: DeribitOptionTrialBot):
    """Inverse ETH: net coin PnL is below ticket gross (sell-buy) after fees."""
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        entry_fee=Decimal("0.69"),
        short_entry_average_price=Decimal("0.0175"),
        entry_index_usd=Decimal("2300"),
        strategy="covered_call",
        option_type="call",
    )
    inst = _eth_call_instrument()
    native = engine._compute_realized_pnl_collateral_native(
        group,
        short_entry_price=Decimal("0.0175"),
        short_close_price=Decimal("0.007"),
        index_at_entry=Decimal("2300"),
        index_at_close=Decimal("2100"),
        short_instrument=inst,
    )
    assert native is not None
    gross = Decimal("0.0105")
    assert native < gross
    assert native > Decimal("0.009")


def test_apr_book_prefers_premium_native_over_usdc_division(engine: DeribitOptionTrialBot):
    group = TradeGroup(
        group_id="0047",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("26.95"),
        original_entry_credit=Decimal("26.95"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        entry_fee=Decimal("0.61"),
        short_entry_average_price=Decimal("0.0135"),
        short_close_average_price=Decimal("0.0085"),
        entry_index_usd=Decimal("2041.64"),
        close_index_usd=Decimal("1990.68"),
        entry_fee_collateral=Decimal("0.0003"),
        close_fee_collateral=Decimal("0.0003"),
        realized_pnl=Decimal("9.431502"),
        realized_pnl_collateral_native=Decimal("0.0044"),
        strategy="covered_call",
        option_type="call",
        status="closed",
    )
    apr_native = engine._realized_pnl_native_for_apr_book(
        group,
        group.realized_pnl or Decimal("0"),
        index_price_usd=Decimal("1990.68"),
    )
    assert apr_native == Decimal("0.0044")
    inflated = (group.realized_pnl or Decimal("0")) / Decimal("2041.64")
    assert inflated > Decimal("0.0046")
    assert apr_native < inflated


def test_usdc_pnl_over_current_index_inflates_eth_display(engine: DeribitOptionTrialBot):
    """Dividing USDC PnL by a lower spot than at entry overstates ETH PnL."""
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        entry_fee=Decimal("0.69"),
        short_entry_average_price=Decimal("0.0175"),
        entry_index_usd=Decimal("2300"),
        realized_pnl=Decimal("24.23"),
        strategy="covered_call",
        option_type="call",
    )
    inflated = engine._realized_pnl_native_for_apr_book(
        group,
        Decimal("24.23"),
        index_price_usd=Decimal("2100"),
    )
    native = engine._compute_realized_pnl_collateral_native(
        group,
        short_entry_price=Decimal("0.0175"),
        short_close_price=Decimal("0.007"),
        index_at_entry=Decimal("2300"),
        index_at_close=Decimal("2100"),
        short_instrument=_eth_call_instrument(),
    )
    assert inflated > Decimal("0.0105")
    assert native is not None
    assert inflated > native


def test_backfill_collateral_native_from_ledger(engine: DeribitOptionTrialBot):
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.69"),
        short_entry_average_price=Decimal("0.0175"),
        short_close_average_price=Decimal("0.007"),
        entry_index_usd=Decimal("2300"),
        close_index_usd=Decimal("2100"),
        realized_close_debit=Decimal("15.33"),
        realized_close_fee=Decimal("0.63"),
    )
    group.backfill_realized_pnl_collateral_native()
    assert group.realized_pnl_collateral_native is not None
    assert group.realized_pnl_collateral_native < Decimal("0.0105")
    group.backfill_realized_pnl_usdc(spot_index_usd=Decimal("2100"))
    assert group.realized_pnl is not None
    assert group.realized_pnl == group.realized_pnl_collateral_native * Decimal("2100")


def test_backfill_realized_pnl_usdc_adds_legacy_close_fee() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="USDC",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.69"),
        entry_index_usd=Decimal("2300"),
        realized_close_debit=Decimal("14.70"),
        realized_close_fee=Decimal("0.63"),
        realized_pnl=Decimal("24.86"),
        close_index_usd=Decimal("2100"),
    )
    assert group.economic_close_debit_usdc() == Decimal("15.33")
    group.backfill_realized_pnl_usdc()
    assert group.realized_pnl == Decimal("39.56") - Decimal("15.33")


def test_enrich_from_journal_computes_correct_eth_pnl() -> None:
    """Regression: USDC/spot conversion must not replace fill-price native PnL."""
    group = TradeGroup(
        group_id="0040",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("38.044948"),
        original_entry_credit=Decimal("38.044948"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.663477"),
        entry_index_usd=Decimal("0"),
        realized_close_debit=Decimal("14.306112"),
        realized_close_fee=Decimal("0.631152"),
        realized_pnl=Decimal("23.738836"),
    )
    journal = [
        {
            "instrument_name": "ETH-29MAY26-2300-C",
            "leg": "short",
            "event_type": "open",
            "price": "0.0175",
        },
        {
            "instrument_name": "ETH-29MAY26-2300-C",
            "leg": "short",
            "event_type": "close",
            "price": "0.0065",
        },
    ]
    group.backfill_realized_pnl_collateral_native(
        spot_index_usd=Decimal("2082"),
        journal_executions=journal,
    )
    assert group.short_entry_average_price == Decimal("0.0175")
    assert group.short_close_average_price == Decimal("0.0065")
    native = group.realized_pnl_collateral_native
    assert native is not None
    assert abs(native - Decimal("0.0104")) < Decimal("0.0005")
    assert native < Decimal("0.0114")
    assert group.realized_pnl is not None
    assert abs(group.realized_pnl - native * Decimal("2082")) < Decimal("0.05")


def test_ledger_fallback_when_journal_has_close_only() -> None:
    group = TradeGroup(
        group_id="0032",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-22MAY26-2500-C",
        short_strike=Decimal("2500"),
        entry_credit=Decimal("38"),
        original_entry_credit=Decimal("38"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.66"),
        realized_close_debit=Decimal("13.254566"),
        realized_close_fee=Decimal("0.63"),
    )
    journal = [
        {
            "instrument_name": "ETH-22MAY26-2500-C",
            "leg": "short",
            "event_type": "close",
            "price": "0.0055",
            "extra": {"source": "deribit_api"},
        },
    ]
    group.backfill_realized_pnl_collateral_native(journal_executions=journal)
    assert group.short_close_average_price == Decimal("0.0055")
    assert group.realized_pnl_collateral_native is not None
    assert group.realized_pnl_collateral_native > Decimal("0.009")


def test_fees_native_without_entry_index_uses_spot_fallback() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.69"),
        realized_close_fee=Decimal("0.63"),
        close_index_usd=Decimal("2100"),
    )
    fees = group.fees_native(index_fallback_usd=Decimal("2400"))
    assert fees is not None
    assert fees > Decimal("0")


def test_compute_realized_pnl_native_entry_amount_minus_exit_minus_fee() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("39.56"),
        original_entry_credit=Decimal("39.56"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        entry_fee=Decimal("0.69"),
        short_entry_average_price=Decimal("0.0175"),
        entry_index_usd=Decimal("2300"),
        short_close_average_price=Decimal("0.007"),
        close_index_usd=Decimal("2100"),
        realized_close_debit=Decimal("15.33"),
        realized_close_fee=Decimal("0.63"),
    )
    entry_amount = group.entry_amount_native()
    exit_amount = group.exit_amount_native()
    fees = group.fees_native()
    assert entry_amount == Decimal("0.0175")
    assert exit_amount == Decimal("0.007")
    assert fees is not None
    native = group.compute_realized_pnl_native()
    assert native == entry_amount - exit_amount - fees


def test_entry_credit_net_usdc_detects_legacy_gross_entry() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="ETH",
        collateral_currency="ETH",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="ETH-29MAY26-2300-C",
        short_strike=Decimal("2300"),
        entry_credit=Decimal("40.25"),
        original_entry_credit=Decimal("40.25"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        entry_fee=Decimal("0.69"),
        short_entry_average_price=Decimal("0.0175"),
        entry_index_usd=Decimal("2300"),
    )
    assert group.entry_credit_net_usdc() == Decimal("39.56")


def test_apply_coin_close_from_native_derives_usdc_once() -> None:
    """Coin close premium is source of truth; USDC debit is native × index, not reversed."""
    group = TradeGroup(
        group_id="0001",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.5"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC-26JUN26-80000-C",
        short_strike=Decimal("80000"),
        entry_credit=Decimal("300"),
        original_entry_credit=Decimal("300"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        short_entry_average_price=Decimal("0.009"),
        entry_index_usd=Decimal("70000"),
    )
    idx = Decimal("73270.7")
    group.apply_coin_close_from_native(short_close_premium=Decimal("0.008"), index_usd=idx)
    assert group.short_close_average_price == Decimal("0.008")
    assert group.close_fee_collateral == Decimal("0.00015")
    expected_debit = (Decimal("0.008") * Decimal("0.5") + Decimal("0.00015")) * idx
    assert group.realized_close_debit == expected_debit
    native = group.compute_realized_pnl_native()
    assert native == Decimal("0.0002")


def test_resolved_short_close_price_skips_usdc_round_trip_for_coin() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.5"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC-26JUN26-80000-C",
        short_strike=Decimal("80000"),
        entry_credit=Decimal("300"),
        original_entry_credit=Decimal("300"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        close_index_usd=Decimal("73270.7"),
        realized_close_debit=Decimal("311.46924"),
        realized_close_fee=Decimal("10.990605"),
    )
    assert group.resolved_short_close_price() == Decimal("0")
    group.short_close_average_price = Decimal("0.008")
    assert group.resolved_short_close_price() == Decimal("0.008")


def test_backfill_coin_collateral_ledger_does_not_infer_close_from_usdc_debit() -> None:
    group = TradeGroup(
        group_id="0001",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("0.5"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC-26JUN26-80000-C",
        short_strike=Decimal("80000"),
        entry_credit=Decimal("300"),
        original_entry_credit=Decimal("300"),
        max_loss=Decimal("100"),
        regime_at_entry="normal",
        status="closed",
        short_entry_average_price=Decimal("0.009"),
        entry_index_usd=Decimal("73266.17"),
        close_index_usd=Decimal("73270.7"),
        realized_close_debit=Decimal("311.46924"),
        realized_close_fee=Decimal("10.990605"),
    )
    group.backfill_coin_collateral_ledger()
    assert group.short_close_average_price is None or group.short_close_average_price <= 0
    group.short_close_average_price = Decimal("0.008")
    group.backfill_coin_collateral_ledger()
    assert group.short_close_average_price == Decimal("0.008")
    assert group.compute_realized_pnl_native() == Decimal("0.0002")
