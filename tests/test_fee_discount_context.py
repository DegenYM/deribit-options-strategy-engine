from __future__ import annotations

from decimal import Decimal

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.fee_discount import FeeDiscountContext

_T = 1_700_000_000_000  # first option trade anchor (ms)
_INSIDE = _T + 24 * 60 * 60 * 1000  # +1 day, inside a 6-month window
_OUTSIDE = _T + 220 * 24 * 60 * 60 * 1000  # +220 days, past the window


def _ctx(**overrides) -> FeeDiscountContext:
    base = dict(
        base_rate=Decimal("0.10"),
        discount_months=6,
        anchor="first_trade",
        registration_timestamp_ms=None,
        first_trade_timestamp_ms=_T,
    )
    base.update(overrides)
    return FeeDiscountContext(**base)


def test_rate_at_inside_window_returns_base_rate() -> None:
    assert _ctx().rate_at(_INSIDE) == Decimal("0.10")


def test_rate_at_outside_window_returns_zero() -> None:
    assert _ctx().rate_at(_OUTSIDE) == Decimal("0")


def test_rate_zero_when_disabled() -> None:
    assert _ctx(base_rate=Decimal("0")).rate_at(_INSIDE) == Decimal("0")


def test_engine_and_strategy_share_single_context(tmp_path) -> None:
    config = make_config(
        tmp_path,
        option_fee_discount_rate=Decimal("0.10"),
        option_fee_discount_months=6,
        option_fee_discount_anchor="first_trade",
    )
    bot = DeribitOptionTrialBot(config, FakeClient())

    # Pre-seed the engine's resolve cache so it does not overwrite the anchor.
    bot._fee_discount_first_trade_ms = _T
    bot.strategy.first_option_trade_timestamp_ms = _T

    # The legacy attribute proxies the shared context.
    assert bot.strategy.fee_discount.first_trade_timestamp_ms == _T

    # Screening and execution must resolve identically through the one context.
    assert bot.strategy._effective_fee_discount_rate(_INSIDE) == bot._option_fee_discount_rate_at(_INSIDE)
    assert bot.strategy._effective_fee_discount_rate(_INSIDE) == Decimal("0.10")
    assert bot.strategy._effective_fee_discount_rate(_OUTSIDE) == bot._option_fee_discount_rate_at(_OUTSIDE)
    assert bot._option_fee_discount_rate_at(_OUTSIDE) == Decimal("0")
