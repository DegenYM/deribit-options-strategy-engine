from __future__ import annotations

import json

from cc_engine.snapshot import empty_snapshot, load_worker_snapshot
from fastapi import APIRouter, Depends

from ..config import settings
from ..deps import get_current_user, get_tenant
from ..models import Tenant, User
from .bot import _worker_settings

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: User = Depends(get_current_user), tenant: Tenant = Depends(get_tenant)):
    market_path = settings.data_dir / "market" / "latest.json"
    market = None
    if market_path.is_file():
        try:
            market = json.loads(market_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            market = None
    snapshot = empty_snapshot(tenant_id=tenant.id)
    if tenant.credential is not None:
        live = bool(tenant.desired_state and tenant.desired_state.desired == "live")
        snapshot = load_worker_snapshot(_worker_settings(tenant, live=live))
    return {
        "strategy": "covered_call",
        "disclaimer": "本工具不構成投資建議；掩護性買權無法消除現貨下跌風險，亦無收益保證。損益以幣本位為主、U 本位為輔。APR 不是承諾。",
        "market": market,
        "bot": snapshot,
        "performance": snapshot.get("performance") or {},
        "desired": tenant.desired_state.desired if tenant.desired_state else "stopped",
        "plan_id": tenant.subscription.plan_id if tenant.subscription else None,
    }
