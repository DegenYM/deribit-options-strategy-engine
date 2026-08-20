from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .crypto import hash_token
from .db import get_db
from .models import SessionToken, Tenant, User
from .timeutil import as_utc


def get_current_user(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias="cc_session"),
) -> User:
    if not session:
        raise HTTPException(status_code=401, detail="未登入")
    row = db.query(SessionToken).filter(SessionToken.token_hash == hash_token(session)).one_or_none()
    if row is None or as_utc(row.expires_at) < datetime.now(tz=UTC):
        raise HTTPException(status_code=401, detail="登入已過期")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="使用者不存在")
    return user


def get_tenant(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.user_id == user.id).order_by(Tenant.created_at.asc()).first()
    if tenant is None:
        tenant = Tenant(user_id=user.id, name="default")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    return tenant


def issue_session(db: Session, user: User, raw_token: str) -> SessionToken:
    row = SessionToken(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(tz=UTC) + timedelta(days=settings.session_days),
    )
    db.add(row)
    return row
