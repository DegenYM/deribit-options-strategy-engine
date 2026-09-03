"""Cross-process sharing of Deribit public reads.

Deribit meters unauthenticated requests per IP, and every investor process on
this host asks for identical public data, so a read paid for by one process must
satisfy the others.
"""

from conftest import make_config

import deribit_engine.client as client_module
from deribit_engine import public_cache
from deribit_engine.client import DeribitClient


class _CountingClient(DeribitClient):
    """Counts HTTP-level calls without touching the network."""

    def __init__(self, config, responses):
        super().__init__(config)
        self.responses = responses
        self.requests: list[str] = []

    def _request(self, method_name, *, params=None, **_kwargs):
        self.requests.append(method_name)
        return self.responses[method_name]


def _fresh(tmp_path, monkeypatch, responses):
    """A client that shares tmp_path's store but has an empty in-process cache."""
    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE_PATH", str(tmp_path / "public.db"))
    public_cache.reset_for_tests(str(tmp_path / "public.db"))
    client_module.reset_public_read_cache()
    return _CountingClient(make_config(tmp_path), responses)


def test_book_summary_is_shared_between_processes(tmp_path, monkeypatch):
    rows = [{"instrument_name": "BTC-1", "bid_price": "0.01"}]
    responses = {"public/get_book_summary_by_currency": rows}

    first = _fresh(tmp_path, monkeypatch, responses)
    assert first.get_book_summary_by_currency("BTC") == rows
    assert first.requests == ["public/get_book_summary_by_currency"]

    # A second process: same shared store, cold in-process cache.
    second = _fresh(tmp_path, monkeypatch, responses)
    assert second.get_book_summary_by_currency("BTC") == rows
    assert second.requests == [], "second process should be served from the shared store"


def test_order_book_and_instruments_are_shared(tmp_path, monkeypatch):
    responses = {
        "public/get_order_book": {"instrument_name": "BTC-1", "best_bid_price": 0.01},
        "public/get_instruments": [{"instrument_name": "BTC-1"}],
    }

    first = _fresh(tmp_path, monkeypatch, responses)
    first.get_order_book("BTC-1")
    first.get_instruments("BTC")
    assert sorted(first.requests) == ["public/get_instruments", "public/get_order_book"]

    second = _fresh(tmp_path, monkeypatch, responses)
    assert second.get_order_book("BTC-1") == responses["public/get_order_book"]
    assert second.get_instruments("BTC") == responses["public/get_instruments"]
    assert second.requests == []


def test_expired_shared_entry_refetches(tmp_path, monkeypatch):
    responses = {"public/get_book_summary_by_currency": [{"instrument_name": "BTC-1"}]}
    first = _fresh(tmp_path, monkeypatch, responses)
    first.get_book_summary_by_currency("BTC")

    monkeypatch.setenv("DERIBIT_BOOK_SUMMARY_CACHE_TTL_SEC", "0.0001")
    second = _fresh(tmp_path, monkeypatch, responses)
    monkeypatch.setenv("DERIBIT_BOOK_SUMMARY_CACHE_TTL_SEC", "0.0001")
    second.get_book_summary_by_currency("BTC")

    assert second.requests == ["public/get_book_summary_by_currency"]


def test_cache_can_be_disabled(tmp_path, monkeypatch):
    responses = {"public/get_book_summary_by_currency": [{"instrument_name": "BTC-1"}]}
    first = _fresh(tmp_path, monkeypatch, responses)
    first.get_book_summary_by_currency("BTC")

    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE", "0")
    second = _fresh(tmp_path, monkeypatch, responses)
    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE", "0")
    second.get_book_summary_by_currency("BTC")

    assert second.requests == ["public/get_book_summary_by_currency"]


def test_store_failure_falls_through_to_network(tmp_path, monkeypatch):
    """A broken cache must never block a read."""
    responses = {"public/get_book_summary_by_currency": [{"instrument_name": "BTC-1"}]}
    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE_PATH", str(tmp_path / "nope" / "x.db"))
    public_cache.reset_for_tests(str(tmp_path / "public.db"))
    monkeypatch.setattr(public_cache, "_connect", lambda: None)
    client_module.reset_public_read_cache()
    c = _CountingClient(make_config(tmp_path), responses)

    assert c.get_book_summary_by_currency("BTC") == responses["public/get_book_summary_by_currency"]
    assert c.requests == ["public/get_book_summary_by_currency"]


def test_inverse_native_skips_the_usdc_option_chain(tmp_path, monkeypatch):
    """inverse_native rejects every USDC-quoted+settled market, so fetching the
    whole USDC chain only to discard it wastes a per-IP public request."""
    from conftest import FakeClient

    from deribit_engine.engine import DeribitOptionTrialBot

    class _TrackingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.instrument_calls: list[tuple[str, str]] = []

        def get_instruments(self, currency, *, kind="option", expired=False):
            self.instrument_calls.append((currency.upper(), kind))
            if currency.upper() == "USDC" and kind == "option":
                return [
                    {
                        "instrument_name": "BTC_USDC-28MAR25-90000-C",
                        "base_currency": "BTC",
                        "quote_currency": "USDC",
                        "settlement_currency": "USDC",
                        "option_type": "call",
                        "strike": 90000,
                        "expiration_timestamp": 1,
                        "creation_timestamp": 0,
                        "is_active": True,
                        "contract_size": 1,
                        "min_trade_amount": 1,
                        "tick_size": 0.0001,
                    }
                ]
            return super().get_instruments(currency, kind=kind, expired=expired)

    client = _TrackingClient()
    engine = DeribitOptionTrialBot(
        make_config(tmp_path, option_markets_profile="inverse_native", managed_currencies=("BTC", "ETH")),
        client,
    )

    engine._load_supported_option_markets()

    assert ("USDC", "option") not in client.instrument_calls
    assert ("BTC", "option") in client.instrument_calls
    assert ("ETH", "option") in client.instrument_calls


def test_non_inverse_profile_still_loads_the_usdc_chain(tmp_path, monkeypatch):
    from conftest import FakeClient

    from deribit_engine.engine import DeribitOptionTrialBot

    class _TrackingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.instrument_calls: list[tuple[str, str]] = []

        def get_instruments(self, currency, *, kind="option", expired=False):
            self.instrument_calls.append((currency.upper(), kind))
            return super().get_instruments(currency, kind=kind, expired=expired)

    client = _TrackingClient()
    engine = DeribitOptionTrialBot(
        make_config(tmp_path, option_markets_profile="all", managed_currencies=("BTC", "ETH")),
        client,
    )

    engine._load_supported_option_markets()

    assert ("USDC", "option") in client.instrument_calls
