from datetime import UTC, datetime, timedelta

from cc_saas.db import SessionLocal
from cc_saas.models import User
from conftest import approve, make_client, signup


def test_health_and_plans():
    client = make_client()
    health = client.get("/api/health").json()
    assert health["strategy"] == "covered_call"
    assert "Not investment advice" in health["disclaimer"]
    plans = client.get("/api/plans").json()["plans"]
    assert plans[0]["id"] == "scout"
    assert all(p["strategy"] == "covered_call" for p in plans)


def test_waitlist_blocks_credentials_until_approved():
    client = make_client()
    signup(client, "wait@example.com")
    res = client.post(
        "/api/bot/credentials",
        json={"client_id": "id", "client_secret": "secret-value"},
    )
    assert res.status_code == 403


def test_subscribe_settings_and_dry_run_gate():
    client = make_client()
    signup(client, "live@example.com")
    approve("live@example.com")
    sub = client.post("/api/billing/dev-subscribe", json={"plan_id": "trader"})
    assert sub.status_code == 200
    creds = client.post(
        "/api/bot/credentials",
        json={"client_id": "deribit-client", "client_secret": "abcdefghijklmnop"},
    )
    assert creds.status_code == 200
    assert creds.json()["secret_last4"] == "mnop"
    body = creds.json()
    assert "client_secret" not in body

    denied = client.post(
        "/api/bot/settings",
        json={"risk_tier": "high", "coins": ["BTC"], "profit_sweep": False},
    )
    assert denied.status_code == 403

    ok = client.post(
        "/api/bot/settings",
        json={"risk_tier": "medium", "coins": ["ETH"], "profit_sweep": False},
    )
    assert ok.status_code == 200

    live = client.post("/api/bot/desired", json={"desired": "live"})
    assert live.status_code == 403

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "live@example.com").one()
        user.paper_started_at = datetime.now(tz=UTC) - timedelta(days=10)
        db.commit()
    finally:
        db.close()

    dry = client.post("/api/bot/desired", json={"desired": "dry_run"})
    assert dry.status_code == 200
    live2 = client.post("/api/bot/desired", json={"desired": "live"})
    assert live2.status_code == 200
    pause = client.post("/api/bot/pause")
    assert pause.json()["desired"] == "paused"
    panic = client.post("/api/bot/panic")
    assert panic.json()["desired"] == "panic"

    dash = client.get("/api/dashboard")
    assert dash.status_code == 200
    assert dash.json()["strategy"] == "covered_call"
    assert "投資建議" in dash.json()["disclaimer"]
