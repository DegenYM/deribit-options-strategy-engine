from decimal import Decimal

from conftest import FakeClient, future_expiry, make_config

from deribit_engine.cash_secured_ops import (
    cash_secured_cover_unrestored,
    cash_secured_put_is_itm,
    cash_secured_quantity,
    cash_secured_scan_rank,
    cash_secured_strike_bounds,
    cash_secured_target_native,
    itm_sold_ready_for_cash_secured,
    list_cash_secured_preview_parents,
    resolve_cash_secured_preview_parent,
)
from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.models import OptionInstrument, OrderBookSnapshot, StrategyState, TradeGroup
from deribit_engine.spot_exit_ops import apply_spot_exit_quote_proceeds, spot_exit_quote_currency
from deribit_engine.strategy import StrategySelector
from deribit_engine.utils import utc_now_ms


def _itm_sold_group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0095",
        "currency": "BTC",
        "short_instrument_name": "BTC-28AUG26-63000-C",
        "short_label": "cc-btc-0095",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "0.1",
        "covered_underlying_quantity": "0.1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 2,
        "closed_timestamp_ms": 1_746_000_000_000,
        "short_strike": "63000",
        "entry_credit": "30",
        "original_entry_credit": "30",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "spot_exit_status": "filled",
        "spot_exit_amount": "0.1",
        "spot_exit_instrument_name": "BTC_USDC",
        "spot_exit_quote_proceeds": "9000",
        "spot_exit_quote_proceeds_lifetime": "9000",
        "spot_exit_reason": "covered_call_settlement_exit",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_spot_exit_quote_currency_from_instrument() -> None:
    usdc = _itm_sold_group()
    usdt = _itm_sold_group(spot_exit_instrument_name="BTC_USDT")
    assert spot_exit_quote_currency(usdc) == "USDC"
    assert spot_exit_quote_currency(usdt) == "USDT"


def test_apply_spot_exit_quote_proceeds_accepts_usdc() -> None:
    group = _itm_sold_group(spot_exit_quote_proceeds="0", spot_exit_quote_proceeds_lifetime="0")
    trades = [
        {
            "direction": "sell",
            "instrument_name": "BTC_USDC",
            "amount": "0.1",
            "price": "90000",
            "fee": "1",
            "fee_currency": "USDC",
        }
    ]
    proceeds = apply_spot_exit_quote_proceeds(group, trades)
    assert proceeds == Decimal("8999")
    assert group.spot_exit_quote_proceeds == Decimal("8999")


def test_itm_sold_ready_accepts_usdt_journal_as_usdc() -> None:
    ok, reason = itm_sold_ready_for_cash_secured(_itm_sold_group())
    assert ok is True
    assert reason == ""

    converted, converted_why = itm_sold_ready_for_cash_secured(_itm_sold_group(spot_exit_instrument_name="BTC_USDT"))
    assert converted is True
    assert converted_why == ""

    pending, pending_why = itm_sold_ready_for_cash_secured(_itm_sold_group(spot_exit_status="pending"))
    assert pending is False
    assert pending_why == "spot_exit_not_filled"

    blocked, why = itm_sold_ready_for_cash_secured(
        _itm_sold_group(
            spot_exit_instrument_name="BTC_USDT",
            spot_restore_status="submitted",
            spot_restore_order_id="parked-1",
        )
    )
    assert blocked is False
    assert why == "restore_in_flight"

    cancelled, cancelled_why = itm_sold_ready_for_cash_secured(
        _itm_sold_group(
            spot_exit_instrument_name="BTC_USDT",
            spot_restore_status="skipped",
            spot_restore_reason="operator_cancelled",
        )
    )
    assert cancelled is True
    assert cancelled_why == ""

    parked, parked_why = itm_sold_ready_for_cash_secured(
        _itm_sold_group(cash_secured_status="submitted", cash_secured_order_id="csp-1")
    )
    assert parked is False
    assert parked_why == "already_submitted"

    retry, retry_why = itm_sold_ready_for_cash_secured(
        _itm_sold_group(
            cash_secured_status="skipped",
            cash_secured_reason="operator_cancelled",
        )
    )
    assert retry is True
    assert retry_why == ""

    still_skipped, still_why = itm_sold_ready_for_cash_secured(
        _itm_sold_group(cash_secured_status="skipped", cash_secured_reason="no_short_dated_put")
    )
    assert still_skipped is False
    assert still_why == "already_skipped"


def test_cash_secured_strike_and_quantity() -> None:
    low, high = cash_secured_strike_bounds(Decimal("63000"), Decimal("0.02"))
    assert high == Decimal("63000")
    assert low == Decimal("61740")

    qty = cash_secured_quantity(
        sold_native=Decimal("0.1"),
        usdc_available=Decimal("9000"),
        strike=Decimal("63000"),
        contract_size=Decimal("0.01"),
        min_trade_amount=Decimal("0.01"),
    )
    assert qty == Decimal("0.1")

    tiny = cash_secured_quantity(
        sold_native=Decimal("0.1"),
        usdc_available=Decimal("500"),
        strike=Decimal("63000"),
        contract_size=Decimal("0.01"),
        min_trade_amount=Decimal("0.01"),
    )
    assert tiny == Decimal("0")

    short_at_original = cash_secured_quantity(
        sold_native=Decimal("0.0998"),
        usdc_available=Decimal("7294"),
        strike=Decimal("73000"),
        contract_size=Decimal("0.01"),
        min_trade_amount=Decimal("0.01"),
        cap=Decimal("0.1"),
    )
    assert short_at_original == Decimal("0.09")

    lower_strike = cash_secured_quantity(
        sold_native=Decimal("0.0998"),
        usdc_available=Decimal("7294"),
        strike=Decimal("72000"),
        contract_size=Decimal("0.01"),
        min_trade_amount=Decimal("0.01"),
        cap=Decimal("0.1"),
    )
    assert lower_strike == Decimal("0.1")
    assert cash_secured_scan_rank(
        quantity=lower_strike,
        strike=Decimal("72000"),
        dte=Decimal("6"),
        net_apr=Decimal("0.4"),
    ) < cash_secured_scan_rank(
        quantity=short_at_original,
        strike=Decimal("73000"),
        dte=Decimal("6"),
        net_apr=Decimal("0.5"),
    )


def test_cash_secured_target_ceils_unrestored_cover() -> None:
    group = _itm_sold_group(
        spot_exit_amount="0.0915",
        spot_exit_settlement_loss="0.0084211",
        covered_underlying_quantity="0.1",
        quantity="0.1",
    )
    target = cash_secured_target_native(group)
    assert target > Decimal("0.09")
    assert target <= Decimal("0.1")


def test_covered_call_itm_to_cash_secured_config(tmp_path) -> None:
    from deribit_engine.config import load_config

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=covered_call",
                "TRADED_COLLATERALS=BTC,ETH",
                "COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED=true",
                "COVERED_CALL_CSP_DTE_MIN=3",
                "COVERED_CALL_CSP_DTE_MAX=8",
                "COVERED_CALL_CSP_STRIKE_FLOOR_PCT=0.03",
                "COVERED_CALL_CSP_MIN_OPEN_INTEREST=6",
                "COVERED_CALL_CSP_MAX_SPREAD_RATIO=0.18",
                "COVERED_CALL_CSP_MIN_BOOK_NOTIONAL_USDC=3000",
            ]
        )
    )
    config = load_config(env_file, require_private=False)
    assert config.covered_call_itm_to_cash_secured_enabled is True
    assert config.covered_call_csp_dte_min == 3
    assert config.covered_call_csp_dte_max == 8
    assert config.covered_call_csp_strike_floor_pct == Decimal("0.03")
    assert config.cash_secured_liquidity_gates() == (
        Decimal("6"),
        Decimal("0.18"),
        Decimal("3000"),
    )
    assert config.liquidity_gates("linear")[1] == Decimal("0.14")
    assert "USDC" in config.traded_collaterals


def test_cash_secured_liquidity_defaults_are_modest(tmp_path) -> None:
    from deribit_engine.config import load_config

    env_file = tmp_path / ".env"
    env_file.write_text("OPTION_STRATEGY=covered_call\n")
    config = load_config(env_file, require_private=False)
    assert config.covered_call_csp_strike_floor_pct == Decimal("0.05")
    assert config.cash_secured_liquidity_gates() == (
        Decimal("6"),
        Decimal("0.18"),
        Decimal("3000"),
    )
    assert config.liquidity_gates("linear") == (
        Decimal("8"),
        Decimal("0.14"),
        Decimal("4000"),
    )


def test_spot_instrument_switches_to_usdc_when_csp_enabled(tmp_path) -> None:
    client = FakeClient()
    usdt_engine = DeribitOptionTrialBot(
        make_config(tmp_path, option_strategy="covered_call", covered_call_itm_to_cash_secured_enabled=False),
        client,
    )
    csp_engine = DeribitOptionTrialBot(
        make_config(tmp_path, option_strategy="covered_call", covered_call_itm_to_cash_secured_enabled=True),
        client,
    )
    assert usdt_engine._covered_call_spot_instrument("BTC") == "BTC_USDT"
    assert csp_engine._covered_call_spot_instrument("BTC") == "BTC_USDC"
    assert csp_engine._covered_call_profit_sweep_instrument("BTC") == "BTC_USDT"


def test_manage_skips_auto_restore_for_usdc_itm_when_csp_enabled(tmp_path) -> None:
    client = FakeClient()
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_itm_to_cash_secured_enabled=True,
        covered_call_csp_dte_min=2,
        covered_call_csp_dte_max=21,
        linear_min_book_notional_usdc=Decimal("1000"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.manage(live=False)
    assert not any(
        str(action.get("action") or "").startswith("spot_restore")
        or str(action.get("action") or "").startswith("auto_spot_restore")
        for action in result["actions"]
    )


def test_manage_treats_usdt_itm_as_usdc_for_csp(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_auto_spot_restore_enabled=True,
        covered_call_auto_spot_restore_min_edge_pct=Decimal("0.001"),
        covered_call_itm_to_cash_secured_enabled=True,
        covered_call_csp_dte_min=2,
        covered_call_csp_dte_max=21,
        linear_min_book_notional_usdc=Decimal("1000"),
        linear_min_open_interest=Decimal("1"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            spot_exit_instrument_name="BTC_USDT",
            spot_restore_status="skipped",
            spot_restore_reason="operator_cancelled",
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=False)
    assert not any(
        str(action.get("action") or "").startswith("spot_restore")
        or str(action.get("action") or "").startswith("auto_spot_restore")
        for action in result["actions"]
    )
    previews = [a for a in result["actions"] if a.get("action") == "cash_secured_preview"]
    assert len(previews) == 1
    assert previews[0]["source_group_id"] == "0095"


def test_manage_previews_cash_secured_after_usdc_itm_sale(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_itm_to_cash_secured_enabled=True,
        covered_call_csp_dte_min=2,
        covered_call_csp_dte_max=21,
        linear_min_book_notional_usdc=Decimal("1000"),
        linear_min_open_interest=Decimal("1"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.manage(live=False)
    previews = [a for a in result["actions"] if a.get("action") == "cash_secured_preview"]
    assert len(previews) == 1
    assert previews[0]["source_group_id"] == "0095"
    assert previews[0]["take_bid"] is True
    assert previews[0]["park_mid"] is False
    assert previews[0]["time_in_force"] == "immediate_or_cancel"
    assert Decimal(str(previews[0]["limit_price"])) > 0
    candidate = previews[0]["candidate"]
    assert candidate["strategy"] == "cash_secured"
    assert candidate["option_type"] == "put"
    assert candidate["collateral_currency"] == "USDC"
    assert Decimal(str(candidate["short_strike"])) <= Decimal("63000")
    assert Decimal(str(candidate["short_strike"])) >= Decimal("61740")


def _csp_scan_engine(tmp_path, client=None):
    engine = DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            covered_call_spot_exit_enabled=True,
            covered_call_itm_to_cash_secured_enabled=True,
            covered_call_csp_dte_min=2,
            covered_call_csp_dte_max=21,
            linear_min_book_notional_usdc=Decimal("1000"),
            linear_min_open_interest=Decimal("1"),
        ),
        client or FakeClient(btc_book_equity="0.2"),
    )
    return engine


def test_resolve_cash_secured_preview_parent_from_child() -> None:
    parent = _itm_sold_group(cash_secured_status="entered", cash_secured_group_id="0097")
    child = TradeGroup.from_dict(
        {
            "group_id": "0097",
            "currency": "BTC",
            "status": "open",
            "strategy": "cash_secured",
            "option_type": "put",
            "collateral_currency": "USDC",
            "quantity": "0.1",
            "short_instrument_name": "BTC_USDC-4SEP26-73000-P",
            "short_strike": "73000",
            "cash_secured_from_group_id": "0095",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "entry_credit": "5",
            "max_loss": "7300",
            "regime_at_entry": "normal",
        }
    )
    found, note = resolve_cash_secured_preview_parent([parent, child], "0097")
    assert found is parent
    assert note == "from_child"
    ready, ready_note = resolve_cash_secured_preview_parent([_itm_sold_group()])
    assert ready.group_id == "0095"
    assert ready_note == "ready"
    linked, linked_note = resolve_cash_secured_preview_parent([parent, child])
    assert linked is parent
    assert linked_note == "already_entered"
    missing, missing_note = resolve_cash_secured_preview_parent([parent], "0088")
    assert missing is None
    assert missing_note == "group_not_found"
    open_cc = _itm_sold_group(
        group_id="0096",
        status="open",
        spot_exit_status="",
        spot_exit_amount="0",
        short_strike="75000",
        short_instrument_name="BTC-4SEP26-75000-C",
        closed_timestamp_ms=0,
    )
    hypo, hypo_note = resolve_cash_secured_preview_parent([open_cc])
    assert hypo is open_cc
    assert hypo_note == "open_covered_call"


def test_scan_cash_secured_previews_ranked_puts(tmp_path) -> None:
    engine = _csp_scan_engine(tmp_path)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.scan_cash_secured(from_group_id="0095", top_n=8)
    assert result["action"] == "cash_secured_scan"
    assert result["live"] is False
    assert result["would_place"] is True
    assert result["source_group_id"] == "0095"
    assert result["pick"]["take_bid"] is True
    assert result["pick"]["park_mid"] is False
    assert result["pick"]["time_in_force"] == "immediate_or_cancel"
    assert result["pick"]["pick"] is True
    assert result["candidates"]
    assert result["candidates"][0]["instrument_name"] == result["pick"]["instrument_name"]
    assert Decimal(str(result["pick"]["short_strike"])) <= Decimal("63000")
    assert Decimal(str(result["pick"]["short_strike"])) >= Decimal("61740")


def test_scan_cash_secured_still_ranks_after_entered(tmp_path) -> None:
    engine = _csp_scan_engine(tmp_path)
    parent = _itm_sold_group(cash_secured_status="entered", cash_secured_group_id="0097")
    child = TradeGroup.from_dict(
        {
            "group_id": "0097",
            "currency": "BTC",
            "status": "open",
            "strategy": "cash_secured",
            "option_type": "put",
            "collateral_currency": "USDC",
            "quantity": "0.1",
            "short_instrument_name": "BTC_USDC-14APR30-63000-P",
            "short_strike": "63000",
            "cash_secured_from_group_id": "0095",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "entry_credit": "5",
            "max_loss": "6300",
            "regime_at_entry": "normal",
        }
    )
    state = StrategyState()
    state.groups.extend([parent, child])
    engine.state_store.save(state)

    result = engine.scan_cash_secured(from_group_id="0097")
    assert result["would_place"] is False
    assert result["ready"] is False
    assert result["ready_reason"] == "already_entered"
    assert result["source_group_id"] == "0095"
    assert result["parent_note"] == "from_child"
    assert result["pick"]["instrument_name"]


def test_scan_cash_secured_previews_open_covered_call_without_csp(tmp_path) -> None:
    engine = _csp_scan_engine(tmp_path)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            group_id="0096",
            status="open",
            spot_exit_status="",
            spot_exit_amount="0",
            spot_exit_quote_proceeds="0",
            spot_exit_quote_proceeds_lifetime="0",
            short_strike="63000",
            short_instrument_name="BTC-14APR30-63000-C",
            closed_timestamp_ms=0,
        )
    )
    engine.state_store.save(state)

    result = engine.scan_cash_secured(from_group_id="0096")
    assert result["live"] is False
    assert result["would_place"] is False
    assert result["ready"] is False
    assert result["ready_reason"] in {"not_closed", "spot_exit_not_filled"}
    assert result["parent_note"] == "from_group"
    assert result["source_group_id"] == "0096"
    assert result["hypothetical_usdc"] is True
    assert result["pick"]["take_bid"] is True
    assert result["pick"]["time_in_force"] == "immediate_or_cancel"
    assert Decimal(str(result["pick"]["short_strike"])) <= Decimal("63000")


def test_scan_cash_secured_previews_when_flag_disabled(tmp_path) -> None:
    engine = DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            covered_call_spot_exit_enabled=True,
            covered_call_itm_to_cash_secured_enabled=False,
            covered_call_csp_dte_min=2,
            covered_call_csp_dte_max=21,
            linear_min_book_notional_usdc=Decimal("1000"),
            linear_min_open_interest=Decimal("1"),
        ),
        FakeClient(btc_book_equity="0.2"),
    )
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    result = engine.scan_cash_secured(from_group_id="0095")
    assert result["csp_enabled"] is False
    assert result["would_place"] is False
    assert result["live"] is False
    assert result["reason"] != "csp_disabled"
    assert result["pick"]["instrument_name"]
    assert result["pick"]["take_bid"] is True


def _eth_itm_sold_group(group_id: str, **overrides) -> TradeGroup:
    payload = {
        "group_id": group_id,
        "currency": "ETH",
        "short_instrument_name": "ETH-28AUG26-3150-C",
        "short_strike": "3150",
        "quantity": "1",
        "covered_underlying_quantity": "1",
        "spot_exit_amount": "1",
        "spot_exit_quote_proceeds": "3000",
        "spot_exit_quote_proceeds_lifetime": "3000",
        "collateral_currency": "ETH",
    }
    payload.update(overrides)
    return _itm_sold_group(**payload)


def test_scan_cash_secured_lists_every_itm_sold_parent(tmp_path) -> None:
    engine = _csp_scan_engine(tmp_path)
    state = StrategyState()
    state.groups.extend(
        [
            _itm_sold_group(),
            _eth_itm_sold_group("0101"),
            _eth_itm_sold_group("0102"),
        ]
    )
    engine.state_store.save(state)

    listed = list_cash_secured_preview_parents(state.groups)
    assert [row[0].group_id for row in listed] == ["0095", "0101", "0102"]

    result = engine.scan_cash_secured()
    assert result["group_count"] == 3
    ids = [row["source_group_id"] for row in result["groups"]]
    assert ids == ["0095", "0101", "0102"]
    assert [row["currency"] for row in result["groups"]] == ["BTC", "ETH", "ETH"]
    assert all(row.get("pick") for row in result["groups"])


def test_scan_cash_secured_strike_floor_pct_widens_window(tmp_path) -> None:
    engine = _csp_scan_engine(tmp_path)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    tight = engine.scan_cash_secured(from_group_id="0095", top_n=8, strike_floor_pct=Decimal("0.02"))
    wide = engine.scan_cash_secured(from_group_id="0095", top_n=8, strike_floor_pct=Decimal("0.05"))
    tight_names = {row["instrument_name"] for row in tight["candidates"]}
    wide_names = {row["instrument_name"] for row in wide["candidates"]}
    assert Decimal(str(tight["strike_min"])) == Decimal("61740")
    assert Decimal(str(wide["strike_min"])) == Decimal("59850")
    assert any("60000" in name for name in wide_names)
    assert not any("60000" in name for name in tight_names)


class _ShortUsdcClient(FakeClient):
    """USDT/fee shortfall: enough for 0.1 at 62000, not at 63000."""

    def get_account_summaries(self, *, extended=False):
        rows = super().get_account_summaries(extended=extended)
        for row in rows:
            if row["currency"] == "USDC":
                row["balance"] = "6280"
                row["equity"] = "6280"
                row["available_funds"] = "6280"
                row["available_withdrawal_funds"] = "6280"
        return rows

    def get_instruments(self, currency, *, kind="option", expired=False):
        rows = super().get_instruments(currency, kind=kind, expired=expired)
        if currency != "USDC" or kind != "option":
            return rows
        extra = []
        for days in (14, 21):
            extra.append(
                {
                    "instrument_name": f"BTC_USDC-{days:02d}APR30-62000-P",
                    "base_currency": "BTC",
                    "quote_currency": "USDC",
                    "settlement_currency": "USDC",
                    "instrument_type": "linear",
                    "tick_size": "2.5",
                    "tick_size_steps": [],
                    "min_trade_amount": "0.01",
                    "contract_size": "0.01",
                    "option_type": "put",
                    "expiration_timestamp": future_expiry(days),
                    "strike": "62000",
                    "instrument_state": "open",
                }
            )
        return rows + extra

    def get_order_book(self, instrument_name, *, depth=1):
        if "62000" in instrument_name:
            return {
                "instrument_name": instrument_name,
                "best_bid_price": "600",
                "best_bid_amount": "0.05",
                "best_ask_price": "620",
                "best_ask_amount": "0.05",
                "mark_price": "610",
                "index_price": "70000",
                "mark_iv": "0.55",
                "open_interest": "60",
                "greeks": {"delta": "-0.11"},
            }
        return super().get_order_book(instrument_name, depth=depth)


def test_manage_drops_strike_when_usdc_just_short_of_cover(tmp_path) -> None:
    client = _ShortUsdcClient(btc_book_equity="0.2")
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        covered_call_spot_exit_enabled=True,
        covered_call_itm_to_cash_secured_enabled=True,
        covered_call_csp_dte_min=2,
        covered_call_csp_dte_max=21,
        linear_min_book_notional_usdc=Decimal("1000"),
        linear_min_open_interest=Decimal("1"),
    )
    engine = DeribitOptionTrialBot(config, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            short_strike="63000",
            spot_exit_amount="0.0915",
            spot_exit_settlement_loss="0.0084211",
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=False)
    previews = [a for a in result["actions"] if a.get("action") == "cash_secured_preview"]
    assert len(previews) == 1
    candidate = previews[0]["candidate"]
    assert Decimal(str(candidate["short_strike"])) == Decimal("62000")
    assert Decimal(str(candidate["quantity"])) == Decimal("0.1")


def _csp_engine(tmp_path, client):
    return DeribitOptionTrialBot(
        make_config(
            tmp_path,
            option_strategy="covered_call",
            option_markets_profile="inverse_native",
            covered_call_spot_exit_enabled=True,
            covered_call_itm_to_cash_secured_enabled=True,
            covered_call_csp_dte_min=2,
            covered_call_csp_dte_max=21,
            linear_min_book_notional_usdc=Decimal("1000"),
            linear_min_open_interest=Decimal("1"),
        ),
        client,
    )


def test_manage_takes_cash_secured_bid_ioc(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            cash_secured_status="skipped",
            cash_secured_reason="operator_cancelled",
        )
    )
    engine.state_store.save(state)

    result = engine.manage(live=True)
    entered = [a for a in result["actions"] if a.get("action") == "cash_secured_entered"]
    assert len(entered) == 1
    assert entered[0]["take_bid"] is True
    assert entered[0]["time_in_force"] == "immediate_or_cancel"
    assert entered[0]["park_mid"] is False
    order = client.placed_orders[0]
    assert order["time_in_force"] == "immediate_or_cancel"
    assert order["post_only"] in {False, None}
    loaded = engine.state_store.load()
    parent = next(g for g in loaded.groups if g.group_id == "0095")
    child = next(g for g in loaded.groups if g.strategy == "cash_secured")
    assert parent.cash_secured_status == "entered"
    assert parent.cash_secured_group_id == child.group_id
    assert child.cash_secured_from_group_id == "0095"


def test_manage_retries_unfilled_cash_secured_ioc(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    client.order_scripts_by_label["trial-csp-btc-0095-short"] = [
        {"order_state": "cancelled", "filled_amount": "0", "trades": []},
        {"order_state": "filled"},
    ]
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(_itm_sold_group())
    engine.state_store.save(state)

    first = engine.manage(live=True)
    assert any(a.get("action") == "cash_secured_unfilled" for a in first["actions"])
    parent = engine.state_store.load().groups[0]
    assert parent.cash_secured_status == ""
    assert parent.cash_secured_group_id == ""

    second = engine.manage(live=True)
    entered = [a for a in second["actions"] if a.get("action") == "cash_secured_entered"]
    assert len(entered) == 1
    assert engine.state_store.load().groups[0].cash_secured_status == "entered"


def test_manage_retries_cancelled_cash_secured_park_with_ioc(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            cash_secured_status="submitted",
            cash_secured_reason="parked_mid",
            cash_secured_order_id="csp-park-1",
            cash_secured_instrument_name="BTC_USDC-14APR30-63000-P",
            cash_secured_limit_price=Decimal("610"),
        )
    )
    engine.state_store.save(state)
    client.order_states["csp-park-1"] = {
        "order": {"order_id": "csp-park-1", "order_state": "cancelled", "filled_amount": "0"}
    }

    result = engine.manage(live=True)
    assert any(
        a.get("action") == "cash_secured_skipped" and a.get("reason") == "operator_cancelled" for a in result["actions"]
    )
    entered = [a for a in result["actions"] if a.get("action") == "cash_secured_entered"]
    assert len(entered) == 1
    assert entered[0]["take_bid"] is True
    parent = next(g for g in engine.state_store.load().groups if g.group_id == "0095")
    assert parent.cash_secured_status == "entered"


def test_manage_completes_filled_cash_secured_park(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            cash_secured_status="submitted",
            cash_secured_reason="parked_mid",
            cash_secured_order_id="csp-fill-1",
            cash_secured_instrument_name="BTC_USDC-14APR30-63000-P",
            cash_secured_limit_price=Decimal("610"),
        )
    )
    engine.state_store.save(state)
    client.order_states["csp-fill-1"] = {
        "order": {
            "order_id": "csp-fill-1",
            "order_state": "filled",
            "filled_amount": "0.01",
            "average_price": "610",
            "price": "610",
        },
        "trades": [
            {
                "order_id": "csp-fill-1",
                "instrument_name": "BTC_USDC-14APR30-63000-P",
                "direction": "sell",
                "price": "610",
                "amount": "0.01",
                "fee": "0.2",
                "fee_currency": "USDC",
                "index_price": "70000",
            }
        ],
    }

    result = engine.manage(live=True)
    entered = [a for a in result["actions"] if a.get("action") == "cash_secured_entered"]
    assert len(entered) == 1
    loaded = engine.state_store.load()
    parent = next(g for g in loaded.groups if g.group_id == "0095")
    child = next(g for g in loaded.groups if g.strategy == "cash_secured")
    assert parent.cash_secured_status == "entered"
    assert parent.cash_secured_group_id == child.group_id
    assert child.cash_secured_from_group_id == "0095"
    assert child.quantity == Decimal("0.01")


def test_cash_secured_gates_are_slightly_looser_than_linear_not_wide_open(tmp_path) -> None:
    config = make_config(
        tmp_path,
        linear_min_open_interest=Decimal("8"),
        linear_max_spread_ratio=Decimal("0.14"),
        linear_min_book_notional_usdc=Decimal("4000"),
        covered_call_csp_min_open_interest=Decimal("6"),
        covered_call_csp_max_spread_ratio=Decimal("0.18"),
        covered_call_csp_min_book_notional_usdc=Decimal("3000"),
    )
    assert config.liquidity_gates("linear") == (
        Decimal("8"),
        Decimal("0.14"),
        Decimal("4000"),
    )
    assert config.cash_secured_liquidity_gates() == (
        Decimal("6"),
        Decimal("0.18"),
        Decimal("3000"),
    )
    selector = StrategySelector(config)
    instrument = OptionInstrument.from_api(
        {
            "instrument_name": "BTC_USDC-4SEP26-73000-P",
            "base_currency": "BTC",
            "quote_currency": "USDC",
            "settlement_currency": "USDC",
            "instrument_type": "linear",
            "tick_size": "2.5",
            "min_trade_amount": "0.1",
            "contract_size": "0.1",
            "option_type": "put",
            "expiration_timestamp": future_expiry(5),
            "strike": "73000",
            "instrument_state": "open",
        }
    )
    modest = OrderBookSnapshot(
        instrument_name=instrument.instrument_name,
        best_bid_price=Decimal("85"),
        best_bid_amount=Decimal("5"),
        best_ask_price=Decimal("100"),
        best_ask_amount=Decimal("5"),
        mark_price=Decimal("92"),
        index_price=Decimal("70000"),
        delta=Decimal("-0.20"),
        iv=Decimal("0.50"),
        open_interest=Decimal("10"),
    )
    # (100-85)/92.5 ≈ 16.2% — above linear 14%, inside CSP 18%
    assert modest.spread_ratio > Decimal("0.14")
    assert modest.spread_ratio < Decimal("0.18")
    assert selector._naked_short_put_rejection_reason("BTC", instrument, modest) == "spread_ratio_above_max"
    assert selector._cash_secured_put_rejection_reason("BTC", instrument, modest) is None

    wide = OrderBookSnapshot(
        instrument_name=instrument.instrument_name,
        best_bid_price=Decimal("95"),
        best_bid_amount=Decimal("5"),
        best_ask_price=Decimal("130"),
        best_ask_amount=Decimal("5"),
        mark_price=Decimal("110"),
        index_price=Decimal("70000"),
        delta=Decimal("-0.20"),
        iv=Decimal("0.50"),
        open_interest=Decimal("10"),
    )
    # (130-95)/112.5 ≈ 31% — CSP parks mid, so wide spread is allowed
    assert wide.spread_ratio > Decimal("0.18")
    assert selector._naked_short_put_rejection_reason("BTC", instrument, wide) == "spread_ratio_above_max"
    assert selector._cash_secured_put_rejection_reason("BTC", instrument, wide) is None


def test_cash_secured_put_itm_and_unrestored_cover() -> None:
    assert cash_secured_put_is_itm(index_price=Decimal("60000"), strike=Decimal("63000")) is True
    assert cash_secured_put_is_itm(index_price=Decimal("70000"), strike=Decimal("63000")) is False
    group = TradeGroup.from_dict(
        {
            "group_id": "0100",
            "currency": "BTC",
            "status": "closed",
            "strategy": "cash_secured",
            "quantity": "0.1",
            "spot_restore_amount": "0.03",
        }
    )
    assert cash_secured_cover_unrestored(group) == Decimal("0.07")


def _expired_csp_group(*, strike: str, index: Decimal, now_ms: int) -> TradeGroup:
    exp_ms = now_ms - 3_600_000
    return TradeGroup.from_dict(
        {
            "group_id": "0100",
            "currency": "BTC",
            "collateral_currency": "USDC",
            "short_instrument_name": "BTC_USDC-14APR30-63000-P",
            "short_label": "trial-csp-btc-0095-short",
            "status": "open",
            "strategy": "cash_secured",
            "option_type": "put",
            "quantity": "0.1",
            "short_strike": strike,
            "entry_credit": "20",
            "original_entry_credit": "20",
            "max_loss": "6300",
            "regime_at_entry": "normal",
            "entry_timestamp_ms": exp_ms - 20 * 86_400_000,
            "expiration_timestamp_ms": exp_ms,
            "close_index_usd": str(index),
            "cash_secured_from_group_id": "0095",
        }
    )


def test_reconcile_marks_csp_itm_expiry_for_cover_buy(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    now_ms = utc_now_ms()
    short = "BTC_USDC-14APR30-63000-P"
    state = StrategyState()
    state.groups.append(_expired_csp_group(strike="63000", index=Decimal("60000"), now_ms=now_ms))
    book = OrderBookSnapshot(
        instrument_name=short,
        best_bid_price=Decimal("2500"),
        best_bid_amount=Decimal("1"),
        best_ask_price=Decimal("2600"),
        best_ask_amount=Decimal("1"),
        mark_price=Decimal("2500"),
        index_price=Decimal("60000"),
        delta=Decimal("-0.80"),
        iv=Decimal("0.5"),
        open_interest=Decimal("10"),
    )
    engine._reconcile_state(
        state,
        option_positions=[],
        orderbook_cache={short: book},
        markets_by_currency=engine._load_supported_option_markets(),
    )
    closed = state.groups[0]
    assert closed.status == "closed"
    assert closed.close_reason == "reconciled_expiry"
    assert closed.spot_restore_status == "pending"
    assert closed.spot_restore_instrument_name == "BTC_USDC"
    assert closed.spot_restore_reason == "cash_secured_itm_assignment"


def test_reconcile_otm_csp_expiry_does_not_buy_cover(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    now_ms = utc_now_ms()
    short = "BTC_USDC-14APR30-63000-P"
    state = StrategyState()
    state.groups.append(_expired_csp_group(strike="63000", index=Decimal("70000"), now_ms=now_ms))
    book = OrderBookSnapshot(
        instrument_name=short,
        best_bid_price=Decimal("100"),
        best_bid_amount=Decimal("1"),
        best_ask_price=Decimal("110"),
        best_ask_amount=Decimal("1"),
        mark_price=Decimal("105"),
        index_price=Decimal("70000"),
        delta=Decimal("-0.05"),
        iv=Decimal("0.5"),
        open_interest=Decimal("10"),
    )
    engine._reconcile_state(
        state,
        option_positions=[],
        orderbook_cache={short: book},
        markets_by_currency=engine._load_supported_option_markets(),
    )
    closed = state.groups[0]
    assert closed.status == "closed"
    assert closed.spot_restore_status == ""
    assert closed.spot_restore_reason == ""


def _pending_csp_restore_group(**overrides) -> TradeGroup:
    payload = {
        "group_id": "0100",
        "currency": "BTC",
        "collateral_currency": "USDC",
        "status": "closed",
        "strategy": "cash_secured",
        "option_type": "put",
        "quantity": "0.1",
        "short_strike": "63000",
        "short_instrument_name": "BTC_USDC-14APR30-63000-P",
        "cash_secured_from_group_id": "0095",
        "spot_restore_status": "pending",
        "spot_restore_reason": "cash_secured_itm_assignment",
        "spot_restore_instrument_name": "BTC_USDC",
        "close_index_usd": "60000",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def test_manage_previews_csp_itm_cover_buy(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(_pending_csp_restore_group())
    engine.state_store.save(state)
    result = engine.manage(live=False)
    previews = [a for a in result["actions"] if a.get("action") == "cash_secured_cover_restore_preview"]
    assert len(previews) == 1
    assert previews[0]["park_mid"] is True
    assert previews[0]["instrument_name"] == "BTC_USDC"
    assert Decimal(str(previews[0]["quantity"])) > 0


def test_manage_parks_csp_itm_cover_buy(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    client.order_scripts_by_label["trial-csp-restore-btc-0100"] = [
        {"order_state": "open", "filled_amount": "0", "trades": []},
    ]
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(_pending_csp_restore_group())
    engine.state_store.save(state)

    result = engine.manage(live=True)
    parks = [a for a in result["actions"] if a.get("action") == "cash_secured_cover_restore_submitted"]
    assert len(parks) == 1
    assert parks[0]["park_mid"] is True
    child = engine.state_store.load().groups[0]
    assert child.spot_restore_status == "submitted"
    assert child.spot_restore_reason == "cash_secured_itm_assignment"
    assert child.spot_restore_order_id
    assert child.spot_restore_instrument_name == "BTC_USDC"
    assert client.placed_orders[0]["order_type"] == "limit"
    assert client.placed_orders[0]["instrument_name"] == "BTC_USDC"

    resting = engine.manage(live=True)
    assert any(a.get("action") == "cash_secured_cover_restore_resting" for a in resting["actions"])
    assert not any(a.get("action") == "cash_secured_cover_restore_submitted" for a in resting["actions"])
    assert engine.state_store.load().groups[0].spot_restore_status == "submitted"


def test_manage_skips_cancelled_csp_cover_buy(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(
        _pending_csp_restore_group(
            spot_restore_status="submitted",
            spot_restore_order_id="csp-restore-1",
        )
    )
    engine.state_store.save(state)
    client.order_states["csp-restore-1"] = {
        "order": {"order_id": "csp-restore-1", "order_state": "cancelled", "filled_amount": "0"},
    }

    result = engine.manage(live=True)
    skipped = [a for a in result["actions"] if a.get("action") == "cash_secured_cover_restore_skipped"]
    assert any(a.get("reason") == "operator_cancelled" for a in skipped)
    child = engine.state_store.load().groups[0]
    assert child.spot_restore_status == "skipped"
    assert "operator_cancelled" in child.spot_restore_reason
    assert not child.spot_restore_order_id
    assert not client.placed_orders

    second = engine.manage(live=True)
    assert not any(
        a.get("action") in {"cash_secured_cover_restore", "cash_secured_cover_restore_submitted"}
        for a in second["actions"]
    )


def test_manage_completes_filled_csp_cover_buy(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(
        _pending_csp_restore_group(
            spot_restore_status="submitted",
            spot_restore_order_id="csp-restore-fill-1",
        )
    )
    engine.state_store.save(state)
    client.order_states["csp-restore-fill-1"] = {
        "order": {
            "order_id": "csp-restore-fill-1",
            "order_state": "filled",
            "filled_amount": "0.1",
            "average_price": "60000",
            "price": "60000",
        },
        "trades": [
            {
                "order_id": "csp-restore-fill-1",
                "instrument_name": "BTC_USDC",
                "direction": "buy",
                "price": "60000",
                "amount": "0.1",
                "fee": "6",
                "fee_currency": "USDC",
                "index_price": "60000",
            }
        ],
    }

    result = engine.manage(live=True)
    filled = [a for a in result["actions"] if a.get("action") == "cash_secured_cover_restore"]
    assert len(filled) == 1
    child = engine.state_store.load().groups[0]
    assert child.spot_restore_status == "filled"
    assert child.spot_restore_amount == Decimal("0.1")
    assert child.spot_restore_reason == "cash_secured_itm_assignment"
    assert child.spot_restore_quote_spent == Decimal("6006")
    assert not client.placed_orders


def test_manage_holds_open_cash_secured_to_expiry(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    client.positions = [
        {
            "instrument_name": "BTC_USDC-14APR30-63000-P",
            "direction": "sell",
            "kind": "option",
            "size": "-0.1",
            "size_currency": "-0.1",
            "mark_price": "610",
            "average_price": "600",
            "floating_profit_loss": "0",
            "delta": "-0.11",
        }
    ]
    engine = _csp_engine(tmp_path, client)
    state = StrategyState()
    state.groups.append(
        TradeGroup.from_dict(
            {
                "group_id": "0100",
                "currency": "BTC",
                "collateral_currency": "USDC",
                "status": "open",
                "strategy": "cash_secured",
                "option_type": "put",
                "quantity": "0.1",
                "short_strike": "63000",
                "short_instrument_name": "BTC_USDC-14APR30-63000-P",
                "short_label": "trial-csp-btc-0095-short",
                "entry_credit": "20",
                "original_entry_credit": "20",
                "max_loss": "6300",
                "entry_timestamp_ms": utc_now_ms() - 86400000,
                "expiration_timestamp_ms": utc_now_ms() + 3 * 86400000,
                "cash_secured_from_group_id": "0095",
            }
        )
    )
    engine.state_store.save(state)
    result = engine.manage(live=False)
    assert not any(
        str(a.get("action") or "").endswith("_closed") or str(a.get("reason") or "") == "time_exit"
        for a in result["actions"]
    )
    assert engine.state_store.load().groups[0].status == "open"
