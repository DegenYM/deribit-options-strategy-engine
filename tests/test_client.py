from __future__ import annotations

import pytest
import requests

from deribit_demo.client import DeribitClient
from deribit_demo.exceptions import AuthenticationError, ExchangeError, TransientExchangeError

from conftest import make_config


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


def test_idempotent_request_retries_on_retryable_http(tmp_path, monkeypatch):
    monkeypatch.setattr("deribit_demo.client.time.sleep", lambda _s: None)
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
    monkeypatch.setattr("deribit_demo.client.time.sleep", lambda _s: None)
    session = FakeSession(
        [FakeResponse({}, status_code=522, text="timeout")] * 4
    )
    client = _make_client(tmp_path, session)

    with pytest.raises(TransientExchangeError, match="HTTP 522"):
        client.get_order_book("ETH_USDC-24APR26-1700-P")


def test_idempotent_request_respects_retry_after_header(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("deribit_demo.client.time.sleep", lambda s: sleeps.append(s))
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
    monkeypatch.setattr("deribit_demo.client.time.sleep", lambda _s: None)
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
        session.raise_on_calls = [None, requests.exceptions.ConnectionError("down"), requests.exceptions.ConnectionError("down")]
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
    session = FakeSession(
        [FakeResponse(_error_body(code=11044, message="not_enough_funds"))]
    )
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
