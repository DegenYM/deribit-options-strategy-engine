"""Covered Call SaaS subscription catalog.

Prices are list prices in USD / month. Stripe Price IDs are filled via env.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PlanId = Literal["scout", "trader", "pro", "desk"]
RiskTier = Literal["low", "medium", "high"]

PLAN_ORDER: tuple[PlanId, ...] = ("scout", "trader", "pro", "desk")


@dataclass(frozen=True)
class Plan:
    id: PlanId
    name: str
    price_usd_month: int
    price_twd_month: int
    live_trading: bool
    coins_max: int
    allowed_coins: tuple[str, ...]
    allowed_tiers: tuple[str, ...]
    profit_sweep: bool
    telegram: bool
    subaccounts: int
    webhooks: bool
    dry_run_days_before_live: int
    stripe_price_env: str
    blurb_zh: str
    blurb_en: str

    def to_public_dict(self) -> dict[str, Any]:
        highlights_zh = [
            "僅模擬，不下真實單" if not self.live_trading else "可實單（須先跑滿 dry-run）",
            f"標的最多 {self.coins_max} 個：" + "／".join(self.allowed_coins),
            "風險檔：" + "／".join(self.allowed_tiers),
            "含 profit sweep" if self.profit_sweep else "不含 profit sweep",
            "含 Telegram" if self.telegram else "不含 Telegram",
        ]
        if self.subaccounts > 1:
            highlights_zh.append(f"最多 {self.subaccounts} 個子帳")
        if self.webhooks:
            highlights_zh.append("Webhook 為第二階段")
        return {
            "id": self.id,
            "name": self.name,
            "price_usd_month": self.price_usd_month,
            "price_twd_month": self.price_twd_month,
            "live_trading": self.live_trading,
            "coins_max": self.coins_max,
            "allowed_coins": list(self.allowed_coins),
            "allowed_tiers": list(self.allowed_tiers),
            "profit_sweep": self.profit_sweep,
            "telegram": self.telegram,
            "subaccounts": self.subaccounts,
            "webhooks": self.webhooks,
            "dry_run_days_before_live": self.dry_run_days_before_live,
            "strategy": "covered_call",
            "trial_eligible": self.id == "scout",
            "blurb_zh": self.blurb_zh,
            "blurb_en": self.blurb_en,
            "highlights_zh": highlights_zh,
            "disclaimer_zh": "APR 與歷史績效不是收益承諾，也不是投資建議。",
            "disclaimer_en": "APR figures are not a return guarantee and are not investment advice.",
        }


PLANS: dict[PlanId, Plan] = {
    "scout": Plan(
        id="scout",
        name="Scout",
        price_usd_month=49,
        price_twd_month=1490,
        live_trading=False,
        coins_max=1,
        allowed_coins=("BTC",),
        allowed_tiers=("low",),
        profit_sweep=False,
        telegram=False,
        subaccounts=1,
        webhooks=False,
        dry_run_days_before_live=7,
        stripe_price_env="STRIPE_PRICE_SCOUT",
        blurb_zh="Covered Call 模擬（dry-run）。熟悉 dashboard 與選約邏輯，不下真實單。",
        blurb_en="Covered Call paper trading only. Learn the dashboard without live orders.",
    ),
    "trader": Plan(
        id="trader",
        name="Trader",
        price_usd_month=99,
        price_twd_month=2990,
        live_trading=True,
        coins_max=1,
        allowed_coins=("BTC", "ETH"),
        allowed_tiers=("low", "medium"),
        profit_sweep=False,
        telegram=True,
        subaccounts=1,
        webhooks=False,
        dry_run_days_before_live=7,
        stripe_price_env="STRIPE_PRICE_TRADER",
        blurb_zh="單一標的實單 Covered Call。Telegram 告警與一鍵 Pause / Panic。",
        blurb_en="Live Covered Call on one underlying, with Telegram alerts and kill switch.",
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        price_usd_month=179,
        price_twd_month=5490,
        live_trading=True,
        coins_max=2,
        allowed_coins=("BTC", "ETH"),
        allowed_tiers=("low", "medium", "high"),
        profit_sweep=True,
        telegram=True,
        subaccounts=1,
        webhooks=False,
        dry_run_days_before_live=7,
        stripe_price_env="STRIPE_PRICE_PRO",
        blurb_zh="BTC 與 ETH、三檔風險、profit sweep。",
        blurb_en="BTC and ETH, all risk tiers, profit sweep.",
    ),
    "desk": Plan(
        id="desk",
        name="Desk",
        price_usd_month=299,
        price_twd_month=8900,
        live_trading=True,
        coins_max=2,
        allowed_coins=("BTC", "ETH"),
        allowed_tiers=("low", "medium", "high"),
        profit_sweep=True,
        telegram=True,
        subaccounts=3,
        webhooks=True,
        dry_run_days_before_live=7,
        stripe_price_env="STRIPE_PRICE_DESK",
        blurb_zh="最多三個子帳。Webhook 訊號為第二階段能力。",
        blurb_en="Up to three subaccounts. Signal webhooks ship in phase 2.",
    ),
}


def get_plan(plan_id: str) -> Plan:
    key = plan_id.strip().lower()
    if key not in PLANS:
        raise KeyError(plan_id)
    return PLANS[key]  # type: ignore[index]


DETAILS_PENDING_ZH = "方案細節仍會調整，以下為目前規劃，不是最終報價承諾。"


def comparison_matrix() -> dict[str, Any]:
    plans = [PLANS[plan_id] for plan_id in PLAN_ORDER]
    rows = [
        {
            "id": "price_usd",
            "label_zh": "月費（USD）",
            "cells": {plan.id: f"${plan.price_usd_month}" for plan in plans},
        },
        {
            "id": "price_twd",
            "label_zh": "月費（TWD）",
            "cells": {plan.id: f"NT${plan.price_twd_month:,}" for plan in plans},
        },
        {
            "id": "mode",
            "label_zh": "下單模式",
            "cells": {
                plan.id: ("僅模擬" if not plan.live_trading else "模擬後可實單") for plan in plans
            },
        },
        {
            "id": "coins",
            "label_zh": "標的",
            "cells": {plan.id: "／".join(plan.allowed_coins) for plan in plans},
        },
        {
            "id": "tiers",
            "label_zh": "風險檔",
            "cells": {plan.id: "／".join(plan.allowed_tiers) for plan in plans},
        },
        {
            "id": "sweep",
            "label_zh": "Profit sweep",
            "cells": {plan.id: ("有" if plan.profit_sweep else "無") for plan in plans},
        },
        {
            "id": "telegram",
            "label_zh": "Telegram",
            "cells": {plan.id: ("有" if plan.telegram else "無") for plan in plans},
        },
        {
            "id": "subaccounts",
            "label_zh": "子帳數量",
            "cells": {plan.id: str(plan.subaccounts) for plan in plans},
        },
        {
            "id": "webhooks",
            "label_zh": "Webhook",
            "cells": {plan.id: ("第二階段" if plan.webhooks else "無") for plan in plans},
        },
    ]
    return {"plan_ids": list(PLAN_ORDER), "plan_names": {plan.id: plan.name for plan in plans}, "rows": rows}


def public_catalog() -> list[dict[str, Any]]:
    return [PLANS[plan_id].to_public_dict() for plan_id in PLAN_ORDER]
