"""Client hardening: unsafe-path semantics, per-IP public pacing, cache single-flight."""

from __future__ import annotations

import threading

import pytest
import requests
from conftest import make_config
from test_client import FakeResponse, FakeSession, _auth_result, _error_body, _ok_body

import deribit_engine.client as client_module
from deribit_engine import exchange_throttle
from deribit_engine.client import DeribitClient
from deribit_engine.exceptions import ExchangeError, TransientExchangeError


@pytest.fixture(autouse=True)
def _reset_client_globals(monkeypatch):
    with client_module._AUTH_CACHE_LOCK:
        client_module._AUTH_TOKEN_CACHE.clear()
    client_module.reset_public_read_cache()
    exchange_throttle.reset_adaptive_backoff()
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda _s: None)
    yield
    with client_module._AUTH_CACHE_LOCK:
        client_module._AUTH_TOKEN_CACHE.clear()
    client_module.reset_public_read_cache()
    exchange_throttle.reset_adaptive_backoff()


def _client(tmp_path, session):
    return DeribitClient(make_config(tmp_path), session=session)


def _sell(client):
    return client.place_order(
        direction="sell",
        instrument_name="BTC-PERPETUAL",
        amount="1",
        label="trial-x",
        price="100",
    )


# ------------------------------------------------------------------
# FIX 1 — unsafe path never resends on ConnectionError
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ConnectionError("connection reset"),
        requests.exceptions.ConnectTimeout("connect timeout"),  # subclass of ConnectionError
        requests.exceptions.ReadTimeout("read timeout"),
    ],
)
def test_unsafe_methods_send_exactly_once_on_transport_failure(tmp_path, monkeypatch, exc):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    session = FakeSession(
        [FakeResponse(_auth_result()), FakeResponse(_ok_body({"order": {}}))],
        raise_on_calls=[None, exc],
    )
    client = _client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="reconcile required"):
        _sell(client)

    assert [c["json"]["method"] for c in session.calls] == ["public/auth", "private/sell"]


def test_unsafe_connection_error_message_names_method(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    session = FakeSession(
        [FakeResponse(_auth_result())],
        raise_on_calls=[None, requests.exceptions.ConnectionError("boom")],
    )
    client = _client(tmp_path, session)

    with pytest.raises(TransientExchangeError) as info:
        client.cancel_order("abc")

    assert str(info.value).startswith("private/cancel connection failed; reconcile required")


# ------------------------------------------------------------------
# FIX 2 — unsafe path widens adaptive pacing on 429 / 10028
# ------------------------------------------------------------------


def test_unsafe_http_429_widens_private_identity_interval(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    session = FakeSession(
        [FakeResponse(_auth_result()), FakeResponse({}, status_code=429, text="rate limited")],
    )
    client = _client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="rate limited"):
        _sell(client)

    assert exchange_throttle.adaptive_interval_seconds("id") == pytest.approx(0.20)
    assert len(session.calls) == 2


def test_unsafe_jsonrpc_too_many_requests_widens_private_identity_interval(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_error_body(code=10028, message="too_many_requests", data={"wait": 1})),
        ],
    )
    client = _client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="too_many_requests"):
        _sell(client)

    assert exchange_throttle.adaptive_interval_seconds("id") == pytest.approx(0.20)
    # Never resent.
    assert [c["json"]["method"] for c in session.calls] == ["public/auth", "private/sell"]


def test_unsafe_business_error_does_not_touch_pacing(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    session = FakeSession(
        [FakeResponse(_auth_result()), FakeResponse(_error_body(code=11044, message="not_enough_funds"))],
    )
    client = _client(tmp_path, session)

    with pytest.raises(ExchangeError, match="not_enough_funds"):
        _sell(client)

    assert exchange_throttle.adaptive_interval_seconds("id") == pytest.approx(0.10)


# ------------------------------------------------------------------
# FIX 3 — public/* paces against the host-wide key, private per client_id
# ------------------------------------------------------------------


def test_public_methods_pace_against_global_key(tmp_path, monkeypatch):
    paced: list[str | None] = []
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: paced.append(identity))
    session = FakeSession(
        [
            FakeResponse(_ok_body({"index_price": 1})),
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body([])),
        ]
    )
    client = _client(tmp_path, session)

    client.get_index_price("btc_usd")
    client.get_positions()

    # index_price → global; public/auth → global; private/get_positions → client_id.
    assert paced == [None, None, "id"]


def test_public_429_penalizes_global_key_not_client_id(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    session = FakeSession([FakeResponse({}, status_code=429, text="rate limited")] * 5)
    client = _client(tmp_path, session)

    with pytest.raises(TransientExchangeError):
        client.get_order_book("BTC-PERPETUAL")

    assert exchange_throttle.adaptive_interval_seconds(None) > 0.10
    assert exchange_throttle.adaptive_interval_seconds("id") == pytest.approx(0.10)


def test_private_429_penalizes_client_id_not_global_key(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    session = FakeSession([FakeResponse(_auth_result())] + [FakeResponse({}, status_code=429, text="x")] * 5)
    client = _client(tmp_path, session)

    with pytest.raises(TransientExchangeError):
        client.get_positions()

    assert exchange_throttle.adaptive_interval_seconds("id") > 0.10
    assert exchange_throttle.adaptive_interval_seconds(None) == pytest.approx(0.10)


def test_public_pacing_shares_one_slot_across_client_ids(tmp_path, monkeypatch):
    """Two accounts on one host must serialize public reads (per-IP quota)."""
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.05")
    exchange_throttle._global_last_request_monotonic = 0.0
    exchange_throttle._identity_last_request_monotonic.clear()
    sleeps: list[float] = []
    monotonic = iter([0.05, 0.06, 0.07, 0.08, 0.09, 0.10])

    class _FakeTime:
        @staticmethod
        def sleep(s):
            sleeps.append(s)

        @staticmethod
        def monotonic():
            return next(monotonic)

    # Patch the throttle module's ``time`` only, so the client's cache clock is untouched.
    monkeypatch.setattr("deribit_engine.exchange_throttle.time", _FakeTime)

    a = DeribitClient(make_config(tmp_path, client_id="acct-a"), session=FakeSession([FakeResponse(_ok_body({}))]))
    b = DeribitClient(make_config(tmp_path, client_id="acct-b"), session=FakeSession([FakeResponse(_ok_body({}))]))

    a.get_index_price("btc_usd")
    b.get_index_price("eth_usd")

    # Second public call from a *different* client_id still had to wait for the global slot.
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.04, abs=0.001)


# ------------------------------------------------------------------
# FIX 8 — public read cache single-flight
# ------------------------------------------------------------------


def test_cached_public_read_single_flight_across_threads(monkeypatch):
    monkeypatch.setattr(client_module.public_cache, "read", lambda key, ttl: (False, None))
    monkeypatch.setattr(client_module.public_cache, "write", lambda key, value: None)
    calls = {"n": 0}
    entered = threading.Event()
    release = threading.Event()

    def loader():
        calls["n"] += 1
        entered.set()
        release.wait(timeout=5)
        return {"v": calls["n"]}

    results: list[dict] = []

    def worker():
        results.append(client_module._cached_public_read("sf:key", 60.0, loader))

    first = threading.Thread(target=worker)
    first.start()
    assert entered.wait(timeout=5)
    others = [threading.Thread(target=worker) for _ in range(4)]
    for t in others:
        t.start()
    release.set()
    first.join(timeout=5)
    for t in others:
        t.join(timeout=5)

    assert calls["n"] == 1
    assert len(results) == 5
    assert all(r == {"v": 1} for r in results)
    assert client_module._PUBLIC_READ_INFLIGHT == {}


def test_cached_public_read_releases_flight_on_loader_error(monkeypatch):
    monkeypatch.setattr(client_module.public_cache, "read", lambda key, ttl: (False, None))
    monkeypatch.setattr(client_module.public_cache, "write", lambda key, value: None)

    def boom():
        raise TransientExchangeError("down")

    with pytest.raises(TransientExchangeError):
        client_module._cached_public_read("sf:err", 60.0, boom)
    assert client_module._PUBLIC_READ_INFLIGHT == {}

    # A later caller is not deadlocked and loads fresh.
    assert client_module._cached_public_read("sf:err", 60.0, lambda: {"ok": True}) == {"ok": True}


def test_cached_public_read_different_keys_do_not_block_each_other(monkeypatch):
    monkeypatch.setattr(client_module.public_cache, "read", lambda key, ttl: (False, None))
    monkeypatch.setattr(client_module.public_cache, "write", lambda key, value: None)
    a_started = threading.Event()
    a_release = threading.Event()

    def slow_a():
        a_started.set()
        a_release.wait(timeout=5)
        return "a"

    out: dict[str, str] = {}
    ta = threading.Thread(target=lambda: out.__setitem__("a", client_module._cached_public_read("k:a", 60.0, slow_a)))
    ta.start()
    assert a_started.wait(timeout=5)
    tb = threading.Thread(
        target=lambda: out.__setitem__("b", client_module._cached_public_read("k:b", 60.0, lambda: "b"))
    )
    tb.start()
    tb.join(timeout=5)
    assert out.get("b") == "b", "key b must not wait on key a's in-flight load"
    a_release.set()
    ta.join(timeout=5)
    assert out.get("a") == "a"


# ------------------------------------------------------------------
# FIX 10 — get_instrument uses the TTL cache
# ------------------------------------------------------------------


def test_get_instrument_uses_process_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_INSTRUMENTS_CACHE_TTL_SEC", "300")
    row = {"instrument_name": "BTC_USDC-29MAY26-68000-P", "tick_size": 0.1}
    session = FakeSession([FakeResponse(_ok_body(row)), FakeResponse(_ok_body({"tick_size": 99}))])
    client = _client(tmp_path, session)

    first = client.get_instrument("BTC_USDC-29MAY26-68000-P")
    second = client.get_instrument("BTC_USDC-29MAY26-68000-P")
    other = client.get_instrument("ETH_USDC-29MAY26-2000-P")

    assert first == row
    assert second == row
    assert other == {"tick_size": 99}
    calls = [
        c["json"]["params"]["instrument_name"] for c in session.calls if c["json"]["method"] == "public/get_instrument"
    ]
    assert calls == ["BTC_USDC-29MAY26-68000-P", "ETH_USDC-29MAY26-2000-P"]


def test_get_instrument_cache_disabled_with_zero_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_INSTRUMENTS_CACHE_TTL_SEC", "0")
    session = FakeSession([FakeResponse(_ok_body({"a": 1})), FakeResponse(_ok_body({"a": 2}))])
    client = _client(tmp_path, session)

    assert client.get_instrument("X") == {"a": 1}
    assert client.get_instrument("X") == {"a": 2}
