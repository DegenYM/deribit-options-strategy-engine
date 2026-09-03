from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import get_current_user
from ..models import DesiredState, Tenant, User, WorkerHeartbeat

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ApproveBody(BaseModel):
    email: str
    approved: bool = True


def _admin(user: User) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理員")
    return user


@router.get("/tenants")
def list_tenants(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _admin(user)
    rows = db.query(Tenant).all()
    out = []
    for tenant in rows:
        owner = db.get(User, tenant.user_id)
        hb = db.get(WorkerHeartbeat, tenant.id)
        desired = db.query(DesiredState).filter(DesiredState.tenant_id == tenant.id).one_or_none()
        out.append(
            {
                "tenant_id": tenant.id,
                "email": owner.email if owner else "",
                "approved": owner.approved if owner else False,
                "plan_id": tenant.subscription.plan_id if tenant.subscription else None,
                "desired": desired.desired if desired else "stopped",
                "worker": {
                    "pid": hb.pid if hb else 0,
                    "updated_at": hb.updated_at.isoformat() if hb else None,
                    "last_error": hb.last_error if hb else "",
                },
            }
        )
    return {"tenants": out}


@router.post("/approve")
def approve_user(body: ApproveBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _admin(user)
    target = db.query(User).filter(User.email == body.email.lower().strip()).one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="找不到使用者")
    target.approved = body.approved
    target.waitlisted = not body.approved
    tenant = db.query(Tenant).filter(Tenant.user_id == target.id).first()
    audit(
        db,
        tenant_id=tenant.id if tenant else "admin",
        user_id=user.id,
        action="user_approved" if body.approved else "user_unapproved",
        payload={"email": target.email},
    )
    db.commit()
    return {"ok": True, "email": target.email, "approved": target.approved}
