from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from .config import settings
from .models import Subscription, User
from .plans import Plan, get_plan
from .timeutil import as_utc


class EntitlementError(HTTPException):
    def __init__(self, detail: str, status_code: int = 403) -> None:
        super().__init__(status_code=status_code, detail=detail)


def active_plan(sub: Subscription | None) -> Plan:
    if sub is None or sub.status not in {"active", "trialing"}:
        raise EntitlementError("需要有效訂閱才能使用此功能")
    return get_plan(sub.plan_id)


def assert_approved(user: User) -> None:
    if settings.waitlist_only and not user.approved and not user.is_admin:
        raise EntitlementError("帳號仍在 waitlist，需管理方核准後才能連線與啟動 bot")


def can_use_tier(plan: Plan, risk_tier: str) -> bool:
    return risk_tier.strip().lower() in plan.allowed_tiers


def can_use_coins(plan: Plan, coins: list[str]) -> bool:
    normalized = [c.upper() for c in coins]
    if len(set(normalized)) != len(normalized):
        return False
    if len(normalized) == 0 or len(normalized) > plan.coins_max:
        return False
    return all(coin in plan.allowed_coins for coin in normalized)


def can_enable_sweep(plan: Plan, enabled: bool) -> bool:
    return (not enabled) or plan.profit_sweep


def can_enable_telegram(plan: Plan, enabled: bool) -> bool:
    return (not enabled) or plan.telegram


def live_unlocked(user: User, plan: Plan, *, now: datetime | None = None) -> bool:
    if not plan.live_trading:
        return False
    clock = now or datetime.now(tz=UTC)
    if user.live_unlocked_at is not None:
        return as_utc(user.live_unlocked_at) <= clock
    started = user.paper_started_at
    if started is None:
        return False
    return clock >= as_utc(started) + timedelta(days=settings.dry_run_min_days)


def mark_paper_started(user: User, *, now: datetime | None = None) -> None:
    if user.paper_started_at is None:
        user.paper_started_at = now or datetime.now(tz=UTC)
