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


def _sqlite_rows(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "public.db") as conn:
        return conn.execute("SELECT key, stored_ms FROM public_reads ORDER BY stored_ms").fetchall()


def test_eviction_drops_rows_older_than_max_age(tmp_path, monkeypatch):
    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE_PATH", str(tmp_path / "public.db"))
    monkeypatch.setenv("PUBLIC_CACHE_MAX_AGE_SECONDS", "3600")
    public_cache.reset_for_tests(str(tmp_path / "public.db"))

    public_cache.write("fresh", {"v": 1})
    # Backdate one row to two hours ago.
    import sqlite3

    with sqlite3.connect(tmp_path / "public.db") as conn:
        conn.execute(
            "INSERT INTO public_reads (key, stored_ms, payload) VALUES ('old', ?, '{}')",
            (int(__import__("time").time() * 1000) - 2 * 3600 * 1000,),
        )
    assert public_cache.row_count() == 2

    conn = public_cache._connect()
    assert public_cache.evict(conn) == 1
    assert [row[0] for row in _sqlite_rows(tmp_path)] == ["fresh"]


def test_eviction_trims_to_row_cap_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE_PATH", str(tmp_path / "public.db"))
    monkeypatch.setenv("PUBLIC_CACHE_MAX_ROWS", "100")  # clamped to the minimum of 100
    public_cache.reset_for_tests(str(tmp_path / "public.db"))
    import sqlite3

    conn = public_cache._connect()
    now_ms = int(__import__("time").time() * 1000)
    with sqlite3.connect(tmp_path / "public.db") as raw:
        raw.executemany(
            "INSERT INTO public_reads (key, stored_ms, payload) VALUES (?, ?, '{}')",
            [
                (
                    f"k{i:04d}",
                    now_ms - (150 - i) * 1000,
                )
                for i in range(150)
            ],
        )
    assert public_cache.row_count() == 150

    assert public_cache.evict(conn) == 50
    keys = [row[0] for row in _sqlite_rows(tmp_path)]
    assert len(keys) == 100
    assert keys[0] == "k0050"  # the 50 oldest (k0000..k0049) were removed
    assert keys[-1] == "k0149"


def test_write_runs_eviction_every_n_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("DERIBIT_PUBLIC_CACHE_PATH", str(tmp_path / "public.db"))
    public_cache.reset_for_tests(str(tmp_path / "public.db"))
    monkeypatch.setattr(public_cache, "_EVICT_EVERY_N_WRITES", 5)
    calls: list[int] = []
    real_evict = public_cache.evict

    def _spy(conn, *, now_ms=None):
        calls.append(1)
        return real_evict(conn, now_ms=now_ms)

    monkeypatch.setattr(public_cache, "evict", _spy)
    for i in range(12):
        public_cache.write(f"k{i}", {"i": i})
    assert len(calls) == 2  # writes 5 and 10
    assert public_cache.row_count() == 12


def test_env_knobs_have_floors_and_defaults(monkeypatch):
    monkeypatch.delenv("PUBLIC_CACHE_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("PUBLIC_CACHE_MAX_ROWS", raising=False)
    assert public_cache.max_age_seconds() == public_cache.DEFAULT_MAX_AGE_SECONDS == 86_400
    assert public_cache.max_rows() == public_cache.DEFAULT_MAX_ROWS == 5_000
    monkeypatch.setenv("PUBLIC_CACHE_MAX_AGE_SECONDS", "1")
    monkeypatch.setenv("PUBLIC_CACHE_MAX_ROWS", "abc")
    assert public_cache.max_age_seconds() == 60
    assert public_cache.max_rows() == 5_000


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
