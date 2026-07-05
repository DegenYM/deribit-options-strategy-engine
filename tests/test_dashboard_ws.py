from __future__ import annotations

import pytest
from conftest import make_config

import deribit_engine.frontend_server as frontend_server
from deribit_engine.frontend_server.dashboard_ws import parse_ws_channels


def test_parse_ws_channels_defaults() -> None:
    assert parse_ws_channels(None) == frozenset({"market", "portfolio", "groups"})
    assert parse_ws_channels("market,health") == frozenset({"market", "health"})


def test_parse_ws_channels_rejects_unknown() -> None:
    try:
        parse_ws_channels("market,foo")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "foo" in str(exc)


@pytest.mark.enable_socket
def test_dashboard_websocket_hello_and_market(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json")

    class _SpotClient:
        def get_index_price(self, index_name: str) -> dict:
            return {"index_price": "65000" if "btc" in index_name else "3500"}

    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    monkeypatch.setattr(
        "deribit_engine.frontend_server.market_vol.fetch_iv_rank_snapshot",
        lambda *_a, **_k: {"iv_rank": {"BTC": "0.5", "ETH": "0.4"}, "iv_rank_lookback_days": 30},
    )
    monkeypatch.setattr(
        "deribit_engine.frontend_server.market_vol.fetch_index_price_change_24h_pct",
        lambda *_a, **_k: {"BTC": "1.2", "ETH": "-0.5"},
    )
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _cfg: _SpotClient())

    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/dashboard?channels=market") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["channels"] == ["market"]
            update = ws.receive_json()
            assert update["type"] == "update"
            assert update["channel"] == "market"
            assert update["data"]["BTC"] == "65000"
            assert update["data"]["ETH"] == "3500"


@pytest.mark.enable_socket
def test_dashboard_websocket_portfolio_from_cache(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    env_file = tmp_path / ".env.test"
    env_file.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    cfg = make_config(tmp_path, state_file=tmp_path / "bot.json", client_id="cid", client_secret="sec")
    fake_status = {"portfolio": {"total_equity_usdc": "12345", "regime": "normal"}}
    fake_groups = {"open": [], "closed": [], "underlying_index_usd": {"BTC": "65000"}}

    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "_aggregate_status", lambda *_a, **_k: fake_status)
    monkeypatch.setattr(frontend_server, "_aggregate_groups", lambda *_a, **_k: fake_groups)

    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )

    with TestClient(app) as client:
        # Warm caches via REST so websocket initial snapshot can read them.
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        groups_resp = client.get("/api/groups")
        assert groups_resp.status_code == 200

        with client.websocket_connect("/ws/dashboard?channels=portfolio,groups") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            portfolio = ws.receive_json()
            groups = ws.receive_json()
            channels = {portfolio.get("channel"), groups.get("channel")}
            assert channels == {"portfolio", "groups"}
            payloads = {
                portfolio.get("channel"): portfolio.get("data"),
                groups.get("channel"): groups.get("data"),
            }
            assert payloads["portfolio"]["portfolio"]["total_equity_usdc"] == "12345"
            assert payloads["groups"]["open"] == []
