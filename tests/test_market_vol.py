from __future__ import annotations

from decimal import Decimal

from deribit_engine.frontend_server.market_vol import (
    fetch_index_price_change_24h_pct,
    fetch_iv_rank_snapshot,
)


class _ChartClient:
    def get_index_chart_data(self, index_name, *, range_name="1d"):
        if range_name != "1d":
            return []
        if index_name == "btc_usd":
            return [[1, "100000"], [2, "102500"]]
        if index_name == "eth_usd":
            return [[1, "3000"], [2, "2910"]]
        return []


class _FakeClient:
    def get_volatility_index_data(self, currency, *, start_timestamp, end_timestamp, resolution="1D"):
        assert resolution == "1D"
        if currency.upper() == "BTC":
            return {
                "data": [
                    [1_700_000_000_000, 40.0, 42.0, 38.0, 40.0],
                    [1_700_086_400_000, 50.0, 52.0, 48.0, 50.0],
                    [1_700_172_800_000, 60.0, 62.0, 58.0, 60.0],
                ]
            }
        return {
            "data": [
                [1_700_000_000_000, 30.0, 32.0, 28.0, 30.0],
                [1_700_086_400_000, 45.0, 47.0, 43.0, 45.0],
            ]
        }


def test_fetch_index_price_change_24h_pct_from_chart():
    payload = fetch_index_price_change_24h_pct(_ChartClient())
    assert payload["BTC"] == "2.5"
    assert Decimal(payload["ETH"]) == Decimal("-3")


def test_fetch_iv_rank_snapshot_returns_per_currency_metrics():
    payload = fetch_iv_rank_snapshot(_FakeClient(), lookback_days=3)
    assert payload["iv_rank_lookback_days"] == 3
    assert payload["dvol"]["BTC"] == "60"
    assert payload["dvol"]["ETH"] == "45"
    assert payload["iv_rank_pct"]["BTC"] == "91.7"
    assert payload["iv_rank_pct"]["ETH"] == "89.5"
    assert Decimal(payload["iv_rank"]["BTC"]) == Decimal("0.9166")
    assert Decimal(payload["iv_rank"]["ETH"]) == Decimal("0.8947")
