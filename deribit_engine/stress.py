from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .models import OptionInstrument, normalize_strategy_name
from .utils import ONE, ZERO, safe_div, to_decimal


@dataclass(frozen=True)
class StressScenario:
    name: str
    spot_shock: Decimal  # e.g. -0.4 means spot * 0.6
    liquidity_slippage: Decimal  # additional adverse premium multiplier (close premium * (1+slip))


def _intrinsic(*, spot: Decimal, strike: Decimal, option_type: str) -> Decimal:
    if option_type == "call":
        return max(spot - strike, ZERO)
    return max(strike - spot, ZERO)


def _intrinsic_settlement(
    instrument: OptionInstrument,
    *,
    shocked_spot: Decimal,
    option_type: str,
) -> Decimal:
    """Intrinsic value expressed in the instrument's settlement currency."""
    if shocked_spot <= 0:
        return ZERO
    inst_type = (instrument.instrument_type or "").lower()
    settle = (instrument.settlement_currency or "").upper()
    quote = (instrument.quote_currency or "").upper()
    if settle == "USDC" or inst_type == "linear" or quote == "USDC":
        # Linear option premiums are quoted per underlying unit; the position
        # quantity already carries the option amount, so do not scale by
        # contract_size here.
        return _intrinsic(spot=shocked_spot, strike=instrument.strike, option_type=option_type)
    intrinsic_usd = _intrinsic(spot=shocked_spot, strike=instrument.strike, option_type=option_type)
    return safe_div(intrinsic_usd, shocked_spot, ZERO)


def stress_short_option_loss_usdc(
    instrument: OptionInstrument,
    *,
    option_type: str,
    quantity: Decimal,
    entry_premium: Decimal,
    spot: Decimal,
    scenario: StressScenario,
) -> Decimal:
    """Conservative instantaneous-loss estimate under a black-swan shock.

    Approximations:
    - Reprices option to intrinsic at shocked spot, plus a liquidity penalty
      applied to the *repurchase* premium (worse fills).
    - Ignores any delta-hedge or dynamic management; this is meant to bound tail risk.
    """
    shocked_spot = spot * (ONE + scenario.spot_shock)
    if shocked_spot <= 0:
        shocked_spot = ZERO
    repurchase = _intrinsic_settlement(instrument, shocked_spot=shocked_spot, option_type=option_type)
    repurchase *= ONE + scenario.liquidity_slippage

    # PnL in settlement currency: short receives entry_premium, pays repurchase.
    pnl_settle = (entry_premium - repurchase) * quantity

    # Convert to USDC if coin-settled.
    if instrument.settlement_currency.upper() == "USDC" or (instrument.instrument_type or "").lower() == "linear":
        return pnl_settle
    return pnl_settle * shocked_spot


def stress_short_option_loss_breakdown_usdc(
    instrument: OptionInstrument,
    *,
    option_type: str,
    quantity: Decimal,
    entry_premium: Decimal,
    spot: Decimal,
    scenario: StressScenario,
) -> dict[str, Any]:
    """Return a loss breakdown in USDC-equivalent terms.

    Components:
    - base_move: loss due to intrinsic at shocked spot (no extra slippage)
    - slippage: additional adverse cost due to liquidity_slippage
    - total: base_move + slippage (matches stress_short_option_loss_usdc)
    """
    return stress_option_position_pnl_breakdown_usdc(
        instrument,
        option_type=option_type,
        quantity=quantity,
        current_premium=entry_premium,
        spot=spot,
        scenario=scenario,
        direction="sell",
    )


def stress_option_position_pnl_breakdown_usdc(
    instrument: OptionInstrument,
    *,
    option_type: str,
    quantity: Decimal,
    current_premium: Decimal,
    spot: Decimal,
    scenario: StressScenario,
    direction: str,
) -> dict[str, Any]:
    """Return stressed PnL for a long or short option position.

    ``current_premium`` is the mark baseline. Short positions buy back at an
    adverse premium; long positions liquidate at an adverse discount.
    """
    shocked_spot = spot * (ONE + scenario.spot_shock)
    if shocked_spot <= 0:
        shocked_spot = ZERO
    intrinsic_settle = _intrinsic_settlement(instrument, shocked_spot=shocked_spot, option_type=option_type)
    side = (direction or "").lower()
    if side == "buy":
        repurchase_no_slip = intrinsic_settle
        repurchase_with_slip = intrinsic_settle * max(ONE - scenario.liquidity_slippage, ZERO)
        pnl_no_slip_settle = (repurchase_no_slip - current_premium) * quantity
        pnl_with_slip_settle = (repurchase_with_slip - current_premium) * quantity
    else:
        repurchase_no_slip = intrinsic_settle
        repurchase_with_slip = intrinsic_settle * (ONE + scenario.liquidity_slippage)
        pnl_no_slip_settle = (current_premium - repurchase_no_slip) * quantity
        pnl_with_slip_settle = (current_premium - repurchase_with_slip) * quantity

    slip_component_settle = pnl_with_slip_settle - pnl_no_slip_settle  # negative or zero
    base_component_settle = pnl_no_slip_settle

    if instrument.settlement_currency.upper() == "USDC" or (instrument.instrument_type or "").lower() == "linear":
        base_usdc = base_component_settle
        slip_usdc = slip_component_settle
        total_usdc = pnl_with_slip_settle
    else:
        base_usdc = base_component_settle * shocked_spot
        slip_usdc = slip_component_settle * shocked_spot
        total_usdc = pnl_with_slip_settle * shocked_spot

    return {
        "shocked_spot": shocked_spot,
        "intrinsic": intrinsic_settle,
        "repurchase_no_slip": repurchase_no_slip,
        "repurchase_with_slip": repurchase_with_slip,
        "base_move_usdc": base_usdc,
        "slippage_usdc": slip_usdc,
        "total_usdc": total_usdc,
    }


def black_swan_strategy_analysis(strategy: str | None) -> dict[str, Any]:
    normalized = normalize_strategy_name(strategy)
    analyses: dict[str, dict[str, Any]] = {
        "naked_short": {
            "label": "naked_short",
            "summary": "主要黑天鵝是標的方向行情：short put 在快速下跌時、short call 在快速上漲時都會變深度 ITM，虧損可能遠大於已收權利金。",
            "focus": "優先看 ±30% 到 ±60% shock 的 worst / p05，以及 by_book 是否接近單本帳 equity 上限。put 看下跌、call 看上漲。",
            "model_note": "這個壓力測試會把 short option 以 shocked intrinsic 加滑價估算買回成本，並用每本帳 equity 作歸零上限。",
            "actions": [
                "降低 `PER_LEG_IM_CAP_PUT` / `PER_LEG_IM_CAP_CALL`、`EXPIRY_IM_CAP`、`BOOK_IM_TARGET/BOOK_IM_HARD`。",
                "把 put / call 的 delta 與 OTM 範圍往保守側移，避免為了 APR 接近價外太近。",
                "進入 `CRISIS` regime 時停開新倉，已有倉位優先處理最大 gamma / 最近到期腿。",
            ],
        },
        "bull_put_spread": {
            "label": "bull_put_spread",
            "summary": "主要黑天鵝仍是標的快速下跌，但 long put 保護腿會把到期最大虧損限制在 spread width 減淨權利金附近。",
            "focus": "除了 worst / p05，也要看 short-leg 損失是否被 long put 抵銷；若報告只看到 short leg，應視為未扣保護腿的保守上限。",
            "model_note": "實際倉位壓力測試會把 buy/sell option 一起重估，因此 long put 會抵銷同方向下跌風險。",
            "actions": [
                "縮小 short/long strike width，或提高 long put delta，直接降低最大虧損。",
                "檢查 long leg 流動性；黑天鵝時保護腿有價但出場也會吃滑價。",
                "不要只看淨 APR，應同時比較 max loss、book equity 佔用與 p05 tail loss。",
            ],
        },
        "covered_call": {
            "label": "covered_call",
            "summary": "主要黑天鵝不是 naked call 爆倉，而是現貨 cover 隨標的大跌；上漲端則是收益被 call strike 封頂。",
            "focus": "下跌 shock 要看 BTC/ETH book equity 的 USDC 等值縮水；上漲暴衝則看 ITM call 的買回/履約與是否需要 spot exit。",
            "model_note": "實際倉位壓力測試在 covered_call 下會把 BTC/ETH book equity 的下跌納入 spot cover 損益；上漲端屬機會成本與交割管理風險，未用負 shock 表格表達。",
            "actions": [
                "call delta 可比裸賣 call 高，但要確認 cover 數量足夠且沒有重複占用。",
                "若希望 ITM 時鎖定退場，開啟 `COVERED_CALL_SPOT_EXIT_ENABLED`，保守模式用 robust exit。",
                "下跌黑天鵝重點是現貨庫存 drawdown，可用較低 call delta/較高權利金提供有限緩衝。",
            ],
        },
    }
    return analyses.get(normalized, analyses["naked_short"])


def summarize_losses(losses: list[Decimal]) -> dict[str, Any]:
    if not losses:
        return {"count": 0}
    ordered = sorted(losses)
    n = len(ordered)

    def pct(p: float) -> Decimal:
        if n == 1:
            return ordered[0]
        k = int(round((n - 1) * p))
        k = max(0, min(n - 1, k))
        return ordered[k]

    total = sum(ordered, ZERO)
    worst = ordered[0]
    best = ordered[-1]
    avg = safe_div(total, Decimal(str(n)), ZERO)
    return {
        "count": n,
        "worst": worst,
        "p05": pct(0.05),
        "p50": pct(0.50),
        "p95": pct(0.95),
        "best": best,
        "avg": avg,
    }


def summarize_loss_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a list of {loss, date, by_book} entries."""
    losses: list[Decimal] = []
    worst_entry: dict[str, Any] | None = None
    for e in entries:
        loss = to_decimal(e.get("loss"))
        losses.append(loss)
        if worst_entry is None or loss < to_decimal(worst_entry.get("loss")):
            worst_entry = e
    summary = summarize_losses(losses)
    if worst_entry is not None:
        summary["worst_date"] = worst_entry.get("date")
        summary["worst_by_book"] = worst_entry.get("by_book")
        summary["worst_components"] = worst_entry.get("components")
    return summary
