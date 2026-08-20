from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..crypto import hash_token, new_token
from ..db import get_db
from ..deps import get_current_user, get_tenant, issue_session
from ..entitlements import subscription_is_live
from ..models import MagicLink, Tenant, User
from ..timeutil import as_utc
from ..trials import provision_signup

router = APIRouter(prefix="/api/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: str


class VerifyRequest(BaseModel):
    token: str


@router.post("/magic-link")
def request_magic_link(body: MagicLinkRequest, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="email 格式不正確")
    raw = new_token()
    db.add(
        MagicLink(
            email=email,
            token_hash=hash_token(raw),
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=settings.magic_link_ttl_minutes),
        )
    )
    db.commit()
    # Dev / waitlist: never silently email yet. Always return the token in non-prod
    # so the first 10 users can sign in without an ESP. Production should swap this
    # for Resend and stop returning `dev_token`.
    return {
        "ok": True,
        "email": email,
        "dev_token": raw,
        "message": "若已設定郵件服務，請至信箱點擊登入連結。開發模式會直接回傳 token。",
    }


@router.post("/verify")
def verify_magic_link(body: VerifyRequest, response: Response, db: Session = Depends(get_db)):
    row = db.query(MagicLink).filter(MagicLink.token_hash == hash_token(body.token)).one_or_none()
    now = datetime.now(tz=UTC)
    if row is None or row.used_at is not None or as_utc(row.expires_at) < now:
        raise HTTPException(status_code=400, detail="連結無效或已過期")
    row.used_at = now
    user = db.query(User).filter(User.email == row.email).one_or_none()
    created = False
    if user is None:
        user = User(email=row.email, waitlisted=True, approved=False)
        db.add(user)
        db.flush()
        tenant = Tenant(user_id=user.id, name="default")
        db.add(tenant)
        db.flush()
        provision_signup(db, user, tenant)
        created = True
    raw_session = new_token()
    issue_session(db, user, raw_session)
    if created:
        audit(db, tenant_id="signup", user_id=user.id, action="signup", payload={"email": user.email})
    db.commit()
    response.set_cookie(
        "cc_session",
        raw_session,
        httponly=True,
        samesite="lax",
        max_age=settings.session_days * 86400,
        path="/",
    )
    return {"ok": True, "email": user.email, "approved": user.approved, "waitlisted": user.waitlisted}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("cc_session", path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user), tenant: Tenant = Depends(get_tenant)):
    sub = tenant.subscription
    trial_live = bool(sub and sub.status == "trialing" and subscription_is_live(sub))
    return {
        "id": user.id,
        "email": user.email,
        "approved": user.approved,
        "waitlisted": user.waitlisted,
        "is_admin": user.is_admin,
        "paper_started_at": user.paper_started_at.isoformat() if user.paper_started_at else None,
        "live_unlocked_at": user.live_unlocked_at.isoformat() if user.live_unlocked_at else None,
        "dry_run_min_days": settings.dry_run_min_days,
        "intake_complete": bool(tenant.onboarding and tenant.onboarding.intake_complete),
        "plan_id": sub.plan_id if sub else None,
        "subscription_status": sub.status if sub else "inactive",
        "trial_days": settings.trial_days,
        "trial_plan_id": settings.trial_plan_id,
        "trial_ends_at": as_utc(sub.trial_ends_at).isoformat() if sub and sub.trial_ends_at else None,
        "trial_active": trial_live,
    }
