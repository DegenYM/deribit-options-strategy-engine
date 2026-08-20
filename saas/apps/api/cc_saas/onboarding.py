"""Intake survey → plan / tier / coin recommendation.

This is a setup questionnaire, not KYC and not investment advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .plans import PLAN_ORDER, PlanId, get_plan

Experience = Literal["novice", "options", "bots"]
Inventory = Literal["none", "transferring", "already_on_deribit"]
CoinChoice = Literal["BTC", "ETH", "both"]
CapitalBand = Literal["under_10k", "10_50k", "over_50k"]
Intent = Literal["learn", "overlay", "desk"]
Drawdown = Literal["conservative", "balanced", "aggressive"]

ACKNOWLEDGEMENTS = (
    "not_advice",
    "no_apr",
    "spot_downside",
    "keys_own",
    "panic_no_fill",
)

ACK_LABELS_ZH = {
    "not_advice": "我了解 Canopy（樹冠）是軟體工具，不是投資建議、基金或代操。",
    "no_apr": "我了解任何 APR、權利金或歷史績效都不是收益承諾。",
    "spot_downside": "我了解 Covered Call 無法消除現貨下跌風險，call 被行使時現貨可能被賣掉。",
    "keys_own": "我會用自己的 Deribit 子帳 API，不開 wallet 權限，並可隨時撤銷金鑰。",
    "panic_no_fill": "我了解 Pause／Panic 會送出指令，但不保證成交、價格或滑價。",
}


@dataclass(frozen=True)
class IntakeAnswers:
    experience: Experience
    inventory: Inventory
    coins: CoinChoice
    capital_band: CapitalBand
    intent: Intent
    drawdown: Drawdown
    want_sweep: bool
    alerts: bool
    acknowledgements: tuple[str, ...]


@dataclass(frozen=True)
class Recommendation:
    plan_id: PlanId
    risk_tier: str
    coins: tuple[str, ...]
    profit_sweep: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        plan = get_plan(self.plan_id)
        return {
            "plan_id": self.plan_id,
            "plan_name": plan.name,
            "risk_tier": self.risk_tier,
            "coins": list(self.coins),
            "profit_sweep": self.profit_sweep,
            "reasons": list(self.reasons),
            "plan": plan.to_public_dict(),
        }


def parse_intake(payload: dict[str, Any]) -> IntakeAnswers:
    acks = tuple(str(item) for item in payload.get("acknowledgements") or [])
    missing = [key for key in ACKNOWLEDGEMENTS if key not in acks]
    if missing:
        raise ValueError("請勾選全部風險聲明後再送出")
    try:
        return IntakeAnswers(
            experience=_one(payload, "experience", ("novice", "options", "bots")),  # type: ignore[arg-type]
            inventory=_one(payload, "inventory", ("none", "transferring", "already_on_deribit")),  # type: ignore[arg-type]
            coins=_one(payload, "coins", ("BTC", "ETH", "both")),  # type: ignore[arg-type]
            capital_band=_one(payload, "capital_band", ("under_10k", "10_50k", "over_50k")),  # type: ignore[arg-type]
            intent=_one(payload, "intent", ("learn", "overlay", "desk")),  # type: ignore[arg-type]
            drawdown=_one(payload, "drawdown", ("conservative", "balanced", "aggressive")),  # type: ignore[arg-type]
            want_sweep=bool(payload.get("want_sweep")),
            alerts=bool(payload.get("alerts")),
            acknowledgements=tuple(ACKNOWLEDGEMENTS),
        )
    except KeyError as exc:
        raise ValueError(f"缺少欄位：{exc.args[0]}") from exc


def _one(payload: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = str(payload.get(key) or "").strip()
    if key == "coins":
        value = value.upper() if value.lower() != "both" else "both"
        if value == "BOTH":
            value = "both"
    if value not in allowed:
        raise ValueError(f"{key} 不在允許選項內")
    return value


def recommend(answers: IntakeAnswers) -> Recommendation:
    reasons: list[str] = []
    if answers.intent == "desk":
        plan_id: PlanId = "desk"
        reasons.append("你需要多個子帳操作，建議 Desk。")
    elif answers.intent == "learn" or answers.inventory == "none" or answers.experience == "novice":
        plan_id = "scout"
        reasons.append("先用 Scout 做 dry-run，熟悉選約與倉位再考慮實單。")
        if answers.inventory == "none":
            reasons.append("帳上還沒有現貨時，不應直接開實單 Covered Call。")
    elif answers.coins == "both" or answers.drawdown == "aggressive" or answers.want_sweep:
        plan_id = "pro"
        reasons.append("雙幣、較積極的 delta、或 profit sweep 需要 Pro。")
    else:
        plan_id = "trader"
        reasons.append("單一標的、已有或將轉入現貨，Trader 可在 7 天 dry-run 後開實單。")

    if answers.intent == "overlay" and answers.inventory == "already_on_deribit" and plan_id == "scout":
        # Novice overlay on existing spot: still Scout first.
        reasons.append("即使已有現貨，新手仍建議先模擬。")

    plan = get_plan(plan_id)
    wanted = ("BTC", "ETH") if answers.coins == "both" else (answers.coins,)
    coins = tuple(coin for coin in wanted if coin in plan.allowed_coins)[: plan.coins_max]
    if not coins:
        coins = (plan.allowed_coins[0],)
    if answers.coins == "both" and plan.coins_max < 2:
        reasons.append(f"{plan.name} 只能跑一種幣，已先幫你收成 {coins[0]}。")

    tier_wish = {"conservative": "low", "balanced": "medium", "aggressive": "high"}[answers.drawdown]
    allowed_tiers = plan.allowed_tiers
    if tier_wish in allowed_tiers:
        risk_tier = tier_wish
    else:
        risk_tier = allowed_tiers[-1]
        reasons.append(f"{plan.name} 不含 {tier_wish} tier，改為 {risk_tier}。")

    profit_sweep = bool(answers.want_sweep and plan.profit_sweep)
    if answers.want_sweep and not plan.profit_sweep:
        reasons.append(f"{plan.name} 不含 profit sweep。")

    return Recommendation(
        plan_id=plan_id,
        risk_tier=risk_tier,
        coins=coins,
        profit_sweep=profit_sweep,
        reasons=tuple(reasons),
    )


def survey_schema() -> dict[str, Any]:
    return {
        "acknowledgements": [{"id": key, "label_zh": ACK_LABELS_ZH[key]} for key in ACKNOWLEDGEMENTS],
        "questions": [
            {
                "id": "experience",
                "label_zh": "你賣過選擇權嗎？",
                "options": [
                    {"id": "novice", "label_zh": "沒賣過，想先看引擎怎麼選約"},
                    {"id": "options", "label_zh": "手動賣過 call／put"},
                    {"id": "bots", "label_zh": "跑過自動交易或選擇權機器人"},
                ],
            },
            {
                "id": "inventory",
                "label_zh": "要備兌的現貨在哪？",
                "options": [
                    {"id": "none", "label_zh": "還沒有 BTC／ETH 現貨"},
                    {"id": "transferring", "label_zh": "有現貨，準備轉進 Deribit 子帳"},
                    {"id": "already_on_deribit", "label_zh": "已經在 Deribit 子帳"},
                ],
            },
            {
                "id": "coins",
                "label_zh": "先跑哪個標的？",
                "options": [
                    {"id": "BTC", "label_zh": "只跑 BTC"},
                    {"id": "ETH", "label_zh": "只跑 ETH"},
                    {"id": "both", "label_zh": "BTC 與 ETH"},
                ],
            },
            {
                "id": "capital_band",
                "label_zh": "這條子帳大概會放多少名義現貨？（只供開通建議，不是 AUM）",
                "options": [
                    {"id": "under_10k", "label_zh": "約 USD 1 萬以下"},
                    {"id": "10_50k", "label_zh": "約 USD 1–5 萬"},
                    {"id": "over_50k", "label_zh": "約 USD 5 萬以上"},
                ],
            },
            {
                "id": "intent",
                "label_zh": "這次開通的目的？",
                "options": [
                    {"id": "learn", "label_zh": "先模擬，搞懂流程"},
                    {"id": "overlay", "label_zh": "在已有現貨上疊加備兌賣 call"},
                    {"id": "desk", "label_zh": "多個子帳／之後要接訊號"},
                ],
            },
            {
                "id": "drawdown",
                "label_zh": "現貨被 call away 時，你比較能接受哪一種？",
                "options": [
                    {"id": "conservative", "label_zh": "寧可少收權利金，也較不想被行權（low）"},
                    {"id": "balanced", "label_zh": "平衡權利金與被行權機率（medium）"},
                    {"id": "aggressive", "label_zh": "較能接受被行權，換更近的 delta（high）"},
                ],
            },
        ],
        "plans": list(PLAN_ORDER),
    }
