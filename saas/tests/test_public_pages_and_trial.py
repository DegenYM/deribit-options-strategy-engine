from dataclasses import replace
from datetime import UTC, datetime, timedelta

import cc_saas.trials as trials_mod
from cc_saas.db import SessionLocal
from cc_saas.models import Tenant, User
from conftest import make_client, signup
from test_onboarding import _intake


def test_spa_pages_serve_html():
    client = make_client()
    for path in ("/", "/strategy", "/pricing", "/login", "/signup", "/app"):
        res = client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "Canopy" in res.text


def test_strategies_explain_max_profit_and_max_loss():
    client = make_client()
    payload = client.get("/api/strategies").json()
    assert payload["v1_strategy"] == "covered_call"
    covered = next(item for item in payload["strategies"] if item["id"] == "covered_call")
    assert covered["available"] is True
    assert "權利金" in covered["max_profit"]["headline_zh"]
    assert "零" in covered["max_loss"]["headline_zh"]
    assert any(item["status"] == "coming_soon" for item in payload["strategies"])
    product = client.get("/api/product").json()
    assert product["trial_days"] == 30
    assert product["trial_plan_id"] == "scout"


def test_plans_catalog_includes_comparison_and_trial_flag():
    client = make_client()
    catalog = client.get("/api/plans").json()
    assert catalog["trial_days"] == 30
    assert catalog["trial_plan_id"] == "scout"
    assert "調整" in catalog["details_pending_zh"]
    assert catalog["comparison"]["plan_ids"] == ["scout", "trader", "pro", "desk"]
    scout = catalog["plans"][0]
    assert scout["id"] == "scout"
    assert scout["trial_eligible"] is True
    assert catalog["plans"][1]["trial_eligible"] is False


def test_waitlist_signup_does_not_start_trial():
    client = make_client()
    signup(client, "nowait-trial@example.com")
    me = client.get("/api/auth/me").json()
    assert me["approved"] is False
    assert me["trial_active"] is False
    assert me["plan_id"] is None
    dash = client.get("/api/dashboard")
    assert dash.status_code == 200
    assert dash.json()["performance"]["has_data"] is False
    assert dash.json()["performance"]["total_equity_usdc"] is None


def test_signup_starts_scout_trial_when_waitlist_off(monkeypatch):
    client = make_client()
    monkeypatch.setattr(trials_mod, "settings", replace(trials_mod.settings, waitlist_only=False))
    signup(client, "trialer@example.com")
    me = client.get("/api/auth/me").json()
    assert me["approved"] is True
    assert me["waitlisted"] is False
    assert me["trial_active"] is True
    assert me["plan_id"] == "scout"
    bill = client.get("/api/billing").json()
    assert bill["status"] == "trialing"
    assert bill["trial_ends_at"]


def test_expired_trial_blocks_settings(monkeypatch):
    client = make_client()
    monkeypatch.setattr(trials_mod, "settings", replace(trials_mod.settings, waitlist_only=False))
    signup(client, "expired-trial@example.com")
    saved = client.post(
        "/api/onboarding",
        json=_intake(
            experience="novice",
            inventory="none",
            coins="BTC",
            intent="learn",
            drawdown="conservative",
        ),
    )
    assert saved.status_code == 200
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "expired-trial@example.com").one()
        tenant = db.query(Tenant).filter(Tenant.user_id == user.id).one()
        tenant.subscription.trial_ends_at = datetime.now(tz=UTC) - timedelta(days=1)
        db.commit()
    finally:
        db.close()
    denied = client.post(
        "/api/bot/settings",
        json={"risk_tier": "low", "coins": ["BTC"], "profit_sweep": False},
    )
    assert denied.status_code == 403
    assert "試用已結束" in denied.json()["detail"]
