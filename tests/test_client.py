from __future__ import annotations

import pytest
import requests
from conftest import make_config

from deribit_engine.client import DeribitClient
from deribit_engine.exceptions import ExchangeError, TransientExchangeError


@pytest.fixture(autouse=True)
def _disable_exchange_throttle(monkeypatch):
    from deribit_engine.client import _AUTH_CACHE_LOCK, _AUTH_TOKEN_CACHE, reset_public_read_cache

    with _AUTH_CACHE_LOCK:
        _AUTH_TOKEN_CACHE.clear()
    reset_public_read_cache()
    monkeypatch.setattr("deribit_engine.client.pace_exchange_request", lambda identity=None: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0")
    yield
    with _AUTH_CACHE_LOCK:
        _AUTH_TOKEN_CACHE.clear()
    reset_public_read_cache()


class FakeResponse:
    def __init__(self, payload, *, status_code=200, text="", headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _ok_body(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def _error_body(*, code, message, data=None):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": code, "message": message, "data": data},
    }


def _auth_result(**overrides):
    base = {
        "access_token": "token-a",
        "refresh_token": "refresh-a",
        "expires_in": 900,
        "token_type": "bearer",
        "scope": "connection",
    }
    base.update(overrides)
    return _ok_body(base)


class FakeSession:
    def __init__(self, responses, *, raise_on_calls=None):
        self.responses = list(responses)
        self.raise_on_calls = list(raise_on_calls or [])
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        call = {"url": url, "json": json, "headers": headers or {}, "timeout": timeout}
        self.calls.append(call)
        idx = len(self.calls) - 1
        if idx < len(self.raise_on_calls) and self.raise_on_calls[idx] is not None:
            raise self.raise_on_calls[idx]
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _make_client(tmp_path, session):
    return DeribitClient(make_config(tmp_path), session=session)


# ------------------------------------------------------------------
# Idempotent path (reads)
# ------------------------------------------------------------------


def test_get_positions_omits_kind_filter_when_kind_is_any(tmp_path):
    session = FakeSession([FakeResponse(_auth_result()), FakeResponse(_ok_body([]))])
    client = _make_client(tmp_path, session)

    client.get_positions(currency="any", kind="any")

    data_calls = [c for c in session.calls if c["json"]["method"] == "private/get_positions"]
    assert len(data_calls) == 1
    params = data_calls[0]["json"]["params"]
    assert params.get("currency") == "any"
    assert "kind" not in params


def test_idempotent_request_sends_jsonrpc_post(tmp_path):
    session = FakeSession([FakeResponse(_ok_body({"instrument_name": "BTC-PERPETUAL"}))])
    client = _make_client(tmp_path, session)

    result = client.get_order_book("BTC-PERPETUAL")

    assert result == {"instrument_name": "BTC-PERPETUAL"}
    assert len(session.calls) == 1
    call = session.calls[0]
    body = call["json"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "public/get_order_book"
    assert body["params"]["instrument_name"] == "BTC-PERPETUAL"
    assert "instrument_name=" not in call["url"]


def test_get_instrument_uses_exact_public_endpoint(tmp_path):
    session = FakeSession([FakeResponse(_ok_body({"instrument_name": "BTC_USDC-29MAY26-68000-P"}))])
    client = _make_client(tmp_path, session)

    result = client.get_instrument("BTC_USDC-29MAY26-68000-P")

    assert result == {"instrument_name": "BTC_USDC-29MAY26-68000-P"}
    body = session.calls[0]["json"]
    assert body["method"] == "public/get_instrument"
    assert body["params"]["instrument_name"] == "BTC_USDC-29MAY26-68000-P"


def test_backoff_with_jitter_within_bounds():
    for base in (0.5, 1.0, 2.0):
        for _ in range(50):
            jittered = DeribitClient._backoff_with_jitter(base)
            assert base * 0.5 <= jittered <= base
    assert DeribitClient._backoff_with_jitter(0) == 0.0


def test_rate_limit_feedback_widens_then_recovers_adaptive_interval(tmp_path, monkeypatch):
    from deribit_engine import exchange_throttle

    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda _s: None)
    monkeypatch.setenv("DERIBIT_MIN_REQUEST_INTERVAL_SEC", "0.10")
    monkeypatch.setenv("DERIBIT_MAX_REQUEST_INTERVAL_SEC", "0.50")
    exchange_throttle.reset_adaptive_backoff()
    try:
        # Exhaust retries with 429s so no success note resets the penalty.
        session = FakeSession([FakeResponse({}, status_code=429, text="rate limited") for _ in range(4)])
        client = _make_client(tmp_path, session)
        with pytest.raises(TransientExchangeError):
            client.get_order_book("BTC-PERPETUAL")
        assert exchange_throttle.adaptive_interval_seconds("id") > 0.10

        # A subsequent success decays the penalty back toward base.
        ok_session = FakeSession([FakeResponse(_ok_body({"ok": True}))])
        client2 = _make_client(tmp_path, ok_session)
        client2.get_order_book("BTC-PERPETUAL")
        assert exchange_throttle.adaptive_interval_seconds("id") < 0.50
    finally:
        exchange_throttle.reset_adaptive_backoff()


def test_idempotent_request_retries_on_retryable_http(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse({}, status_code=522, text="timeout"),
            FakeResponse(_ok_body({"ok": True})),
        ]
    )
    client = _make_client(tmp_path, session)

    result = client.get_order_book("ETH_USDC-24APR26-1700-P")

    assert result == {"ok": True}
    assert len(session.calls) == 2


def test_idempotent_request_raises_after_retry_exhaustion(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda _s: None)
    session = FakeSession([FakeResponse({}, status_code=522, text="timeout")] * 4)
    client = _make_client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="HTTP 522"):
        client.get_order_book("ETH_USDC-24APR26-1700-P")


def test_idempotent_request_respects_retry_after_header(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda s: sleeps.append(s))
    session = FakeSession(
        [
            FakeResponse({}, status_code=429, text="slow down", headers={"Retry-After": "2.5"}),
            FakeResponse(_ok_body({"ok": True})),
        ]
    )
    client = _make_client(tmp_path, session)

    result = client.get_order_book("BTC-PERPETUAL")

    assert result == {"ok": True}
    assert sleeps == [2.5]


def test_call_auth_retries_on_429(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda s: sleeps.append(s))
    session = FakeSession(
        [
            FakeResponse({}, status_code=429, text="slow down", headers={"Retry-After": "1.0"}),
            FakeResponse(_auth_result()),
        ]
    )
    client = _make_client(tmp_path, session)

    result = client._call_auth(
        {
            "grant_type": "client_credentials",
            "client_id": client.config.client_id,
            "client_secret": client.config.client_secret,
        }
    )
    client._apply_auth_result(result)

    assert sleeps == [1.0]
    assert client._access_token == "token-a"


def test_auth_token_cache_shared_across_clients(tmp_path, monkeypatch):
    from deribit_engine.client import _AUTH_CACHE_LOCK, _AUTH_TOKEN_CACHE

    with _AUTH_CACHE_LOCK:
        _AUTH_TOKEN_CACHE.clear()
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda _s: None)
    auth = FakeResponse(_auth_result())
    summaries = FakeResponse(_ok_body({"summaries": []}))
    session_a = FakeSession([auth, summaries])
    session_b = FakeSession([summaries])
    client_a = _make_client(tmp_path, session_a)
    client_b = _make_client(tmp_path, session_b)

    client_a.get_account_summaries()
    client_b.get_account_summaries()

    assert [c["json"]["method"] for c in session_b.calls] == ["private/get_account_summaries"]


def test_get_account_summaries_triggers_oauth_first(tmp_path):
    session = FakeSession(
        [
            _ok_response := FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"summaries": [{"currency": "BTC"}]})),
        ]
    )
    client = _make_client(tmp_path, session)

    result = client.get_account_summaries(extended=True)

    assert result == [{"currency": "BTC"}]
    assert session.calls[0]["json"]["method"] == "public/auth"
    assert session.calls[0]["json"]["params"]["grant_type"] == "client_credentials"
    assert session.calls[1]["headers"].get("Authorization") == "Bearer token-a"


def test_oauth_refreshes_when_token_expires(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result(expires_in=0)),
            FakeResponse(_ok_body([])),
            FakeResponse(_auth_result(access_token="token-b", refresh_token="refresh-b", expires_in=0)),
            FakeResponse(_ok_body([])),
        ]
    )
    client = _make_client(tmp_path, session)

    client.get_positions()
    client.get_positions()

    auth_calls = [c for c in session.calls if c["json"]["method"] == "public/auth"]
    assert len(auth_calls) == 2
    # Second auth call should use refresh_token from first response.
    assert auth_calls[1]["json"]["params"]["grant_type"] == "refresh_token"
    assert auth_calls[1]["json"]["params"]["refresh_token"] == "refresh-a"


# ------------------------------------------------------------------
# Non-idempotent path (mutations)
# ------------------------------------------------------------------


def test_place_order_retries_once_on_connection_error(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_engine.client.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"order": {"order_id": "1"}, "trades": []})),
        ],
        raise_on_calls=[None, requests.exceptions.ConnectionError("down")],
    )
    client = _make_client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="connection failed"):
        # Only one connection-error retry is allowed (2 total calls after auth).
        # Provide two failures, the second after retry, to confirm the cap.
        session.raise_on_calls = [
            None,
            requests.exceptions.ConnectionError("down"),
            requests.exceptions.ConnectionError("down"),
        ]
        client.place_order(
            direction="sell",
            instrument_name="BTC-PERPETUAL",
            amount="1",
            label="trial-x",
            price="100",
        )
    # auth + 2 order attempts (initial + 1 retry) = 3 calls total
    assert len(session.calls) == 3


def test_place_order_does_not_retry_on_timeout(tmp_path):
    session = FakeSession(
        [FakeResponse(_auth_result())],
        raise_on_calls=[None, requests.exceptions.Timeout("slow")],
    )
    client = _make_client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="timed out"):
        client.place_order(
            direction="buy",
            instrument_name="BTC-PERPETUAL",
            amount="1",
            label="trial-x",
            price="100",
        )
    assert len(session.calls) == 2  # auth + 1 attempt, no retry


def test_place_order_does_not_retry_on_5xx(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse({}, status_code=502, text="bad gateway"),
        ]
    )
    client = _make_client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="server error HTTP 502"):
        client.place_order(
            direction="sell",
            instrument_name="BTC-PERPETUAL",
            amount="1",
            label="trial-x",
            price="100",
        )
    assert len(session.calls) == 2


def test_place_order_does_not_retry_on_429(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse({}, status_code=429, text="rate limited"),
        ]
    )
    client = _make_client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="rate limited"):
        client.place_order(
            direction="sell",
            instrument_name="BTC-PERPETUAL",
            amount="1",
            label="trial-x",
            price="100",
        )
    assert len(session.calls) == 2


def test_place_order_includes_client_order_id_by_default(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"order": {}, "trades": []})),
        ]
    )
    client = _make_client(tmp_path, session)

    client.place_order(
        direction="sell",
        instrument_name="BTC-PERPETUAL",
        amount="1",
        label="trial-x",
        price="100",
    )
    body = session.calls[1]["json"]
    assert body["method"] == "private/sell"
    assert body["params"]["label"] == "trial-x"
    assert body["params"]["client_order_id"].startswith("trial-x-")
    # Sensitive fields must live in body, not URL
    assert "instrument_name=" not in session.calls[1]["url"]


def test_create_combo_uses_post_body(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"id": "combo-1", "legs": []})),
        ]
    )
    client = _make_client(tmp_path, session)

    trades = [
        {"instrument_name": "BTC-A", "amount": "1", "direction": "buy"},
        {"instrument_name": "BTC-B", "amount": "1", "direction": "sell"},
    ]
    result = client.create_combo(trades)

    assert result["id"] == "combo-1"
    body = session.calls[1]["json"]
    assert body["method"] == "private/create_combo"
    assert body["params"]["trades"] == trades


def test_get_account_summaries_supports_object_wrapped_response(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(
                _ok_body(
                    {
                        "summaries": [
                            {"currency": "BTC", "equity": "1"},
                            {"currency": "USDC", "equity": "1000"},
                        ]
                    }
                )
            ),
        ]
    )
    client = _make_client(tmp_path, session)

    result = client.get_account_summaries(extended=True)

    assert result == [
        {"currency": "BTC", "equity": "1"},
        {"currency": "USDC", "equity": "1000"},
    ]


def test_business_error_raises_exchange_error(tmp_path):
    session = FakeSession([FakeResponse(_error_body(code=11044, message="not_enough_funds"))])
    client = _make_client(tmp_path, session)

    with pytest.raises(ExchangeError, match="not_enough_funds"):
        client.get_order_book("BTC-PERPETUAL")


def test_invalid_access_token_forces_reauth_on_idempotent_path(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result(access_token="token-1")),
            FakeResponse({}, status_code=401, text="unauthorized"),
            FakeResponse(_auth_result(access_token="token-2")),
            FakeResponse(_ok_body([])),
        ]
    )
    client = _make_client(tmp_path, session)

    client.get_positions()

    auth_calls = [c for c in session.calls if c["json"]["method"] == "public/auth"]
    assert len(auth_calls) == 2
    # Final data call uses refreshed token
    positions_calls = [c for c in session.calls if c["json"]["method"] == "private/get_positions"]
    assert positions_calls[-1]["headers"].get("Authorization") == "Bearer token-2"


def _deribit_http_error_body(*, code, message, data=None):
    """Deribit HTTP 4xx envelopes omit jsonrpc ``id`` (matches live API)."""
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message, "data": data},
        "testnet": False,
    }


def test_session_not_found_on_http_400_forces_reauth(tmp_path):
    session_not_found = _deribit_http_error_body(
        code=13009,
        message="unauthorized",
        data={"reason": "session_not_found"},
    )
    session = FakeSession(
        [
            FakeResponse(_auth_result(access_token="token-stale")),
            FakeResponse(session_not_found, status_code=400, text="session_not_found"),
            FakeResponse(_auth_result(access_token="token-fresh")),
            FakeResponse(_ok_body({"summaries": [{"currency": "BTC"}]})),
        ]
    )
    client = _make_client(tmp_path, session)

    result = client.get_account_summaries()

    assert result == [{"currency": "BTC"}]
    auth_calls = [c for c in session.calls if c["json"]["method"] == "public/auth"]
    assert len(auth_calls) == 2
    summary_calls = [c for c in session.calls if c["json"]["method"] == "private/get_account_summaries"]
    assert summary_calls[-1]["headers"].get("Authorization") == "Bearer token-fresh"


def test_deribit_http_error_without_jsonrpc_id_raises_classified_error(tmp_path):
    session = FakeSession(
        [
            FakeResponse(
                _deribit_http_error_body(code=11044, message="not_enough_funds"),
                status_code=400,
                text="not_enough_funds",
            )
        ]
    )
    client = _make_client(tmp_path, session)

    with pytest.raises(ExchangeError, match="not_enough_funds"):
        client.get_order_book("BTC-PERPETUAL")


def test_get_user_trades_by_currency_jsonrpc_params(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"trades": [{"trade_id": "1"}], "has_more": False})),
        ]
    )
    client = _make_client(tmp_path, session)

    out = client.get_user_trades_by_currency(
        "USDC",
        kind="option",
        count=100,
        sorting="desc",
        historical=True,
        subaccount_id=42,
    )

    assert out == {"trades": [{"trade_id": "1"}], "has_more": False}
    data_calls = [c for c in session.calls if c["json"]["method"] == "private/get_user_trades_by_currency"]
    assert len(data_calls) == 1
    p = data_calls[0]["json"]["params"]
    assert p["currency"] == "USDC"
    assert p["count"] == 100
    assert p["kind"] == "option"
    assert p["sorting"] == "desc"
    assert p["historical"] is True
    assert p["subaccount_id"] == 42


def test_get_user_trades_by_instrument_jsonrpc_params(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"trades": [{"trade_id": "x"}], "has_more": True})),
        ]
    )
    client = _make_client(tmp_path, session)

    out = client.get_user_trades_by_instrument(
        "BTC_USDC-27MAR26-90000-P",
        count=20,
        sorting="asc",
        historical=False,
        subaccount_id=7,
    )

    assert out == {"trades": [{"trade_id": "x"}], "has_more": True}
    data_calls = [c for c in session.calls if c["json"]["method"] == "private/get_user_trades_by_instrument"]
    assert len(data_calls) == 1
    p = data_calls[0]["json"]["params"]
    assert p["instrument_name"] == "BTC_USDC-27MAR26-90000-P"
    assert p["count"] == 20
    assert p["sorting"] == "asc"
    assert p["historical"] is False
    assert p["subaccount_id"] == 7


def test_get_instruments_uses_process_cache(tmp_path, monkeypatch):
    from deribit_engine.client import _INSTRUMENTS_CACHE, _INSTRUMENTS_CACHE_LOCK

    with _INSTRUMENTS_CACHE_LOCK:
        _INSTRUMENTS_CACHE.clear()
    monkeypatch.setenv("DERIBIT_INSTRUMENTS_CACHE_TTL_SEC", "300")
    rows = [{"instrument_name": "BTC-1"}]
    session = FakeSession([FakeResponse(_ok_body(rows)), FakeResponse(_ok_body([{"instrument_name": "BTC-2"}]))])
    client = _make_client(tmp_path, session)

    first = client.get_instruments("BTC", kind="option", expired=False)
    second = client.get_instruments("BTC", kind="option", expired=False)

    assert first == rows
    assert second == rows
    instrument_calls = [c for c in session.calls if c["json"]["method"] == "public/get_instruments"]
    assert len(instrument_calls) == 1


def test_get_index_price_uses_short_ttl_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DERIBIT_INDEX_PRICE_CACHE_TTL_SEC", "30")
    session = FakeSession(
        [
            FakeResponse(_ok_body({"index_price": 70000})),
            FakeResponse(_ok_body({"index_price": 71000})),
        ]
    )
    client = _make_client(tmp_path, session)

    first = client.get_index_price("btc_usd")
    second = client.get_index_price("btc_usd")

    assert first == {"index_price": 70000}
    assert second == {"index_price": 70000}
    price_calls = [c for c in session.calls if c["json"]["method"] == "public/get_index_price"]
    assert len(price_calls) == 1


def test_get_index_price_cache_disabled_with_zero_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("DERIBIT_INDEX_PRICE_CACHE_TTL_SEC", "0")
    session = FakeSession(
        [
            FakeResponse(_ok_body({"index_price": 70000})),
            FakeResponse(_ok_body({"index_price": 71000})),
        ]
    )
    client = _make_client(tmp_path, session)

    assert client.get_index_price("btc_usd") == {"index_price": 70000}
    assert client.get_index_price("btc_usd") == {"index_price": 71000}
    price_calls = [c for c in session.calls if c["json"]["method"] == "public/get_index_price"]
    assert len(price_calls) == 2


def test_get_volatility_index_data_buckets_window(tmp_path, monkeypatch):
    monkeypatch.setenv("DERIBIT_MACRO_CACHE_TTL_SEC", "60")
    session = FakeSession(
        [
            FakeResponse(_ok_body({"data": [[1, 2, 3, 4, 5]]})),
            FakeResponse(_ok_body({"data": [[9, 9, 9, 9, 9]]})),
        ]
    )
    client = _make_client(tmp_path, session)

    # Two near-simultaneous calls with timestamps a few ms apart fall in the same
    # 60s bucket and must hit the cache once.
    a = client.get_volatility_index_data("BTC", start_timestamp=1_000_000, end_timestamp=2_000_000)
    b = client.get_volatility_index_data("BTC", start_timestamp=1_000_010, end_timestamp=2_000_010)

    assert a == b == {"data": [[1, 2, 3, 4, 5]]}
    dvol_calls = [c for c in session.calls if c["json"]["method"] == "public/get_volatility_index_data"]
    assert len(dvol_calls) == 1


def test_get_subaccounts_jsonrpc_params(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body([{"id": 7, "type": "subaccount", "username": "fee_acc"}])),
        ]
    )
    client = _make_client(tmp_path, session)

    rows = client.get_subaccounts(with_portfolio=True)

    assert rows == [{"id": 7, "type": "subaccount", "username": "fee_acc"}]
    data_calls = [c for c in session.calls if c["json"]["method"] == "private/get_subaccounts"]
    assert len(data_calls) == 1
    assert data_calls[0]["json"]["params"]["with_portfolio"] is True


def test_submit_transfer_between_subaccounts_jsonrpc_params(tmp_path):
    session = FakeSession(
        [
            FakeResponse(_auth_result()),
            FakeResponse(_ok_body({"id": 3, "state": "confirmed", "currency": "USDC", "amount": 100})),
        ]
    )
    client = _make_client(tmp_path, session)

    out = client.submit_transfer_between_subaccounts(
        currency="USDC",
        amount="100",
        destination=42,
        source=10,
        nonce="abc12345",
    )

    assert out["id"] == 3
    data_calls = [c for c in session.calls if c["json"]["method"] == "private/submit_transfer_between_subaccounts"]
    assert len(data_calls) == 1
    p = data_calls[0]["json"]["params"]
    assert p["currency"] == "USDC"
    assert p["amount"] == "100"
    assert p["destination"] == 42
    assert p["source"] == 10
    assert p["nonce"] == "abc12345"
