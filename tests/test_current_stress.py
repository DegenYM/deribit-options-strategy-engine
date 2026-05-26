from __future__ import annotations

from decimal import Decimal

from conftest import make_config

from deribit_engine.current_stress import compute_current_stress, compute_stress_from_prefetch
from deribit_engine.engine import ExchangePrefetch
from deribit_engine.models import AccountSummary, OptionInstrument, Position


def _prefetch_from_fake_client(fake_client, cfg) -> ExchangePrefetch:
    summaries = {
        s.currency: s for s in (AccountSummary.from_api(x) for x in fake_client.get_account_summaries()) if s.currency
    }
    positions = [Position.from_api(x) for x in fake_client.positions if isinstance(x, dict)]
    option_positions = [p for p in positions if p.kind == "option"]
    markets_by_currency: dict[str, list[OptionInstrument]] = {}
    for currency in ("BTC", "ETH", "USDC"):
        markets_by_currency[currency] = [
            OptionInstrument.from_api(row)
            for row in fake_client.get_instruments(currency, kind="option", expired=False)
            if isinstance(row, dict)
        ]
    return ExchangePrefetch(
        summaries=summaries,
        open_orders=[],
        positions=positions,
        option_positions=option_positions,
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency=markets_by_currency,
    )


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

    assert Decimal(result.scenarios[0]["loss_usdc_total"]) == Decimal("-709.0")
    assert Decimal(result.scenarios[0]["loss_by_book_usdc"]["USDC"]) == Decimal("-709.0")


def test_compute_stress_from_prefetch_matches_live_client(tmp_path, fake_client):
    cfg = make_config(tmp_path, option_strategy="naked_short", traded_collaterals=("USDC",))
    fake_client.positions = [
        _linear_put_position(
            "BTC_USDC-14APR30-63000-P",
            direction="sell",
            size="0.1",
            mark_price="610",
        )
    ]
    shocks = [Decimal("-0.20")]
    prefetch = _prefetch_from_fake_client(fake_client, cfg)

    live = compute_current_stress(cfg, fake_client, shocks=shocks)
    cached = compute_stress_from_prefetch(cfg, prefetch, shocks=shocks, client=fake_client)

    assert cached.scenarios == live.scenarios
    assert cached.positions == live.positions
    assert cached.equity_usdc_by_book == live.equity_usdc_by_book


def test_compute_stress_from_prefetch_does_not_refetch_account_summaries(tmp_path, fake_client):
    cfg = make_config(tmp_path, option_strategy="naked_short", traded_collaterals=("USDC",))
    fake_client.positions = [
        _linear_put_position(
            "BTC_USDC-14APR30-63000-P",
            direction="sell",
            size="0.1",
            mark_price="610",
        )
    ]
    prefetch = _prefetch_from_fake_client(fake_client, cfg)
    calls = {"summaries": 0, "positions": 0}

    def _blocked_summaries(*args, **kwargs):
        calls["summaries"] += 1
        raise AssertionError("get_account_summaries should not run when prefetch is supplied")

    def _blocked_positions(*args, **kwargs):
        calls["positions"] += 1
        raise AssertionError("get_positions should not run when prefetch is supplied")

    fake_client.get_account_summaries = _blocked_summaries
    fake_client.get_positions = _blocked_positions

    result = compute_stress_from_prefetch(
        cfg,
        prefetch,
        shocks=[Decimal("-0.20")],
        client=fake_client,
    )

    assert calls == {"summaries": 0, "positions": 0}
    assert Decimal(result.scenarios[0]["loss_usdc_total"]) == Decimal("-709.0")
