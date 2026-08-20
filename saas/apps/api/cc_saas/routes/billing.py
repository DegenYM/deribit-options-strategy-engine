from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import audit
from ..billing import apply_plan, apply_stripe_event, create_checkout_session, parse_webhook
from ..config import settings
from ..db import get_db
from ..deps import get_current_user, get_tenant
from ..entitlements import assert_approved
from ..models import Tenant, User
from ..plans import public_catalog

router = APIRouter(tags=["billing"])


class SubscribeBody(BaseModel):
    plan_id: str


@router.get("/api/plans")
def list_plans():
    return {"strategy": "covered_call", "plans": public_catalog()}


@router.get("/api/billing")
def billing_status(user: User = Depends(get_current_user), tenant: Tenant = Depends(get_tenant)):
    sub = tenant.subscription
    return {
        "plan_id": sub.plan_id if sub else None,
        "status": sub.status if sub else "inactive",
        "stripe_configured": bool(settings.stripe_secret_key),
        "dev_billing": settings.allow_dev_billing,
    }


@router.post("/api/billing/checkout")
def checkout(
    body: SubscribeBody,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    assert_approved(user)
    result = create_checkout_session(tenant, body.plan_id)
    audit(db, tenant_id=tenant.id, user_id=user.id, action="checkout_created", payload={"plan_id": body.plan_id})
    db.commit()
    return result


@router.post("/api/billing/dev-subscribe")
def dev_subscribe(
    body: SubscribeBody,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    if not settings.allow_dev_billing:
        raise HTTPException(status_code=403, detail="開發用訂閱已關閉")
    assert_approved(user)
    apply_plan(db, tenant, body.plan_id, status="active")
    audit(db, tenant_id=tenant.id, user_id=user.id, action="dev_subscribe", payload={"plan_id": body.plan_id})
    db.commit()
    return {"ok": True, "plan_id": body.plan_id, "status": "active"}


@router.post("/api/billing/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    event = parse_webhook(payload, signature)
    result = apply_stripe_event(db, event)
    db.commit()
    return result
