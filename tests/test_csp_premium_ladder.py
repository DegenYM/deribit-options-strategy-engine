"""Premium ladder: the wheel's own put premium lifts the next put's strike.

Ported from Canopy (2026-09-14); the arithmetic and these cases are the same in both engines.

Why: the put's strike window ``[K × 0.95, K]`` is anchored to K, the strike the coin was
called away at, and K never moves. Once spot runs past it the window empties — on real
chains (2026-09-12) at about 20% above K for BTC and 30% for ETH.

The real cap on a cash-secured strike is collateral, not K, and collateral is the sale
proceeds plus the premium the puts earned. So::

    ceiling = K + net premium of this wheel's closed puts / contracts
    window  = [ceiling × (1 − floor_pct), ceiling]

Not free money: a higher strike is nearer the money, so the coin is more likely to come
back, at a higher price. Off in code; the shared covered_call profile turns it on, and it
cannot be combined with swapping the put premium into coin.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeClient, make_config

from deribit_engine.cash_secured_ops import (
    cash_secured_premium_ledger,
    cash_secured_strike_bounds,
)
from deribit_engine.config import ConfigurationError, load_config
from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.engine import covered_call as covered_call_module
from deribit_engine.models import TradeGroup

CALLED_STRIKE = Decimal("100000")
FLOOR = Decimal("0.05")


def _parent(**over) -> TradeGroup:
    base = dict(
        group_id="P1",
        currency="BTC",
        collateral_currency="BTC",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC-29DEC26-100000-C",
        short_strike=CALLED_STRIKE,
        entry_credit=Decimal("0"),
        original_entry_credit=Decimal("0"),
        max_loss=Decimal("0"),
        regime_at_entry="normal",
        option_type="call",
        strategy="covered_call",
        status="closed",
        close_reason="reconciled_expiry",
        spot_exit_reason="covered_call_settlement_exit",
        spot_exit_status="filled",
    )
    base.update(over)
    return TradeGroup(**base)


def _child(group_id: str, *, credit: str, debit: str = "0", fee: str = "0", status: str = "closed") -> TradeGroup:
    child = TradeGroup(
        group_id=group_id,
        currency="BTC",
        collateral_currency="USDC",
        quantity=Decimal("1"),
        entry_timestamp_ms=1,
        expiration_timestamp_ms=2,
        short_instrument_name="BTC_USDC-29DEC26-100000-P",
        short_strike=CALLED_STRIKE,
        entry_credit=Decimal(credit),
        original_entry_credit=Decimal(credit),
        max_loss=Decimal("0"),
        regime_at_entry="normal",
        option_type="put",
        strategy="cash_secured_put",
        status=status,
        close_reason="reconciled_expiry" if status == "closed" else "",
        cash_secured_from_group_id="P1",
    )
    return replace(child, realized_close_debit=Decimal(debit), realized_close_fee=Decimal(fee))


# --- the window -------------------------------------------------------------------------


def test_without_the_ladder_the_ceiling_stays_at_the_called_strike():
    low, high = cash_secured_strike_bounds(CALLED_STRIKE, FLOOR)
    assert high == CALLED_STRIKE
    assert low == Decimal("95000")


def test_banked_premium_raises_the_ceiling_by_exactly_that_premium():
    """Sell at 2,000, bank 100 after fees -> the next put can be written at 2,100."""
    low, high = cash_secured_strike_bounds(
        Decimal("2000"),
        FLOOR,
        premium_credit=Decimal("100"),
        quantity=Decimal("1"),
    )
    assert high == Decimal("2100")
    assert low == Decimal("2100") * Decimal("0.95")


def test_the_ladder_is_per_contract():
    """200 banked on two contracts lifts the strike by 100 — otherwise it is not cash-secured."""
    _low, high = cash_secured_strike_bounds(
        Decimal("2000"),
        FLOOR,
        premium_credit=Decimal("200"),
        quantity=Decimal("2"),
    )
    assert high == Decimal("2100")


def test_a_loss_lowers_the_ceiling():
    """Money an active roll lost comes out of the strike it can secure, as it comes out of the cash."""
    _low, high = cash_secured_strike_bounds(
        Decimal("2000"),
        FLOOR,
        premium_credit=Decimal("-50"),
        quantity=Decimal("1"),
    )
    assert high == Decimal("1950")


def test_the_window_does_not_depend_on_spot():
    """The window is ``[(K + A) × 0.95, K + A]``; spot is not an input.

    A call settles in the money, so at entry spot is always above the window; if spot later
    falls through it, the put already open goes ITM and self-assign ends the leg. The engine
    never writes a new put far below the market, so a spot cap would only give up premium.
    """
    low, high = cash_secured_strike_bounds(
        CALLED_STRIKE,
        FLOOR,
        premium_credit=Decimal("9000"),
        quantity=Decimal("1"),
    )
    assert high == Decimal("109000")
    assert low == Decimal("109000") * Decimal("0.95")


@pytest.mark.parametrize("strike", [Decimal("0"), Decimal("-1")])
def test_a_missing_strike_yields_an_empty_window(strike: Decimal):
    assert cash_secured_strike_bounds(strike, FLOOR) == (Decimal("0"), Decimal("0"))


# --- the ledger -------------------------------------------------------------------------


def test_the_ledger_sums_closed_children_net_of_costs():
    parent = _parent()
    groups = [
        parent,
        _child("C1", credit="100"),  # expired worthless
        _child("C2", credit="120", debit="22", fee="2"),  # bought back
    ]
    assert cash_secured_premium_ledger(parent, groups) == Decimal("198")


def test_the_close_fee_is_not_counted_twice():
    """The close debit already includes the close fee: bought back at 20.00 + 2.33 fee is 22.33, not 24.66."""
    parent = _parent()
    rolled = _child("C1", credit="20.6176331", debit="22.3293686", fee="2.3293686")
    assert cash_secured_premium_ledger(parent, [parent, rolled]) == Decimal("20.6176331") - Decimal("22.3293686")


def test_an_open_child_is_not_in_the_ledger():
    """The open put's premium is already committed as collateral; it cannot count twice."""
    parent = _parent()
    groups = [parent, _child("C1", credit="100", status="open")]
    assert cash_secured_premium_ledger(parent, groups) == Decimal("0")


def test_a_child_with_no_recorded_credit_is_skipped_not_counted_as_a_loss():
    parent = _parent()
    groups = [parent, _child("C1", credit="0", debit="40")]
    assert cash_secured_premium_ledger(parent, groups) == Decimal("0")


def test_another_parents_children_do_not_fund_this_wheel():
    parent = _parent()
    stranger = replace(_child("C9", credit="500"), cash_secured_from_group_id="P2")
    assert cash_secured_premium_ledger(parent, [parent, stranger]) == Decimal("0")


def test_premium_already_swapped_into_coin_does_not_lift_the_strike():
    """Swapped premium is coin now, not collateral — an account that ran the swap before the ladder."""
    parent = _parent()
    swapped = replace(
        _child("C1", credit="500"),
        csp_premium_swap_amount=Decimal("200"),
        csp_premium_swap_status="filled",
    )
    # A pending row stashes the full premium as its amount before the first fill: nothing spent yet.
    pending = replace(
        _child("C2", credit="100"),
        csp_premium_swap_amount=Decimal("100"),
        csp_premium_swap_status="pending",
    )
    assert cash_secured_premium_ledger(parent, [parent, swapped, pending]) == Decimal("400")


# --- both together ----------------------------------------------------------------------


def test_ledger_and_window_together_walk_the_strike_up():
    """Called away -> two puts expire worthless -> the strike climbs by what they banked, no more."""
    parent = _parent()
    groups = [parent, _child("C1", credit="900"), _child("C2", credit="1100", debit="50", fee="50")]
    banked = cash_secured_premium_ledger(parent, groups)
    assert banked == Decimal("1950")
    _low, high = cash_secured_strike_bounds(
        parent.short_strike,
        FLOOR,
        premium_credit=banked,
        quantity=parent.quantity,
    )
    assert high == Decimal("101950")


def test_with_the_ladder_off_the_same_history_changes_nothing():
    """With the switch off, however much is banked, the window must not move."""
    parent = _parent()
    groups = [parent, _child("C1", credit="900"), _child("C2", credit="1100")]
    assert cash_secured_premium_ledger(parent, groups) > 0
    _low, high = cash_secured_strike_bounds(parent.short_strike, FLOOR)
    assert high == CALLED_STRIKE


# --- the engine -------------------------------------------------------------------------


def _engine(tmp_path, *, ladder: bool):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        option_markets_profile="inverse_native",
        managed_currencies=("BTC",),
        enable_short_put=False,
        enable_short_call=True,
        covered_call_itm_to_cash_secured_enabled=True,
        covered_call_csp_strike_floor_pct=FLOOR,
        covered_call_csp_premium_ladder=ladder,
    )
    return DeribitOptionTrialBot(config, FakeClient())


def _context(*groups: TradeGroup):
    return SimpleNamespace(state=SimpleNamespace(groups=list(groups)))


def test_the_engine_window_follows_the_switch(tmp_path):
    parent = _parent()
    context = _context(parent, _child("C1", credit="900"), _child("C2", credit="1100", debit="50", fee="50"))
    on = _engine(tmp_path, ladder=True)._cash_secured_strike_window(context, parent, quantity=Decimal("1"))
    off = _engine(tmp_path, ladder=False)._cash_secured_strike_window(context, parent, quantity=Decimal("1"))
    assert on == (Decimal("101950") * Decimal("0.95"), Decimal("101950"))
    assert off == (Decimal("95000"), CALLED_STRIKE)


def test_the_scan_floor_override_still_applies_with_the_ladder(tmp_path):
    parent = _parent()
    context = _context(parent, _child("C1", credit="1000"))
    low, high = _engine(tmp_path, ladder=True)._cash_secured_strike_window(
        context,
        parent,
        quantity=Decimal("1"),
        strike_floor_pct=Decimal("0.10"),
    )
    assert high == Decimal("101000")
    assert low == Decimal("101000") * Decimal("0.90")


def test_every_put_window_goes_through_the_one_helper():
    """Live entry, dry-run scan and active roll must agree, or the scan would show a strike the bot would not write."""
    source = Path(covered_call_module.__file__).read_text()
    assert source.count("cash_secured_strike_bounds(") == 1
    assert source.count("self._cash_secured_strike_window(") == 3


# --- config -----------------------------------------------------------------------------


def _load(tmp_path, *lines: str):
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(["OPTION_STRATEGY=covered_call", *lines]))
    return load_config(env_file, require_private=False)


def test_the_ladder_is_off_in_code(tmp_path):
    assert _load(tmp_path).covered_call_csp_premium_ladder is False


def test_the_ladder_parses(tmp_path):
    assert _load(tmp_path, "CSP_PREMIUM_LADDER=true").covered_call_csp_premium_ladder is True


def test_the_ladder_and_the_premium_swap_cannot_both_be_on(tmp_path):
    """Both spend the same premium; whichever ran second would find it gone."""
    with pytest.raises(ConfigurationError, match="CSP_PREMIUM_LADDER"):
        _load(tmp_path, "CSP_PREMIUM_LADDER=true", "COVERED_CALL_CSP_PREMIUM_TARGET=spot")
    swap_only = _load(tmp_path, "CSP_PREMIUM_LADDER=false", "COVERED_CALL_CSP_PREMIUM_TARGET=spot")
    assert swap_only.covered_call_csp_premium_target == "spot"
