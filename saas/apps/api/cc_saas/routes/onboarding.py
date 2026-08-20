from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import audit
from ..brand import (
    BRAND,
    EXCHANGE,
    GLOSS_ZH,
    HERO_TITLE_EN,
    HERO_TITLE_ZH,
    MODEL,
    NOT_CLAIMS_ZH,
    ORIGIN_EN,
    ORIGIN_ZH,
    PRODUCT_SLUG,
    STRATEGY,
    TAGLINE_EN,
    TAGLINE_ZH,
    WHY_POINTS_ZH,
)
from ..config import settings
from ..db import get_db
from ..deps import get_current_user, get_tenant
from ..models import Onboarding, Tenant, User
from ..onboarding import ACK_LABELS_ZH, ACKNOWLEDGEMENTS, parse_intake, recommend, survey_schema

router = APIRouter(tags=["onboarding"])


class IntakeBody(BaseModel):
    experience: str
    inventory: str
    coins: str
    capital_band: str
    intent: str
    drawdown: str
    want_sweep: bool = False
    alerts: bool = False
    acknowledgements: list[str] = Field(default_factory=list)


def _row_payload(row: Onboarding | None) -> dict[str, Any]:
    if row is None:
        return {
            "intake_complete": False,
            "answers": None,
            "recommendation": None,
        }
    recommendation = None
    if row.intake_complete:
        recommendation = {
            "plan_id": row.recommended_plan_id,
            "risk_tier": row.recommended_tier,
            "coins": [c for c in row.recommended_coins.split(",") if c],
            "profit_sweep": row.recommended_sweep,
            "reasons": json.loads(row.recommend_reasons or "[]"),
        }
    return {
        "intake_complete": row.intake_complete,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "answers": {
            "experience": row.experience,
            "inventory": row.inventory,
            "coins": row.coins,
            "capital_band": row.capital_band,
            "intent": row.intent,
            "drawdown": row.drawdown,
            "want_sweep": row.want_sweep,
            "alerts": row.alerts,
        },
        "recommendation": recommendation,
    }


@router.get("/api/product")
def product_meta():
    return {
        "brand": BRAND,
        "slug": PRODUCT_SLUG,
        "gloss_zh": GLOSS_ZH,
        "tagline_zh": TAGLINE_ZH,
        "tagline_en": TAGLINE_EN,
        "hero_title_zh": HERO_TITLE_ZH,
        "hero_title_en": HERO_TITLE_EN,
        "origin_zh": ORIGIN_ZH,
        "origin_en": ORIGIN_EN,
        "why_points_zh": list(WHY_POINTS_ZH),
        "strategy": STRATEGY,
        "exchange": EXCHANGE,
        "model": MODEL,
        "not_claims_zh": list(NOT_CLAIMS_ZH),
        "acknowledgements": [{"id": key, "label_zh": ACK_LABELS_ZH[key]} for key in ACKNOWLEDGEMENTS],
        "setup_checklist_zh": [
            "Deribit 帳戶已完成 KYC",
            "開一個子帳給 Canopy，不要用主帳",
            "把要當擔保的 BTC／ETH 現貨轉進該子帳",
            "API 只開 account:read + trade:read_write，不開 wallet",
            "能設 IP 白名單就綁平台出口 IP",
            "關掉同一子帳上會搶單的其他自動策略",
            "接受買權被履約時現貨可能被賣掉",
        ],
        "waitlist_only": settings.waitlist_only,
        "trial_days": settings.trial_days,
        "trial_plan_id": settings.trial_plan_id,
        "disclaimer": "Not investment advice. No APR guarantee. A canopy is shade, not a roof.",
    }


@router.get("/api/onboarding/schema")
def onboarding_schema():
    return survey_schema()


@router.get("/api/onboarding")
def get_onboarding(user: User = Depends(get_current_user), tenant: Tenant = Depends(get_tenant)):
    return _row_payload(tenant.onboarding)


@router.post("/api/onboarding")
def save_onboarding(
    body: IntakeBody,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    try:
        answers = parse_intake(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rec = recommend(answers)
    if tenant.onboarding is None:
        tenant.onboarding = Onboarding(tenant_id=tenant.id)
        db.add(tenant.onboarding)
    row = tenant.onboarding
    row.experience = answers.experience
    row.inventory = answers.inventory
    row.coins = answers.coins
    row.capital_band = answers.capital_band
    row.intent = answers.intent
    row.drawdown = answers.drawdown
    row.want_sweep = answers.want_sweep
    row.alerts = answers.alerts
    row.acknowledgements = json.dumps(list(answers.acknowledgements))
    row.recommended_plan_id = rec.plan_id
    row.recommended_tier = rec.risk_tier
    row.recommended_coins = ",".join(rec.coins)
    row.recommended_sweep = rec.profit_sweep
    row.recommend_reasons = json.dumps(list(rec.reasons), ensure_ascii=False)
    row.intake_complete = True
    row.completed_at = datetime.now(tz=UTC)
    audit(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        action="onboarding_intake",
        payload={"plan_id": rec.plan_id, "tier": rec.risk_tier, "coins": list(rec.coins)},
    )
    db.commit()
    payload = _row_payload(row)
    payload["recommendation"] = rec.to_dict()
    return payload
