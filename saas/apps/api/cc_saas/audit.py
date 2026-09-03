from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def audit(db: Session, *, tenant_id: str, user_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            payload=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        )
    )
