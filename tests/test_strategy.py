from decimal import Decimal

from deribit_demo.fees import linear_usdc_short_put_apr_premium_over_strike
from deribit_demo.models import NakedPutCandidate, OptionInstrument, OrderBookSnapshot, RiskRegime
from deribit_demo.strategy import StrategySelector

from conftest import FakeClient, make_book, make_config


def test_buy_limit_price_uses_tick_size_steps_for_reversed_options(tmp_path):
    config = make_config(tmp_path)
    selector = StrategySelector(config)
    client = FakeClient()
    instrument = OptionInstrument.from_api(
        next(item for item in client.get_instruments("BTC", kind="option", expired=False) if item["instrument_name"] == "BTC-14APR30-63000-P")
    )
    payload = client.get_order_book("BTC-14APR30-63000-P")
    payload["best_ask_price"] = "0.006"
    payload["best_bid_price"] = "0.0055"
    book = OrderBookSnapshot.from_api(payload)

    price = selector.buy_limit_price(instrument, book)

    assert price > Decimal("0.005")


def test_close_sell_price_never_floors_one_tick_bid_to_zero(tmp_path):
    config = make_config(tmp_path)
    selector = StrategySelector(config)
    client = FakeClient()
    instrument = OptionInstrument.from_api(
        next(item for item in client.get_instruments("USDC", kind="option", expired=False) if item["instrument_name"] == "ETH_USDC-14APR30-3000-P")
    )
    payload = client.get_order_book("ETH_USDC-14APR30-3000-P")
    payload["best_bid_price"] = "0.5"
    payload["best_ask_price"] = "1.0"
    book = OrderBookSnapshot.from_api(payload)

    price = selector.close_sell_price(instrument, book)

    assert price == Decimal("0.5")


def test_linear_usdc_apr_matches_premium_over_strike_times_365_over_dte():
    """APR = (權利金 / strike / DTE) * 365 for USDC linear."""
    premium = Decimal("10.2")
    strike = Decimal("1850")
    dte = Decimal("13.05932285")
    apr = linear_usdc_short_put_apr_premium_over_strike(
        premium_per_contract=premium,
        strike=strike,
        dte_days=dte,
    )
    expected = (premium / strike / dte) * Decimal("365")
    assert apr == expected
    assert apr > Decimal("0.15")
    assert apr < Decimal("0.18")


def test_build_naked_short_put_candidates_inverse_btc(tmp_path):
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
    )
    selector = StrategySelector(config)
    client = FakeClient(btc_book_equity="1.0")
    instruments = [OptionInstrument.from_api(item) for item in client.get_instruments("BTC", kind="option", expired=False)]

    def loader(name):
        return OrderBookSnapshot.from_api(client.get_order_book(name))

    candidates = selector.build_naked_short_put_candidates(
        instruments,
        loader,
        regime=RiskRegime.NORMAL,
        summary_equity=Decimal("1"),
        summary_maintenance_margin=Decimal("0.01"),
        collateral_currency="BTC",
        currency="BTC",
        existing_im_by_expiry={},
    )
    assert candidates
    assert isinstance(candidates[0], NakedPutCandidate)
    assert candidates[0].short_leg.instrument_name.endswith("-P")
    assert candidates[0].net_apr > 0


def test_build_bull_put_spread_candidates_adds_long_put(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="bull_put_spread",
        min_net_apr=Decimal("0.005"),
        entry_dte_min=7,
        entry_dte_max=24,
        bull_put_long_delta_min=Decimal("0.04"),
        bull_put_long_delta_max=Decimal("0.06"),
    )
    selector = StrategySelector(config)
    client = FakeClient(btc_book_equity="1.0")
    instruments = [OptionInstrument.from_api(item) for item in client.get_instruments("BTC", kind="option", expired=False)]

    def loader(name):
        return OrderBookSnapshot.from_api(client.get_order_book(name))

    candidates = selector.build_bull_put_spread_candidates(
        instruments,
        loader,
        regime=RiskRegime.NORMAL,
        summary_equity=Decimal("1"),
        summary_maintenance_margin=Decimal("0.01"),
        collateral_currency="BTC",
        currency="BTC",
        existing_im_by_expiry={},
    )

    assert candidates
    candidate = candidates[0]
    assert candidate.strategy == "bull_put_spread"
    assert candidate.long_leg is not None
    assert candidate.long_leg.instrument_name.endswith("-P")
    assert candidate.long_leg.strike < candidate.short_leg.strike
    assert candidate.net_premium_native > 0
    assert candidate.estimated_im_total > 0


def _make_btc_call_payload(days: int, strike: int, *, delta: str = "0.11") -> tuple[dict, dict]:
    """Build synthetic instrument + orderbook payloads for a BTC inverse short call test."""
    from conftest import future_expiry

    expiry = future_expiry(days)
    instrument = {
        "instrument_name": f"BTC-{days:02d}APR30-{strike}-C",
        "base_currency": "BTC",
        "quote_currency": "BTC",
        "settlement_currency": "BTC",
        "instrument_type": "reversed",
        "tick_size": "0.0001",
        "tick_size_steps": [{"above_price": "0.005", "tick_size": "0.0005"}],
        "min_trade_amount": "0.1",
        "contract_size": "0.1",
        "option_type": "call",
        "expiration_timestamp": expiry,
        "strike": str(strike),
        "instrument_state": "open",
    }
    book = {
        "instrument_name": f"BTC-{days:02d}APR30-{strike}-C",
        "best_bid_price": "0.0032",
        "best_bid_amount": "0.3",
        "best_ask_price": "0.0034",
        "best_ask_amount": "0.3",
        "mark_price": "0.0033",
        "index_price": "70000",
        "mark_iv": "0.55",
        "open_interest": "60",
        "greeks": {"delta": delta},
    }
    return instrument, book


def test_build_naked_short_call_candidates_inverse_btc(tmp_path):
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_call=True,
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000)
    instrument = OptionInstrument.from_api(inst_payload)

    def loader(name):
        assert name == instrument.instrument_name
        return OrderBookSnapshot.from_api(book_payload)

    candidates = selector.build_naked_short_call_candidates(
        [instrument],
        loader,
        regime=RiskRegime.NORMAL,
        summary_equity=Decimal("1"),
        summary_maintenance_margin=Decimal("0.01"),
        collateral_currency="BTC",
        currency="BTC",
        existing_im_by_expiry={},
    )
    assert candidates
    assert isinstance(candidates[0], NakedPutCandidate)
    assert candidates[0].short_leg.instrument_name.endswith("-C")
    assert candidates[0].option_type == "call"
    assert candidates[0].net_apr > 0


def test_build_covered_call_candidates_requires_existing_cover(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000)
    instrument = OptionInstrument.from_api(inst_payload)

    def loader(name):
        assert name == instrument.instrument_name
        return OrderBookSnapshot.from_api(book_payload)

    assert selector.build_covered_call_candidates(
        [instrument],
        loader,
        regime=RiskRegime.NORMAL,
        collateral_currency="BTC",
        currency="BTC",
        available_cover_quantity=Decimal("0"),
        summary_equity=Decimal("1"),
    ) == []

    candidates = selector.build_covered_call_candidates(
        [instrument],
        loader,
        regime=RiskRegime.NORMAL,
        collateral_currency="BTC",
        currency="BTC",
        available_cover_quantity=Decimal("0.2"),
        summary_equity=Decimal("1"),
    )
    assert candidates
    assert candidates[0].strategy == "covered_call"
    assert candidates[0].covered_underlying_quantity == candidates[0].quantity


def test_covered_call_scan_rejection_detail_summarizes_reasons(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        btc_call_delta_min=Decimal("0.30"),
        btc_call_delta_max=Decimal("0.50"),
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    instrument = OptionInstrument.from_api(inst_payload)

    def loader(name):
        assert name == instrument.instrument_name
        return OrderBookSnapshot.from_api(book_payload)

    detail = selector.covered_call_scan_rejection_detail(
        "BTC",
        [instrument],
        loader,
        regime=RiskRegime.NORMAL,
        collateral_currency="BTC",
        available_cover_quantity=Decimal("0.2"),
        summary_equity=Decimal("1"),
    )

    assert detail["calls_in_dte_window"] == 1
    assert detail["liquidity_rejections"] == {"delta_out_of_range": 1}
    assert detail["after_liquidity_rejections"] == {}
    assert detail["instruments_passing_all_build_gates"] == 0
    assert detail["instrument_names_passing_all_build_gates"] == []


def test_short_call_rejected_when_delta_out_of_range(tmp_path):
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_call=True,
        btc_call_delta_min=Decimal("0.30"),
        btc_call_delta_max=Decimal("0.50"),
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    instrument = OptionInstrument.from_api(inst_payload)
    book = OrderBookSnapshot.from_api(book_payload)

    reason = selector._naked_short_call_rejection_reason("BTC", instrument, book)
    assert reason == "delta_out_of_range"


def test_short_call_rejected_when_otm_out_of_range(tmp_path):
    config = make_config(
        tmp_path,
        enable_short_call=True,
        btc_call_otm_min=Decimal("0.20"),
        btc_call_otm_max=Decimal("0.40"),
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    instrument = OptionInstrument.from_api(inst_payload)
    book = OrderBookSnapshot.from_api(book_payload)
    reason = selector._naked_short_call_rejection_reason("BTC", instrument, book)
    assert reason == "otm_out_of_range"


# ---------------------------------------------------------------------------
# Three-book redesign regressions
# ---------------------------------------------------------------------------


def test_per_leg_im_cap_call_tighter_than_put(tmp_path):
    """Call legs should carry a strictly tighter per-leg IM cap than puts.

    The three-book redesign treats call legs (unbounded upside) as more
    dangerous than put legs and wires that through via
    ``per_leg_im_cap_call`` < ``per_leg_im_cap_put``. Regress that ordering
    here so a refactor that collapses the two knobs will flip this test.
    """
    config = make_config(
        tmp_path,
        per_leg_im_cap_put=Decimal("0.15"),
        per_leg_im_cap_call=Decimal("0.10"),
    )
    assert config.per_leg_im_cap("BTC", option_type="put") == Decimal("0.15")
    assert config.per_leg_im_cap("BTC", option_type="call") == Decimal("0.10")
    assert config.per_leg_im_cap("BTC", option_type="call") < config.per_leg_im_cap(
        "BTC", option_type="put"
    )


def test_liquidity_gates_split_between_inverse_and_linear(tmp_path):
    """Liquidity floor is looser on USDC linear (thinner book) than on inverse."""
    config = make_config(
        tmp_path,
        inverse_min_open_interest=Decimal("20"),
        inverse_max_spread_ratio=Decimal("0.12"),
        inverse_min_book_notional_usdc=Decimal("3000"),
        linear_min_open_interest=Decimal("8"),
        linear_max_spread_ratio=Decimal("0.14"),
        linear_min_book_notional_usdc=Decimal("4000"),
    )
    inv_oi, inv_spread, inv_notional = config.liquidity_gates("reversed")
    lin_oi, lin_spread, lin_notional = config.liquidity_gates("linear")
    assert (inv_oi, inv_spread, inv_notional) == (
        Decimal("20"),
        Decimal("0.12"),
        Decimal("3000"),
    )
    assert (lin_oi, lin_spread, lin_notional) == (
        Decimal("8"),
        Decimal("0.14"),
        Decimal("4000"),
    )


def test_liquidity_gates_use_currency_specific_open_interest(tmp_path):
    config = make_config(
        tmp_path,
        btc_inverse_min_open_interest=Decimal("30"),
        eth_inverse_min_open_interest=Decimal("12"),
        btc_linear_min_open_interest=Decimal("10"),
        eth_linear_min_open_interest=Decimal("6"),
        inverse_max_spread_ratio=Decimal("0.12"),
        linear_max_spread_ratio=Decimal("0.14"),
    )

    assert config.liquidity_gates("reversed", "BTC")[:2] == (
        Decimal("30"),
        Decimal("0.12"),
    )
    assert config.liquidity_gates("reversed", "ETH")[:2] == (
        Decimal("12"),
        Decimal("0.12"),
    )
    assert config.liquidity_gates("linear", "BTC")[:2] == (
        Decimal("10"),
        Decimal("0.14"),
    )
    assert config.liquidity_gates("linear", "ETH")[:2] == (
        Decimal("6"),
        Decimal("0.14"),
    )


def test_scan_for_book_returns_puts_when_available(tmp_path, btc_book):
    """Primary path: put candidates exist → return puts, not calls."""
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_put=True,
        enable_short_call=True,
        short_call_fallback_only=True,
    )
    selector = StrategySelector(config)
    client = FakeClient(btc_book_equity="1.0")
    instruments = [
        OptionInstrument.from_api(item)
        for item in client.get_instruments("BTC", kind="option", expired=False)
    ]

    def loader(name):
        return OrderBookSnapshot.from_api(client.get_order_book(name))

    candidates, option_type = selector.scan_for_book(
        btc_book,
        markets_by_currency={"BTC": instruments},
        orderbook_loader=loader,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        existing_im_by_expiry_by_currency={"BTC": {}},
    )
    assert candidates, "expected at least one put candidate from BTC inverse book"
    assert option_type == "put"
    assert candidates[0].option_type == "put"


def test_scan_for_book_falls_back_to_calls_when_no_puts(tmp_path, btc_book):
    """Fallback path: ``short_call_fallback_only=True`` and no puts → scan calls."""
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_put=True,
        enable_short_call=True,
        short_call_fallback_only=True,
        btc_put_delta_min=Decimal("0.40"),
        btc_put_delta_max=Decimal("0.50"),
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    call_instrument = OptionInstrument.from_api(inst_payload)

    def loader(name):
        if name == call_instrument.instrument_name:
            return OrderBookSnapshot.from_api(book_payload)
        raise KeyError(name)

    candidates, option_type = selector.scan_for_book(
        btc_book,
        markets_by_currency={"BTC": [call_instrument]},
        orderbook_loader=loader,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        existing_im_by_expiry_by_currency={"BTC": {}},
    )
    assert option_type == "call"
    assert candidates, "expected call fallback candidates when puts are filtered out"
    assert candidates[0].option_type == "call"


def test_scan_for_book_can_return_puts_and_calls_together(tmp_path, btc_book):
    """Both mode: put and call candidates compete in the same book scan."""
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_put=True,
        enable_short_call=True,
        short_call_fallback_only=False,
    )
    selector = StrategySelector(config)
    client = FakeClient(btc_book_equity="1.0")
    instruments = [
        OptionInstrument.from_api(item)
        for item in client.get_instruments("BTC", kind="option", expired=False)
    ]
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    call_instrument = OptionInstrument.from_api(inst_payload)
    instruments.append(call_instrument)

    def loader(name):
        if name == call_instrument.instrument_name:
            return OrderBookSnapshot.from_api(book_payload)
        return OrderBookSnapshot.from_api(client.get_order_book(name))

    candidates, option_type = selector.scan_for_book(
        btc_book,
        markets_by_currency={"BTC": instruments},
        orderbook_loader=loader,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        existing_im_by_expiry_by_currency={"BTC": {}},
    )
    sides = {candidate.option_type for candidate in candidates}
    assert option_type == "both"
    assert {"put", "call"} <= sides


def test_take_top_scan_candidates_global_sort_for_naked_short_both(tmp_path):
    """``naked_short`` with ``SHORT_OPTION_SIDE=both`` ranks puts and calls together.

    The take-top helper applies ``naked_put_sort_key`` to the merged list and
    no longer reserves slots for calls; whichever side scores better wins
    those slots. The combined output stays in pure sort order.
    """
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_put=True,
        enable_short_call=True,
        short_call_fallback_only=False,
    )
    selector = StrategySelector(config)
    client = FakeClient(btc_book_equity="1.0")
    instruments = [
        OptionInstrument.from_api(item)
        for item in client.get_instruments("BTC", kind="option", expired=False)
    ]
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    instruments.append(OptionInstrument.from_api(inst_payload))

    def loader(name):
        if name == inst_payload["instrument_name"]:
            return OrderBookSnapshot.from_api(book_payload)
        return OrderBookSnapshot.from_api(client.get_order_book(name))

    puts = selector.build_naked_short_put_candidates(
        instruments,
        loader,
        regime=RiskRegime.NORMAL,
        summary_equity=Decimal("1"),
        summary_maintenance_margin=Decimal("0.01"),
        collateral_currency="BTC",
        currency="BTC",
        existing_im_by_expiry={},
    )
    calls = selector.build_naked_short_call_candidates(
        instruments,
        loader,
        regime=RiskRegime.NORMAL,
        summary_equity=Decimal("1"),
        summary_maintenance_margin=Decimal("0.01"),
        collateral_currency="BTC",
        currency="BTC",
        existing_im_by_expiry={},
    )
    assert puts and calls
    merged = puts + calls
    top = selector.take_top_scan_candidates(merged, limit=5)
    assert len(top) <= 5
    expected = sorted(merged, key=selector.naked_put_sort_key)[:5]
    assert [c.short_leg.instrument_name for c in top] == [
        c.short_leg.instrument_name for c in expected
    ]


def test_scan_for_book_empty_when_calls_disabled(tmp_path, btc_book):
    """When short call is disabled and puts are empty, fallback must stay empty."""
    config = make_config(
        tmp_path,
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        enable_short_put=True,
        enable_short_call=False,
        short_call_fallback_only=True,
        btc_put_delta_min=Decimal("0.40"),
        btc_put_delta_max=Decimal("0.50"),
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.11")
    call_instrument = OptionInstrument.from_api(inst_payload)

    def loader(name):
        return OrderBookSnapshot.from_api(book_payload)

    candidates, option_type = selector.scan_for_book(
        btc_book,
        markets_by_currency={"BTC": [call_instrument]},
        orderbook_loader=loader,
        regime_by_currency={"BTC": RiskRegime.NORMAL},
        existing_im_by_expiry_by_currency={"BTC": {}},
    )
    assert candidates == []
    assert option_type == "put"  # default when no fallback taken
