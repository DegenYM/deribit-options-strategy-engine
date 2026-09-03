from pathlib import Path

from cc_marketd.main import write_snapshot
from cc_saas.billing import apply_stripe_event
from cc_saas.db import SessionLocal
from cc_saas.models import Tenant, User
from cc_saas.plans import get_plan


def test_market_snapshot_json(tmp_path: Path):
    path = write_snapshot({"btc_usd": "100000", "eth_usd": "3500", "source": "test"}, tmp_path)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "100000" in text


def test_stripe_checkout_completed_activates_plan():
    db = SessionLocal()
    try:
        user = User(email="stripe@example.com", approved=True, waitlisted=False)
        db.add(user)
        db.flush()
        tenant = Tenant(user_id=user.id)
        db.add(tenant)
        db.commit()
        tenant_id = tenant.id
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": tenant_id,
                    "metadata": {"tenant_id": tenant_id, "plan_id": "pro"},
                    "customer": "cus_test",
                    "subscription": "sub_test",
                }
            },
        }
        result = apply_stripe_event(db, event)
        db.commit()
        db.refresh(tenant)
        assert result["ok"] is True
        assert tenant.subscription is not None
        assert tenant.subscription.plan_id == "pro"
        assert tenant.subscription.status == "active"
        assert get_plan(tenant.subscription.plan_id).profit_sweep is True
    finally:
        db.close()
