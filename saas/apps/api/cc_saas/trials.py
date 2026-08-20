"""Signup trial: lowest plan (Scout) for TRIAL_DAYS when waitlist is off."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .billing import apply_plan
from .config import settings
from .models import Tenant, User


def trial_end(*, now: datetime | None = None) -> datetime:
    clock = now or datetime.now(tz=UTC)
    return clock + timedelta(days=settings.trial_days)


def start_lowest_tier_trial(db: Session, tenant: Tenant, *, now: datetime | None = None) -> None:
    apply_plan(
        db,
        tenant,
        settings.trial_plan_id,
        status="trialing",
        trial_ends_at=trial_end(now=now),
    )


def provision_signup(db: Session, user: User, tenant: Tenant) -> None:
    """New account: waitlist stays gated; otherwise approve and start Scout trial."""
    if settings.waitlist_only:
        user.waitlisted = True
        user.approved = False
        return
    user.waitlisted = False
    user.approved = True
    if tenant.subscription is None:
        start_lowest_tier_trial(db, tenant)
