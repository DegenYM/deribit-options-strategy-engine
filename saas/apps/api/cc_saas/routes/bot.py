from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cc_engine.settings import CoveredCallSettings
from cc_engine.snapshot import empty_snapshot, load_worker_snapshot
from cc_engine.worker import CoveredCallWorker
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings as app_settings
from ..crypto import decrypt_secret, encrypt_secret, last4
from ..db import get_db
from ..deps import get_current_user, get_tenant
from ..entitlements import (
    active_plan,
    assert_approved,
    can_enable_sweep,
    can_enable_telegram,
    can_use_coins,
    can_use_tier,
    live_unlocked,
    mark_paper_started,
)
from ..models import BotSettings, Credential, DesiredState, Tenant, User


def _assert_intake(tenant: Tenant) -> None:
    if tenant.onboarding is None or not tenant.onboarding.intake_complete:
        raise HTTPException(status_code=400, detail="請先完成開通前調查，再儲存設定或啟動 bot")

router = APIRouter(prefix="/api/bot", tags=["bot"])

ALLOWED_DESIRED = frozenset({"stopped", "dry_run", "live", "paused", "panic"})


class CredentialBody(BaseModel):
    client_id: str
    client_secret: str


class SettingsBody(BaseModel):
    risk_tier: str = "low"
    coins: list[str] = Field(default_factory=lambda: ["BTC"])
    profit_sweep: bool = False
    telegram_chat_id: str = ""
    telegram_bot_token: str = ""


class DesiredBody(BaseModel):
    desired: str


def _tenant_data_dir(tenant_id: str) -> Path:
    path = app_settings.data_dir / "tenants" / tenant_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_settings(db: Session, tenant: Tenant) -> BotSettings:
    if tenant.bot_settings is None:
        row = BotSettings(tenant_id=tenant.id)
        db.add(row)
        tenant.bot_settings = row
        db.flush()
    return tenant.bot_settings


def _ensure_desired(db: Session, tenant: Tenant) -> DesiredState:
    if tenant.desired_state is None:
        row = DesiredState(tenant_id=tenant.id, desired="stopped")
        db.add(row)
        tenant.desired_state = row
        db.flush()
    return tenant.desired_state


def _worker_settings(tenant: Tenant, *, live: bool) -> CoveredCallSettings:
    cred = tenant.credential
    if cred is None:
        raise HTTPException(status_code=400, detail="尚未綁定 Deribit API Key")
    bot = tenant.bot_settings
    coins = tuple((bot.coins_csv if bot else "BTC").split(","))
    token = decrypt_secret(bot.telegram_token_encrypted) if bot and bot.telegram_token_encrypted else ""
    return CoveredCallSettings(
        tenant_id=tenant.id,
        risk_tier=(bot.risk_tier if bot else "low"),
        coins=coins,
        profit_sweep=bool(bot.profit_sweep) if bot else False,
        live=live,
        state_dir=_tenant_data_dir(tenant.id),
        client_id=cred.client_id,
        client_secret=decrypt_secret(cred.secret_encrypted),
        telegram_bot_token=token,
        telegram_chat_id=(bot.telegram_chat_id if bot else ""),
    )


@router.get("/status")
def bot_status(user: User = Depends(get_current_user), tenant: Tenant = Depends(get_tenant)):
    plan = None
    try:
        plan = active_plan(tenant.subscription)
    except HTTPException:
        plan = None
    cred = tenant.credential
    bot = tenant.bot_settings
    desired = tenant.desired_state
    return {
        "has_credentials": cred is not None,
        "client_id": cred.client_id if cred else None,
        "secret_last4": cred.last4 if cred else None,
        "validated_at": cred.validated_at.isoformat() if cred and cred.validated_at else None,
        "risk_tier": bot.risk_tier if bot else "low",
        "coins": (bot.coins_csv.split(",") if bot else ["BTC"]),
        "profit_sweep": bool(bot.profit_sweep) if bot else False,
        "telegram_configured": bool(bot and bot.telegram_chat_id),
        "desired": desired.desired if desired else "stopped",
        "plan_id": plan.id if plan else None,
        "live_unlocked": live_unlocked(user, plan) if plan else False,
        "paper_started_at": user.paper_started_at.isoformat() if user.paper_started_at else None,
    }


@router.post("/credentials")
def save_credentials(
    body: CredentialBody,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    assert_approved(user)
    if tenant.credential is None:
        tenant.credential = Credential(tenant_id=tenant.id, client_id="", secret_encrypted="")
        db.add(tenant.credential)
    tenant.credential.client_id = body.client_id.strip()
    tenant.credential.secret_encrypted = encrypt_secret(body.client_secret.strip())
    tenant.credential.last4 = last4(body.client_secret)
    tenant.credential.validated_at = None
    audit(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        action="credentials_saved",
        payload={"client_id": tenant.credential.client_id, "last4": tenant.credential.last4},
    )
    db.commit()
    return {"ok": True, "client_id": tenant.credential.client_id, "secret_last4": tenant.credential.last4}


@router.post("/credentials/ping")
def ping_credentials(
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    assert_approved(user)
    worker = CoveredCallWorker(_worker_settings(tenant, live=False))
    result = worker.ping()
    if tenant.credential is not None:
        tenant.credential.validated_at = datetime.now(tz=UTC)
    audit(db, tenant_id=tenant.id, user_id=user.id, action="credentials_ping", payload={"ok": True})
    db.commit()
    return result


@router.post("/settings")
def save_settings(
    body: SettingsBody,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    assert_approved(user)
    _assert_intake(tenant)
    plan = active_plan(tenant.subscription)
    coins = [c.strip().upper() for c in body.coins if c.strip()]
    if not can_use_tier(plan, body.risk_tier):
        raise HTTPException(status_code=403, detail=f"{plan.name} 方案不包含 risk tier {body.risk_tier}")
    if not can_use_coins(plan, coins):
        raise HTTPException(status_code=403, detail=f"{plan.name} 方案不允許此幣別組合")
    if not can_enable_sweep(plan, body.profit_sweep):
        raise HTTPException(status_code=403, detail="目前方案不含 profit sweep")
    telegram_on = bool(body.telegram_chat_id.strip() and body.telegram_bot_token.strip())
    if not can_enable_telegram(plan, telegram_on):
        raise HTTPException(status_code=403, detail="目前方案不含 Telegram 告警")
    row = _ensure_settings(db, tenant)
    row.risk_tier = body.risk_tier.strip().lower()
    row.coins_csv = ",".join(coins)
    row.profit_sweep = body.profit_sweep
    row.telegram_chat_id = body.telegram_chat_id.strip()
    if body.telegram_bot_token.strip():
        row.telegram_token_encrypted = encrypt_secret(body.telegram_bot_token.strip())
    audit(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        action="settings_saved",
        payload={"risk_tier": row.risk_tier, "coins": coins, "profit_sweep": row.profit_sweep},
    )
    db.commit()
    return {"ok": True, "risk_tier": row.risk_tier, "coins": coins, "profit_sweep": row.profit_sweep}


@router.post("/desired")
def set_desired(
    body: DesiredBody,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    assert_approved(user)
    _assert_intake(tenant)
    desired = body.desired.strip().lower()
    if desired not in ALLOWED_DESIRED:
        raise HTTPException(status_code=400, detail="非法 desired state")
    plan = active_plan(tenant.subscription)
    if tenant.credential is None:
        raise HTTPException(status_code=400, detail="請先綁定 Deribit API Key")
    if desired == "live":
        if not live_unlocked(user, plan):
            raise HTTPException(
                status_code=403,
                detail=f"需先完成 {app_settings.dry_run_min_days} 天模擬，或目前方案不含實單",
            )
    if desired in {"dry_run", "live"}:
        mark_paper_started(user)
    row = _ensure_desired(db, tenant)
    row.desired = desired
    audit(db, tenant_id=tenant.id, user_id=user.id, action=f"desired_{desired}", payload={"desired": desired})
    db.commit()
    return {"ok": True, "desired": desired}


@router.post("/pause")
def pause_bot(
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    return set_desired(DesiredBody(desired="paused"), user, tenant, db)


@router.post("/panic")
def panic_bot(
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    return set_desired(DesiredBody(desired="panic"), user, tenant, db)


@router.get("/snapshot")
def snapshot(user: User = Depends(get_current_user), tenant: Tenant = Depends(get_tenant)):
    if tenant.credential is None:
        return empty_snapshot(tenant_id=tenant.id)
    live = bool(tenant.desired_state and tenant.desired_state.desired == "live")
    return load_worker_snapshot(_worker_settings(tenant, live=live))
