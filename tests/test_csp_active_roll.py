"""CSP active roll (OTM buy-back + higher daily-yield put). Default OFF."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from conftest import FakeClient, future_expiry, make_config
from test_cash_secured_ops import _csp_engine, _itm_sold_group

from deribit_engine.cash_secured_ops import (
    cash_secured_active_roll_daily_beats_hold,
    cash_secured_active_roll_dte_reason,
    cash_secured_active_roll_fee_edge,
    cash_secured_active_roll_taker_spread_usdc,
    cash_secured_active_roll_tv_ratio,
    cash_secured_last_active_roll_child,
    cash_secured_later_expiry_in_window,
)
from deribit_engine.config import load_config
from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.utils import utc_now_ms

CURRENT = "BTC_USDC-14APR30-63000-P"
NEXT = "BTC_USDC-21APR30-63000-P"
LAST = "BTC_USDC-21APR30-63000-P"
FLOOR_14 = "BTC_USDC-14APR30-60000-P"
FLOOR_21 = "BTC_USDC-21APR30-60000-P"


def _otm_book(name: str, *, bid: str, ask: str, amount: str = "0.2") -> dict:
    mid = (Decimal(bid) + Decimal(ask)) / Decimal("2")
    return {
        "instrument_name": name,
        "best_bid_price": bid,
        "best_bid_amount": amount,
        "best_ask_price": ask,
        "best_ask_amount": amount,
        "mark_price": str(mid),
        "index_price": "70000",
        "mark_iv": "0.55",
        "open_interest": "80",
        "greeks": {"delta": "-0.11"},
    }


def _roll_books(client: FakeClient, *, current_bid="80", current_ask="90", next_bid="400", next_ask="420") -> None:
    client.order_book_overrides[CURRENT] = _otm_book(CURRENT, bid=current_bid, ask=current_ask)
    client.order_book_overrides[NEXT] = _otm_book(NEXT, bid=next_bid, ask=next_ask)
    dead = _otm_book(FLOOR_14, bid="1", ask="1.1")
    dead["open_interest"] = "0"
    client.order_book_overrides[FLOOR_14] = dead
    dead21 = dict(dead)
    dead21["instrument_name"] = FLOOR_21
    client.order_book_overrides[FLOOR_21] = dead21


def _open_csp(*, instrument: str = CURRENT, expiration_ms: int | None = None, **overrides) -> TradeGroup:
    payload = {
        "group_id": "0100",
        "currency": "BTC",
        "collateral_currency": "USDC",
        "status": "open",
        "strategy": "cash_secured",
        "option_type": "put",
        "quantity": "0.1",
        "short_strike": "63000",
        "short_instrument_name": instrument,
        "short_label": "trial-csp-btc-0100-short",
        "entry_credit": "20",
        "original_entry_credit": "20",
        "max_loss": "6300",
        "entry_timestamp_ms": utc_now_ms() - 86400000,
        "expiration_timestamp_ms": expiration_ms if expiration_ms is not None else future_expiry(14),
        "cash_secured_from_group_id": "0095",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def _parent() -> TradeGroup:
    return _itm_sold_group(
        cash_secured_status="entered",
        cash_secured_group_id="0100",
        cash_secured_group_ids=["0100"],
    )


def _short_position(instrument: str = CURRENT) -> dict:
    return {
        "instrument_name": instrument,
        "direction": "sell",
        "kind": "option",
        "size": "-0.1",
        "size_currency": "-0.1",
        "mark_price": "85",
        "average_price": "200",
        "floating_profit_loss": "0",
        "delta": "-0.11",
    }


def _roll_engine(tmp_path, client, **overrides):
    values = dict(
        covered_call_csp_active_roll_enabled=True,
        covered_call_csp_active_roll_min_dte=2,
        covered_call_csp_active_roll_max_dte=21,
        covered_call_csp_active_roll_min_tv_ratio=Decimal("0.25"),
        covered_call_csp_active_roll_min_net_usdc=Decimal("5"),
        covered_call_csp_dte_min=2,
        covered_call_csp_dte_max=21,
        covered_call_csp_self_assign_enabled=False,
    )
    values.update(overrides)
    return _csp_engine(tmp_path, client, **values)


def _seed(engine, *groups: TradeGroup, next_group_id: int = 200) -> None:
    state = StrategyState()
    state.next_group_id = next_group_id
    for group in groups:
        state.groups.append(group)
    engine.state_store.save(state)


def _roll_actions(result: dict) -> list[dict]:
    return [a for a in result["actions"] if a.get("action") == "cash_secured_active_roll"]


def test_active_roll_helpers_tv_dte_fee_and_later_expiry() -> None:
    # OTM: TV = close debit; ratio vs original credit.
    ratio = cash_secured_active_roll_tv_ratio(
        index_price=Decimal("70000"),
        strike=Decimal("63000"),
        quantity=Decimal("0.1"),
        current_debit=Decimal("9"),
        entry_credit=Decimal("20"),
    )
    assert ratio == Decimal("9") / Decimal("20")
    thin = cash_secured_active_roll_tv_ratio(
        index_price=Decimal("70000"),
        strike=Decimal("63000"),
        quantity=Decimal("0.1"),
        current_debit=Decimal("2"),
        entry_credit=Decimal("20"),
    )
    assert thin < Decimal("0.25")

    assert (
        cash_secured_active_roll_dte_reason(dte_days=Decimal("1"), min_dte=Decimal("2"), max_dte=Decimal("10"))
        == "dte_too_short"
    )
    assert (
        cash_secured_active_roll_dte_reason(dte_days=Decimal("14"), min_dte=Decimal("2"), max_dte=Decimal("10"))
        == "dte_out_of_window"
    )
    assert cash_secured_active_roll_dte_reason(dte_days=Decimal("5"), min_dte=Decimal("2"), max_dte=Decimal("10")) == ""

    net, ok = cash_secured_active_roll_fee_edge(
        new_credit=Decimal("40"),
        close_debit=Decimal("9"),
        close_fee=Decimal("1.125"),
        open_fee=Decimal("2.1"),
        min_net_usdc=Decimal("5"),
    )
    assert ok is True
    assert net == Decimal("27.775")
    _, edge_off = cash_secured_active_roll_fee_edge(
        new_credit=Decimal("10"),
        close_debit=Decimal("9"),
        close_fee=Decimal("1.2"),
        open_fee=Decimal("2.1"),
        min_net_usdc=Decimal("5"),
    )
    assert edge_off is False

    spread = cash_secured_active_roll_taker_spread_usdc(
        close_bid=Decimal("80"),
        close_ask=Decimal("90"),
        new_bid=Decimal("100"),
        new_ask=Decimal("120"),
        close_quantity=Decimal("0.1"),
        new_quantity=Decimal("0.1"),
    )
    assert spread == Decimal("1.5")
    eaten_net, eaten_ok = cash_secured_active_roll_fee_edge(
        new_credit=Decimal("10"),
        close_debit=Decimal("9"),
        close_fee=Decimal("1.125"),
        open_fee=Decimal("1.25"),
        min_net_usdc=Decimal("5"),
        spread_usdc=spread,
        min_net_edge_mult=Decimal("2"),
    )
    assert eaten_net < 0
    assert eaten_ok is False

    hold_daily, roll_daily, beats = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("20"),
        hold_dte=Decimal("2.75"),
        new_credit=Decimal("14"),
        new_dte=Decimal("2.75"),
    )
    assert beats is False
    assert hold_daily > roll_daily
    _hold, _roll, later_ok = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("9"),
        hold_dte=Decimal("14"),
        new_credit=Decimal("40"),
        new_dte=Decimal("21"),
        switch_fees=Decimal("3"),
    )
    assert later_ok is True
    _hold, _roll, earlier_ok = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("9"),
        hold_dte=Decimal("21"),
        new_credit=Decimal("40"),
        new_dte=Decimal("14"),
        switch_fees=Decimal("3"),
    )
    assert earlier_ok is True
    _hold, _roll, hurdle_off = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("9"),
        hold_dte=Decimal("14"),
        new_credit=Decimal("13"),
        new_dte=Decimal("14"),
        switch_fees=Decimal("3"),
        min_yield_edge=Decimal("0.25"),
    )
    assert hurdle_off is False
    _hold, _roll, hurdle_on = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("9"),
        hold_dte=Decimal("14"),
        new_credit=Decimal("40"),
        new_dte=Decimal("21"),
        switch_fees=Decimal("3"),
        min_yield_edge=Decimal("0.25"),
    )
    assert hurdle_on is True
    mid_hold, mid_roll, mid_beats = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("8.5"),
        hold_dte=Decimal("14"),
        new_credit=Decimal("20"),
        new_dte=Decimal("21"),
        switch_fees=Decimal("0"),
        min_yield_edge=Decimal("0.25"),
    )
    assert mid_beats is True
    assert mid_roll > mid_hold
    _taker_hold, _taker_roll, taker_beats = cash_secured_active_roll_daily_beats_hold(
        hold_credit=Decimal("9"),
        hold_dte=Decimal("14"),
        new_credit=Decimal("18"),
        new_dte=Decimal("21"),
        switch_fees=Decimal("3.225"),
        min_yield_edge=Decimal("0.25"),
    )
    assert taker_beats is False

    assert cash_secured_later_expiry_in_window(
        expiration_timestamp_ms=2,
        current_expiry_ms=1,
        instrument_name=NEXT,
        current_instrument=CURRENT,
        dte_days=Decimal("21"),
        dte_min=Decimal("2"),
        dte_max=Decimal("21"),
        strike=Decimal("63000"),
        min_strike=Decimal("59850"),
        max_strike=Decimal("63000"),
    )
    assert not cash_secured_later_expiry_in_window(
        expiration_timestamp_ms=1,
        current_expiry_ms=1,
        instrument_name=CURRENT,
        current_instrument=CURRENT,
        dte_days=Decimal("14"),
        dte_min=Decimal("2"),
        dte_max=Decimal("21"),
        strike=Decimal("63000"),
        min_strike=Decimal("59850"),
        max_strike=Decimal("63000"),
    )


def test_near_strike_does_not_active_roll(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.get_index_price = lambda name: {"index_price": Decimal("63200")}
    client.order_book_overrides[CURRENT]["index_price"] = "63200"
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is False
    assert rolls[0]["reason"] == "near_strike"
    assert not any(
        a.get("reason") == "csp_active_roll" for a in result["actions"] if a.get("action") == "close_group_preview"
    )


def test_active_roll_config_defaults_off(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPTION_STRATEGY=covered_call\nCOVERED_CALL_ITM_TO_CASH_SECURED_ENABLED=true\n")
    config = load_config(env_file, require_private=False)
    assert config.covered_call_csp_active_roll_enabled is False
    assert config.covered_call_csp_active_roll_min_dte == 2
    assert config.covered_call_csp_active_roll_max_dte == 10
    assert config.covered_call_csp_active_roll_min_tv_ratio == Decimal("0.25")
    assert config.covered_call_csp_active_roll_min_net_usdc == Decimal("5")
    assert config.covered_call_csp_active_roll_min_yield_edge == Decimal("0.25")
    assert config.covered_call_csp_active_roll_min_net_edge_mult == Decimal("2")
    assert config.covered_call_csp_hold_near_strike_pct == Decimal("0.01")


def test_flag_off_never_rolls(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _csp_engine(
        tmp_path,
        client,
        covered_call_csp_active_roll_enabled=False,
        covered_call_csp_active_roll_max_dte=21,
    )
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    assert _roll_actions(result) == []
    assert not any(a.get("reason") == "csp_active_roll" for a in result["actions"])
    loaded = engine.state_store.load()
    child = next(g for g in loaded.groups if g.group_id == "0100")
    assert child.status == "open"


def test_otm_liquid_next_dte_positive_edge_would_place(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is True
    assert rolls[0]["replacement_instrument"] == NEXT
    assert any(
        a.get("action") == "close_group_preview" and a.get("reason") == "csp_active_roll" for a in result["actions"]
    )
    assert Decimal(str(rolls[0]["net_edge"])) > 0
    assert Decimal(str(rolls[0]["roll_daily"])) > Decimal(str(rolls[0]["hold_daily"])) * Decimal("1.25")
    assert Decimal(str(rolls[0]["min_yield_edge"])) == Decimal("0.25")
    assert engine.state_store.load().groups[-1].status == "open"


def test_otm_liquid_next_dte_places_live(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=True)
    assert any(a.get("action") == "close_group" and a.get("reason") == "csp_active_roll" for a in result["actions"])
    entered = [a for a in result["actions"] if a.get("action") == "cash_secured_entered"]
    assert len(entered) == 1
    loaded = engine.state_store.load()
    closed = next(g for g in loaded.groups if g.group_id == "0100")
    assert closed.status == "closed"
    assert closed.close_reason == "csp_active_roll"
    child = next(g for g in loaded.groups if g.strategy == "cash_secured" and g.status == "open")
    assert child.short_instrument_name == NEXT
    assert child.quantity == Decimal("0.1")


def test_no_higher_daily_yield_holds(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, current_bid="1", current_ask="1.1", next_bid="80", next_ask="90")
    client.positions = [_short_position(LAST)]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp(instrument=LAST, expiration_ms=future_expiry(21)))
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["reason"] == "daily_yield_not_higher"
    assert rolls[0]["would_place"] is False
    assert engine.state_store.load().groups[-1].status == "open"


def test_wide_spread_on_close_no_roll(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, current_bid="80", current_ask="200")
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["reason"] == "illiquid_close"
    assert rolls[0]["would_place"] is False


def test_wide_spread_on_replacement_no_roll(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, next_bid="100", next_ask="400")
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["reason"] == "daily_yield_not_higher"
    assert rolls[0]["would_place"] is False


def test_daily_yield_not_higher_no_roll(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, next_bid="50", next_ask="55")
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["reason"] == "daily_yield_not_higher"
    assert rolls[0]["would_place"] is False


def test_earlier_expiry_higher_daily_would_place(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, current_bid="400", current_ask="420", next_bid="80", next_ask="90")
    client.positions = [_short_position(LAST)]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp(instrument=LAST, expiration_ms=future_expiry(21)))
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is True
    assert rolls[0]["replacement_instrument"] == CURRENT
    assert Decimal(str(rolls[0]["roll_daily"])) >= Decimal(str(rolls[0]["hold_daily"])) * Decimal("1.25")


def test_itm_csp_does_not_take_active_roll_path(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    client.get_index_price = lambda name: {"index_price": Decimal("60000")}
    client.order_book_overrides[CURRENT] = {
        **_otm_book(CURRENT, bid="3000", ask="3050"),
        "index_price": "60000",
        "greeks": {"delta": "-0.75"},
    }
    client.order_book_overrides[NEXT] = _otm_book(NEXT, bid="400", ask="420")
    client.positions = [_short_position()]
    engine = _roll_engine(
        tmp_path,
        client,
        covered_call_csp_self_assign_enabled=True,
        covered_call_csp_self_assign_confirm_cycles=1,
        covered_call_csp_self_assign_max_dte=Decimal("0"),
        covered_call_csp_self_assign_max_tv_pct=Decimal("0.01"),
    )
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    assert _roll_actions(result) == []
    assert not any(a.get("reason") == "csp_active_roll" for a in result["actions"])


def test_dte_at_or_below_min_no_roll(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client, covered_call_csp_active_roll_min_dte=2)
    _seed(engine, _parent(), _open_csp(expiration_ms=future_expiry(1)))
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["reason"] == "dte_too_short"
    assert rolls[0]["would_place"] is False


def test_close_fill_entry_fail_retries_entry_only(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    client.order_scripts_by_label["trial-csp-btc-0200-short"] = [
        {"order_state": "cancelled", "filled_amount": "0", "trades": []},
    ]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp(), next_group_id=200)

    first = engine.manage(live=True)
    assert any(a.get("action") == "close_group" and a.get("reason") == "csp_active_roll" for a in first["actions"])
    assert any(a.get("action") == "cash_secured_unfilled" for a in first["actions"])
    assert not any(a.get("action") == "cash_secured_entered" for a in first["actions"])
    after_close = engine.state_store.load()
    closed = next(g for g in after_close.groups if g.group_id == "0100")
    assert closed.status == "closed"
    parent = next(g for g in after_close.groups if g.group_id == "0095")
    assert parent.cash_secured_reason in {"active_roll_entry_pending", "ioc_unfilled"}
    close_buys = [o for o in client.placed_orders if o["direction"] == "buy" and o["instrument_name"] == CURRENT]
    assert len(close_buys) == 1

    second = engine.manage(live=True)
    assert any(a.get("action") == "cash_secured_entered" for a in second["actions"])
    assert not any(a.get("reason") == "csp_active_roll" and a.get("action") == "close_group" for a in second["actions"])
    close_buys_after = [o for o in client.placed_orders if o["direction"] == "buy" and o["instrument_name"] == CURRENT]
    assert len(close_buys_after) == 1
    loaded = engine.state_store.load()
    assert next(g for g in loaded.groups if g.group_id == "0100").status == "closed"
    opened = [g for g in loaded.groups if g.is_cash_secured_group() and g.status == "open"]
    assert len(opened) == 1
    assert opened[0].short_instrument_name == NEXT
    assert opened[0].group_id != "0100"


def test_retry_skips_worse_same_contract(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    client.order_scripts_by_label["trial-csp-btc-0200-short"] = [
        {"order_state": "cancelled", "filled_amount": "0", "trades": []},
    ]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp(), next_group_id=200)

    first = engine.manage(live=True)
    assert any(a.get("action") == "close_group" and a.get("reason") == "csp_active_roll" for a in first["actions"])
    assert any(a.get("action") == "cash_secured_unfilled" for a in first["actions"])

    client.order_book_overrides[NEXT] = _otm_book(NEXT, bid="50", ask="55")
    client.order_scripts_by_label["trial-csp-btc-0200-short"] = [
        {"order_state": "filled", "filled_amount": "0.1", "average_price": "80", "trades": []},
    ]
    second = engine.manage(live=True)
    assert not any(a.get("action") == "cash_secured_entered" for a in second["actions"])
    assert not any(a.get("reason") == "csp_active_roll" and a.get("action") == "close_group" for a in second["actions"])
    loaded = engine.state_store.load()
    opened = [g for g in loaded.groups if g.is_cash_secured_group() and g.status == "open"]
    assert opened == []
    assert next(g for g in loaded.groups if g.group_id == "0100").status == "closed"


def test_taker_spread_and_fees_eat_net_no_roll(tmp_path) -> None:
    """Earlier-dated bid can look like a yield bump; ask-close + fees wipe the cash."""
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, current_bid="110", current_ask="130", next_bid="80", next_ask="90")
    client.positions = [_short_position(LAST)]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp(instrument=LAST, expiration_ms=future_expiry(21)))
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is False
    assert rolls[0]["reason"] == "net_edge_too_small"
    assert engine.state_store.load().groups[-1].status == "open"


def test_mid_looks_better_taker_hurdle_blocks_roll(tmp_path) -> None:
    """Mid (no fees) would clear the 25% hurdle; IOC bid/ask + fees do not."""
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, current_bid="80", current_ask="90", next_bid="180", next_ask="220")
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is False
    assert rolls[0]["reason"] == "daily_yield_not_higher"
    assert engine.state_store.load().groups[-1].status == "open"


def test_enough_taker_net_and_yield_edge_would_place(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client, current_bid="80", current_ask="90", next_bid="400", next_ask="420")
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    _seed(engine, _parent(), _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is True
    assert Decimal(str(rolls[0]["net_edge"])) > Decimal("9")
    assert Decimal(str(rolls[0]["spread_usdc"])) > 0
    assert Decimal(str(rolls[0]["close_fee"])) > 0
    assert Decimal(str(rolls[0]["open_fee"])) > 0


def test_active_roll_hurdle_env_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPTION_STRATEGY=covered_call",
                "COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED=true",
                "COVERED_CALL_CSP_ACTIVE_ROLL_MIN_YIELD_EDGE=0.3",
                "COVERED_CALL_CSP_ACTIVE_ROLL_MIN_NET_EDGE_MULT=3",
                "",
            ]
        )
    )
    config = load_config(env_file, require_private=False)
    assert config.covered_call_csp_active_roll_min_yield_edge == Decimal("0.3")
    assert config.covered_call_csp_active_roll_min_net_edge_mult == Decimal("3")


def test_make_config_default_active_roll_false(tmp_path) -> None:
    config = make_config(tmp_path, option_strategy="covered_call")
    assert config.covered_call_csp_active_roll_enabled is False
    assert config.covered_call_csp_active_roll_min_yield_edge == Decimal("0.25")
    assert config.covered_call_csp_active_roll_min_net_edge_mult == Decimal("2")


def test_active_roll_skips_when_parent_cover_restored(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    parent = _parent()
    parent.spot_restore_status = "filled"
    parent.spot_restore_amount = Decimal("0.1")
    parent.spot_restore_quote_spent = Decimal("7000")
    _seed(engine, parent, _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is False
    assert rolls[0]["reason"] == "cover_restored"
    assert engine.state_store.load().groups[-1].status == "open"
    assert not any(
        a.get("reason") == "csp_active_roll" for a in result["actions"] if a.get("action") == "close_group_preview"
    )


def test_active_roll_skips_when_sibling_manual_close(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    parent = _parent()
    parent.cash_secured_group_ids = ["0099", "0100"]
    closed = _open_csp()
    closed.group_id = "0099"
    closed.status = "closed"
    closed.close_reason = "manual_close"
    closed.closed_timestamp_ms = utc_now_ms() - 60_000
    closed.close_index_usd = Decimal("80674")
    _seed(engine, parent, closed, _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is False
    assert rolls[0]["reason"] == "operator_closed_child"
    opened = [g for g in engine.state_store.load().groups if g.group_id == "0100"]
    assert opened[0].status == "open"


def test_active_roll_skips_when_sibling_reconciled_external(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    client.positions = [_short_position()]
    engine = _roll_engine(tmp_path, client)
    parent = _parent()
    parent.cash_secured_group_ids = ["0099", "0100"]
    closed = _open_csp()
    closed.group_id = "0099"
    closed.status = "closed"
    closed.close_reason = "reconciled_external"
    closed.closed_timestamp_ms = utc_now_ms() - 60_000
    closed.close_index_usd = Decimal("80674")
    _seed(engine, parent, closed, _open_csp())
    result = engine.manage(live=False)
    rolls = _roll_actions(result)
    assert len(rolls) == 1
    assert rolls[0]["would_place"] is False
    assert rolls[0]["reason"] == "operator_closed_child"
    opened = [g for g in engine.state_store.load().groups if g.group_id == "0100"]
    assert opened[0].status == "open"


def _closed_child(group_id: str, *, close_reason: str, entered_days_ago: int, **overrides) -> TradeGroup:
    day = 86_400_000
    return _open_csp(
        group_id=group_id,
        status="closed",
        close_reason=close_reason,
        entry_timestamp_ms=utc_now_ms() - entered_days_ago * day,
        closed_timestamp_ms=utc_now_ms() - (entered_days_ago - 1) * day,
        close_index_usd="70000",
        **overrides,
    )


def test_active_roll_child_is_the_hurdle_only_while_it_is_newest() -> None:
    """The buyback gates the replacement it was priced against, and nothing later."""
    parent = _itm_sold_group(
        cash_secured_status="entered",
        cash_secured_group_id="0111",
        cash_secured_group_ids=["0109", "0111"],
    )
    rolled = _closed_child("0109", close_reason="csp_active_roll", entered_days_ago=5)

    # Entry failed right after the buyback: it is still the newest child, so the
    # retry prices the replacement against holding it.
    assert cash_secured_last_active_roll_child(parent, [parent, rolled]) is rolled

    # The retry filled and that put has since expired. The buyback is history —
    # reviving its close price would hurdle every later put against a contract
    # that has not existed for days.
    expired = _closed_child("0111", close_reason="reconciled_expiry", entered_days_ago=3)
    assert cash_secured_last_active_roll_child(parent, [parent, rolled, expired]) is None


def test_active_roll_child_ignored_while_a_later_put_is_open() -> None:
    parent = _itm_sold_group(
        cash_secured_status="entered",
        cash_secured_group_id="0111",
        cash_secured_group_ids=["0109", "0111"],
    )
    rolled = _closed_child("0109", close_reason="csp_active_roll", entered_days_ago=5)
    live = _open_csp(group_id="0111", entry_timestamp_ms=utc_now_ms() - 86_400_000)
    assert cash_secured_last_active_roll_child(parent, [parent, rolled, live]) is None


def test_expired_put_after_a_roll_writes_the_next_put(tmp_path) -> None:
    """The wheel re-enters after expiry instead of hurdling against a stale buyback."""
    client = FakeClient(btc_book_equity="0.2")
    _roll_books(client)
    engine = _roll_engine(tmp_path, client)
    parent = _itm_sold_group(
        cash_secured_status="entered",
        cash_secured_group_id="0111",
        cash_secured_group_ids=["0109", "0111"],
    )
    # Bought back at 1420 on a near-the-money put: a hurdle no OTM put can clear.
    rolled = _closed_child(
        "0109",
        close_reason="csp_active_roll",
        entered_days_ago=5,
        short_close_average_price="1420",
    )
    expired = _closed_child("0111", close_reason="reconciled_expiry", entered_days_ago=3)
    _seed(engine, parent, rolled, expired, next_group_id=200)

    result = engine.manage(live=True)
    assert any(a.get("action") == "cash_secured_entered" for a in result["actions"])
    assert not any(a.get("reason") == "daily_yield_not_higher" for a in result["actions"])
