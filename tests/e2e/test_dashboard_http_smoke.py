"""HTTP smoke tests for dashboard static UI and bundle API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conftest import make_config

import deribit_demo.frontend_server as frontend_server


@pytest.fixture()
def dashboard_client(tmp_path, monkeypatch) -> TestClient:
    env_file = tmp_path / ".env.smoke"
    env_file.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=testnet",
                "DERIBIT_CLIENT_ID=smoke",
                "DERIBIT_CLIENT_SECRET=smoke",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = make_config(tmp_path, state_file=tmp_path / "smoke.state.json")
    fake_status = {"portfolio": {"total_equity_usdc": "1000"}, "trade_groups": []}
    fake_groups = {"open": [], "closed": [], "underlying_index_usd": {}}
    fake_summary = {"summary": {"realized_pnl_usdc": "50"}, "recent_closed_trades": []}

    monkeypatch.setattr(frontend_server, "load_config", lambda _path, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "_aggregate_status", lambda *_a, **_k: fake_status)
    monkeypatch.setattr(frontend_server, "_aggregate_groups", lambda *_a, **_k: fake_groups)
    monkeypatch.setattr(frontend_server, "_aggregate_realized_summary", lambda *_a, **_k: fake_summary)
    monkeypatch.setattr(frontend_server, "_latest_ledger_snapshot", lambda *_a, **_k: None)

    app = frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )
    return TestClient(app)


def test_dashboard_index_loads(dashboard_client: TestClient) -> None:
    response = dashboard_client.get("/")
    assert response.status_code == 200
    assert "Deribit Strategy Dashboard" in response.text
    assert "app.js" in response.text


def test_investor_page_loads(dashboard_client: TestClient) -> None:
    response = dashboard_client.get("/investor.html")
    assert response.status_code == 200
    assert "aggregate-card" in response.text


def test_dashboard_app_js_is_monolithic_bundle(dashboard_client: TestClient) -> None:
    js = dashboard_client.get("/app.js").text
    assert js.lstrip().startswith("// Local dashboard logic.")
    assert "bootDashboard" in js
    assert len(js.splitlines()) > 4000


def test_dashboard_bundle_returns_sections(dashboard_client: TestClient) -> None:
    response = dashboard_client.get("/api/dashboard_bundle?days=7")
    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert "groups" in payload
    assert "realized_summary" in payload
