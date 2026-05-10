from __future__ import annotations

from decimal import Decimal

from deribit_demo.current_stress import compute_current_stress
from conftest import make_config


def _linear_put_position(
    instrument_name: str,
    *,
    direction: str,
    size: str = "0.1",
    size_currency: str = "0",
    mark_price: str,
) -> dict[str, str]:
    return {
        "instrument_name": instrument_name,
        "direction": direction,
        "kind": "option",
        "size": size,
        "size_currency": size_currency,
        "mark_price": mark_price,
        "average_price": mark_price,
        "floating_profit_loss": "0",
        "delta": "-0.1",
    }


def test_current_stress_uses_option_size_for_naked_short_put(tmp_path, fake_client):
    cfg = make_config(tmp_path, option_strategy="naked_short", traded_collaterals=("USDC",))
    fake_client.positions = [
        _linear_put_position(
            "BTC_USDC-14APR30-63000-P",
            direction="sell",
            size="0.1",
            size_currency="0",
            mark_price="610",
        )
    ]

    result = compute_current_stress(cfg, fake_client, shocks=[Decimal("-0.20")])

    assert result.positions[0]["quantity"] == Decimal("0.1")
    assert Decimal(result.scenarios[0]["loss_usdc_total"]) == Decimal("-709.0")
    assert Decimal(result.scenarios[0]["loss_by_book_usdc"]["USDC"]) == Decimal("-709.0")


def test_current_stress_bull_put_spread_nets_long_put_protection(tmp_path, fake_client):
    cfg = make_config(tmp_path, option_strategy="bull_put_spread", traded_collaterals=("USDC",))
    fake_client.positions = [
        _linear_put_position(
            "BTC_USDC-14APR30-63000-P",
            direction="sell",
            size="0.1",
            mark_price="610",
        ),
        _linear_put_position(
            "BTC_USDC-14APR30-60000-P",
            direction="buy",
            size="0.1",
            mark_price="195",
        ),
    ]

    result = compute_current_stress(cfg, fake_client, shocks=[Decimal("-0.20")])

    assert Decimal(result.scenarios[0]["loss_usdc_total"]) == Decimal("-368.5")
    assert Decimal(result.scenarios[0]["loss_by_book_usdc"]["USDC"]) == Decimal("-368.5")
    assert Decimal(result.scenarios[0]["components_total_usdc"]["base_move_usdc"]) == Decimal("-258.5")
    assert Decimal(result.scenarios[0]["components_total_usdc"]["slippage_usdc"]) == Decimal("-110.0")


def test_current_stress_infers_linear_usdc_when_metadata_missing(tmp_path, fake_client, monkeypatch):
    cfg = make_config(tmp_path, option_strategy="naked_short", traded_collaterals=("USDC",))
    fake_client.positions = [
        _linear_put_position(
            "BTC_USDC-14APR30-63000-P",
            direction="sell",
            size="0.1",
            mark_price="610",
        )
    ]
    monkeypatch.setattr(fake_client, "get_instruments", lambda *args, **kwargs: [])

    result = compute_current_stress(cfg, fake_client, shocks=[Decimal("-0.20")])

    assert result.positions[0]["settlement_currency"] == "USDC"
    assert result.positions[0]["instrument_type"] == "linear"
    assert Decimal(result.scenarios[0]["loss_usdc_total"]) == Decimal("-709.0")
    assert Decimal(result.scenarios[0]["loss_by_book_usdc"]["USDC"]) == Decimal("-709.0")
