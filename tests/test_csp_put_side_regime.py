"""The wheel's cash-secured put is gated on the put book, not the call book."""

from __future__ import annotations

from decimal import Decimal

from conftest import FakeClient
from test_cash_secured_ops import _csp_engine, _itm_sold_group

from deribit_engine.models import RiskRegime, StrategyState


def _dry_call_book(engine) -> None:
    """Make the currency regime probe fail the way a dry call book does."""
    engine.strategy.core_regime_liquidity_detail = lambda *_a, **_k: (
        False,
        ["call: liquid_expiries=0 < required=1"],
    )


def _seed_parent(engine, **overrides) -> None:
    state = StrategyState()
    state.groups.append(
        _itm_sold_group(
            cash_secured_status="skipped",
            cash_secured_reason="operator_cancelled",
            **overrides,
        )
    )
    engine.state_store.save(state)


def test_dry_call_book_crises_the_currency_but_not_the_put(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    _dry_call_book(engine)

    # The probe is stubbed, so the market list only has to be non-empty.
    regime, detail = engine._determine_regime_with_detail(
        "BTC",
        markets=[object()],
        orderbook_cache={},
    )
    assert regime is RiskRegime.CRISIS
    assert "core_entry_liquidity_check_failed" in detail

    # The put this wheel writes lives on another book entirely.
    assert engine._cash_secured_regime("BTC") is RiskRegime.NORMAL


def test_macro_crisis_still_blocks_the_put(tmp_path) -> None:
    client = FakeClient(
        btc_book_equity="0.2",
        drawdowns={"BTC": Decimal("-0.10"), "ETH": Decimal("-0.02")},
    )
    engine = _csp_engine(tmp_path, client)
    assert engine._cash_secured_regime("BTC") is RiskRegime.CRISIS

    _seed_parent(engine)
    result = engine.manage(live=True)
    skips = [a for a in result["actions"] if a.get("action") == "cash_secured_skipped"]
    assert any(a.get("reason") == "crisis_regime" for a in skips)
    assert not any(a.get("action") == "cash_secured_entered" for a in result["actions"])


def test_dry_call_book_does_not_veto_a_writable_put(tmp_path) -> None:
    client = FakeClient(btc_book_equity="0.2")
    engine = _csp_engine(tmp_path, client)
    _dry_call_book(engine)
    _seed_parent(engine)

    result = engine.manage(live=True)
    assert any(a.get("action") == "cash_secured_entered" for a in result["actions"])
    assert not any(
        a.get("action") == "cash_secured_skipped" and a.get("reason") == "crisis_regime" for a in result["actions"]
    )
