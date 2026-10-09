from __future__ import annotations

from decimal import Decimal
from typing import Any

from deribit_engine.frontend_server.aggregation import (
    UNATTRIBUTED_SPOT_PNL_CACHE_KEY,
    _aggregate_unattributed_spot_pnl,
    attach_cached_premium_sweep_fill_stats,
)
from deribit_engine.unattributed_spot_pnl import (
    compute_unattributed_spot_pnl,
    is_strategy_label,
    label_group_id,
    unattributed_spot_pnl,
)

PREFIX = "eugene_covered_call"


def _trade(
    ts: int,
    instrument: str,
    direction: str,
    amount: str,
    price: str,
    label: str = "",
    *,
    fee: str = "0",
    fee_currency: str = "USDC",
) -> dict[str, Any]:
    return {
        "trade_id": f"{instrument}-{ts}-{direction}",
        "timestamp": ts,
        "instrument_name": instrument,
        "direction": direction,
        "amount": amount,
        "price": price,
        "label": label,
        "fee": fee,
        "fee_currency": fee_currency,
    }


def test_label_helpers() -> None:
    assert is_strategy_label(f"{PREFIX}-csp-restore-btc-0033", PREFIX)
    assert not is_strategy_label("operator-unwind-premature-csp-swap", PREFIX)
    assert not is_strategy_label("", PREFIX)
    assert label_group_id("overbuy-unwind-0033") == "0033"
    assert label_group_id(f"{PREFIX}-spread-btc-0022-short-spot-restore") == "0022"
    assert label_group_id("operator-unwind-premature-csp-swap") is None


def test_operator_unwind_matches_runaway_csp_swap_buys() -> None:
    trades = [
        _trade(1, "BTC_USDC", "buy", "0.05", "77000", f"{PREFIX}-csp-premium-swap-btc-0027"),
        _trade(2, "BTC_USDC", "buy", "0.05", "78000", f"{PREFIX}-csp-premium-swap-btc-0027"),
        _trade(3, "BTC_USDC", "sell", "0.1", "80000", "operator-unwind-premature-csp-swap"),
    ]
    out = compute_unattributed_spot_pnl(trades, PREFIX)
    # Basis = average 77500 → 0.1 × 2500.
    assert Decimal(out["by_book"]["BTC"]) == Decimal("250")
    assert Decimal(out["total_usd"]) == Decimal("250")
    assert out["by_label"]["operator-unwind-premature-csp-swap"]["trades"] == 1
    assert out["events"][0]["ts_ms"] == 3


def test_overbuy_unwind_uses_same_group_restore_basis() -> None:
    trades = [
        _trade(1, "BTC_USDC", "buy", "0.1", "70000", f"{PREFIX}-csp-premium-swap-btc-0027"),
        _trade(2, "BTC_USDC", "buy", "0.02", "76963", f"{PREFIX}-csp-restore-btc-0033"),
        _trade(3, "BTC_USDC", "sell", "0.02", "77255", "overbuy-unwind-0033"),
    ]
    out = compute_unattributed_spot_pnl(trades, PREFIX)
    assert Decimal(out["by_book"]["BTC"]) == Decimal("5.84")


def test_strategy_labeled_fills_are_journal_owned() -> None:
    trades = [
        _trade(1, "BTC_USDT", "sell", "0.0965", "79677", f"{PREFIX}-spread-btc-0022-short-spot-exit"),
        _trade(2, "BTC_USDC", "buy", "0.1", "78567", f"{PREFIX}-spread-btc-0022-short-spot-restore"),
    ]
    out = compute_unattributed_spot_pnl(trades, PREFIX)
    assert out["trade_count"] == 0
    assert Decimal(out["total_usd"]) == 0


def test_unmatched_principal_sale_is_zero_pnl() -> None:
    out = compute_unattributed_spot_pnl(
        [_trade(1, "BTC_USDT", "sell", "0.0002", "73100")],
        PREFIX,
    )
    assert Decimal(out["total_usd"]) == 0
    assert Decimal(out["unmatched_native_sold"]["BTC"]) == Decimal("0.0002")


def test_manual_buy_then_sell_and_open_lots() -> None:
    trades = [
        _trade(1, "ETH_USDC", "buy", "1", "2000"),
        _trade(2, "ETH_USDC", "sell", "0.4", "2100"),
    ]
    out = compute_unattributed_spot_pnl(trades, PREFIX)
    assert Decimal(out["by_book"]["ETH"]) == Decimal("40")
    assert Decimal(out["open_unattributed_native"]["ETH"]) == Decimal("0.6")


def test_sell_never_matches_later_buys() -> None:
    trades = [
        _trade(1, "ETH_USDC", "sell", "0.5", "2500", "operator-unwind-premature-csp-swap"),
        _trade(2, "ETH_USDC", "buy", "0.5", "2000", f"{PREFIX}-csp-premium-swap-eth-0025"),
    ]
    out = compute_unattributed_spot_pnl(trades, PREFIX)
    assert Decimal(out["total_usd"]) == 0
    assert Decimal(out["unmatched_native_sold"]["ETH"]) == Decimal("0.5")


def test_stable_conversion_and_fees() -> None:
    trades = [
        _trade(1, "USDC_USDT", "buy", "1000", "1.0001"),
        _trade(2, "BTC_USDC", "buy", "0.01", "70000", "manual"),
        _trade(3, "BTC_USDC", "sell", "0.01", "71000", "manual", fee="1", fee_currency="USDC"),
    ]
    out = compute_unattributed_spot_pnl(trades, PREFIX)
    assert Decimal(out["by_book"]["USDC"]) == Decimal("-0.1")
    assert Decimal(out["by_book"]["BTC"]) == Decimal("9")


class _FakeClient:
    def __init__(self, trades_by_currency: dict[str, list[dict[str, Any]]]) -> None:
        self.trades_by_currency = trades_by_currency
        self.calls = 0

    def get_user_trades_by_currency(self, currency: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        trades = self.trades_by_currency.get(currency, [])
        return {"trades": list(trades), "has_more": False}


def test_unattributed_spot_pnl_fetches_dedupes_and_caches() -> None:
    sell = _trade(2, "BTC_USDC", "sell", "0.01", "80000", "operator-unwind-premature-csp-swap")
    buy = _trade(1, "BTC_USDC", "buy", "0.01", "79000", f"{PREFIX}-csp-premium-swap-btc-0027")
    client = _FakeClient({"BTC": [buy, sell]})
    out = unattributed_spot_pnl(client, PREFIX)
    assert out is not None
    # recent + historical pages return the same fills; trade_id de-dupes them.
    assert Decimal(out["total_usd"]) == Decimal("10")
    calls = client.calls
    assert unattributed_spot_pnl(client, PREFIX) is out
    assert client.calls == calls


def test_unattributed_spot_pnl_none_without_fills() -> None:
    assert unattributed_spot_pnl(_FakeClient({}), PREFIX) is None


def test_aggregate_unattributed_spot_pnl_sums_logins() -> None:
    a = compute_unattributed_spot_pnl(
        [
            _trade(1, "BTC_USDC", "buy", "0.01", "70000"),
            _trade(3, "BTC_USDC", "sell", "0.01", "71000"),
        ],
        PREFIX,
    )
    b = compute_unattributed_spot_pnl(
        [
            _trade(2, "ETH_USDC", "buy", "1", "2000"),
            _trade(4, "ETH_USDC", "sell", "1", "1990"),
        ],
        PREFIX,
    )
    assert _aggregate_unattributed_spot_pnl([{"unattributed_spot_pnl": a}]) is a
    merged = _aggregate_unattributed_spot_pnl([{"unattributed_spot_pnl": a}, {"unattributed_spot_pnl": b}, {}])
    assert merged is not None
    assert Decimal(merged["total_usd"]) == Decimal("0")
    assert Decimal(merged["by_book"]["BTC"]) == Decimal("10")
    assert Decimal(merged["by_book"]["ETH"]) == Decimal("-10")
    assert [e["ts_ms"] for e in merged["events"]] == [3, 4]
    assert merged["by_label"]["(unlabeled)"]["trades"] == 2
    assert _aggregate_unattributed_spot_pnl([{}]) is None


def test_attach_cached_keeps_unattributed_spot_pnl() -> None:
    class _Cache:
        def __init__(self) -> None:
            self.store: dict[str, Any] = {}

        def seed(self, key: str, value: Any) -> None:
            self.store[key] = value

        def get_stale(self, key: str) -> Any:
            return self.store.get(key)

    cache = _Cache()
    row = {"total_usd": "12.5", "events": []}
    attach_cached_premium_sweep_fill_stats({"unattributed_spot_pnl": row}, cache)  # type: ignore[arg-type]
    assert cache.store[UNATTRIBUTED_SPOT_PNL_CACHE_KEY] == row
    out = attach_cached_premium_sweep_fill_stats({"portfolio": {}}, cache)  # type: ignore[arg-type]
    assert out["unattributed_spot_pnl"] == row
