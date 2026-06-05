from __future__ import annotations

from decimal import Decimal

from deribit_engine.fees import (
    apply_trading_fee_discount,
    inverse_option_fee_native_total,
    option_trade_fee_native,
)


def test_apply_trading_fee_discount_zero_unchanged() -> None:
    assert apply_trading_fee_discount(Decimal("0.00003"), Decimal("0")) == Decimal("0.00003")


def test_apply_trading_fee_discount_ten_percent() -> None:
    assert apply_trading_fee_discount(Decimal("0.00003"), Decimal("0.10")) == Decimal("0.000027")


def test_inverse_option_fee_native_total_pat_sizing() -> None:
    """PAT covered_call: qty=0.1, premium at fee cap → 0.00003 pre-discount, 0.000027 after."""
    fee = inverse_option_fee_native_total(
        premium=Decimal("0.013"),
        quantity=Decimal("0.1"),
        fee_rate=Decimal("0.0003"),
        fee_cap_rate=Decimal("0.125"),
        fee_discount_rate=Decimal("0.10"),
    )
    assert fee == Decimal("0.000027")


def test_option_trade_fee_native_inverse_with_discount() -> None:
    fee = option_trade_fee_native(
        index_price=Decimal("70000"),
        premium=Decimal("0.013"),
        quantity=Decimal("0.1"),
        fee_rate=Decimal("0.0003"),
        fee_cap_rate=Decimal("0.125"),
        quote_currency="",
        settlement_currency="BTC",
        fee_discount_rate=Decimal("0.10"),
    )
    assert fee == Decimal("0.000027")


def test_load_config_option_fee_discount_anchor_defaults_to_registration(tmp_path) -> None:
    from deribit_engine.config import load_config

    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=covered_call",
                "STATE_FILE=.state/x.json",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(env, require_private=False)
    assert cfg.option_fee_discount_anchor == "registration"


def test_load_config_option_fee_discount_rate(tmp_path) -> None:
    from deribit_engine.config import load_config

    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=mainnet",
                "OPTION_STRATEGY=covered_call",
                "OPTION_FEE_DISCOUNT_RATE=0.10",
                "STATE_FILE=.state/x.json",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(env, require_private=False)
    assert cfg.option_fee_discount_rate == Decimal("0.10")
