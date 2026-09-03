from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .models import Subscription, Tenant
from .plans import get_plan


def apply_plan(
    db: Session,
    tenant: Tenant,
    plan_id: str,
    *,
    status: str = "active",
    trial_ends_at: datetime | None = None,
) -> Subscription:
    get_plan(plan_id)
    sub = tenant.subscription
    if sub is None:
        sub = Subscription(tenant_id=tenant.id)
        db.add(sub)
        tenant.subscription = sub
    sub.plan_id = plan_id
    sub.status = status
    if status == "trialing":
        sub.trial_ends_at = trial_ends_at
    else:
        sub.trial_ends_at = None
    return sub


def create_checkout_session(tenant: Tenant, plan_id: str) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="尚未設定 Stripe")
    try:
        import stripe
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="未安裝 stripe 套件") from exc
    price_id = os.environ.get(plan.stripe_price_env, "").strip()
    if not price_id:
        raise HTTPException(status_code=503, detail=f"尚未設定 {plan.stripe_price_env}")
    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=tenant.id,
        metadata={"tenant_id": tenant.id, "plan_id": plan.id},
    )
    return {"checkout_url": session.url, "id": session.id}


def parse_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="尚未設定 STRIPE_WEBHOOK_SECRET")
    try:
        import stripe
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="未安裝 stripe 套件") from exc
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid stripe signature: {exc}") from exc
    return event


def apply_stripe_event(db: Session, event: dict[str, Any]) -> dict[str, Any]:
    etype = event.get("type")
    data = (event.get("data") or {}).get("object") or {}
    tenant_id = ""
    plan_id = ""
    if etype == "checkout.session.completed":
        tenant_id = str(data.get("client_reference_id") or (data.get("metadata") or {}).get("tenant_id") or "")
        plan_id = str((data.get("metadata") or {}).get("plan_id") or "")
        if tenant_id:
            tenant = db.get(Tenant, tenant_id)
            if tenant is None:
                return {"ok": False, "reason": "tenant_not_found"}
            if not plan_id:
                plan_id = "scout"
            sub = apply_plan(db, tenant, plan_id or "scout", status="active")
            sub.stripe_customer_id = str(data.get("customer") or "")
            sub.stripe_subscription_id = str(data.get("subscription") or "")
            return {"ok": True, "action": "activate", "tenant_id": tenant_id, "plan_id": sub.plan_id}
    if etype in {"customer.subscription.deleted", "customer.subscription.paused"}:
        sub_id = str(data.get("id") or "")
        sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == sub_id).one_or_none()
        if sub is None:
            return {"ok": False, "reason": "subscription_not_found"}
        sub.status = "canceled"
        return {"ok": True, "action": "cancel", "tenant_id": sub.tenant_id}
    if etype == "customer.subscription.updated":
        sub_id = str(data.get("id") or "")
        sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == sub_id).one_or_none()
        if sub is None:
            return {"ok": False, "reason": "subscription_not_found"}
        status = str(data.get("status") or sub.status)
        sub.status = "active" if status in {"active", "trialing"} else status
        return {"ok": True, "action": "update", "tenant_id": sub.tenant_id, "status": sub.status}
    return {"ok": True, "ignored": etype}
