from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .stress import black_swan_strategy_analysis
from .utils import format_decimal, safe_div, to_decimal


def _fmt(x: Any, places: int = 6) -> str:
    if isinstance(x, Decimal):
        return format_decimal(x, places) or "0"
    try:
        d = to_decimal(x)
        return format_decimal(d, places) or "0"
    except Exception:
        return str(x)


def _pct(x: Any, *, base: Any, places: int = 2) -> str:
    b = to_decimal(base)
    if b <= 0:
        return "n/a"
    v = to_decimal(x)
    return _fmt(safe_div(v, b) * Decimal("100"), places=places) + "%"


def _money(x: Any, places: int = 2) -> str:
    return _fmt(to_decimal(x), places=places)


def render_backtest_report_md(
    *,
    generated_at: datetime,
    backtest: dict[str, Any],
    scan: list[dict[str, Any]] | None,
) -> str:
    params = backtest.get("params") or {}
    stress = backtest.get("stress") or {}
    notes = backtest.get("notes") or []
    capital = params.get("reference_capital_usdc") or 0
    strategy = params.get("option_strategy") or params.get("strategy") or "naked_short"
    strategy_analysis = black_swan_strategy_analysis(str(strategy))

    lines: list[str] = []
    lines.append("# 回測與黑天鵝風險報告（Deribit 公開資料）")
    lines.append("")
    lines.append(f"- 產出時間：`{generated_at.isoformat()}`")
    lines.append(f"- 策略：`{strategy_analysis.get('label')}`")
    lines.append(
        f"- 回測區間：`{params.get('start')}` → `{params.get('end')}`（resolution={params.get('resolution')}）"
    )
    lines.append(f"- 參考資金（USDC）：`{params.get('reference_capital_usdc')}`")
    lines.append("")

    # ------------------------------------------------------------------
    # Executive summary (what the user actually wants)
    # ------------------------------------------------------------------
    lines.append("## 一頁結論（先看這裡）")
    lines.append(f"- **這段期間有沒有真的開倉**：`{params.get('open_trade_count', 0)}` 筆")
    lines.append(
        f"- **回測總損益**：`{_money(params.get('total_pnl_usdc'))}` USDC（約 `{_pct(params.get('total_pnl_usdc'), base=capital)}`）"
    )
    lines.append(f"- **最大回撤（歷史回放）**：`{_pct(params.get('max_drawdown_pct'), base=Decimal('1'))}`")
    if isinstance(stress, dict) and stress.get("spot_-40_slip_20%"):
        s40 = stress.get("spot_-40_slip_20%") or {}
        if isinstance(s40, dict) and s40.get("count"):
            lines.append(
                f"- **黑天鵝（-40% + 滑價）最差單次估算損失**：`{_money(s40.get('worst'))}` USDC（約 `{_pct(s40.get('worst'), base=capital)}`）"
            )
            if s40.get("worst_date") or s40.get("worst_by_book"):
                lines.append(f"  - 最差發生日：`{s40.get('worst_date')}`；book 拆解：`{s40.get('worst_by_book')}`")
            comps = s40.get("worst_components") if isinstance(s40, dict) else None
            if isinstance(comps, dict) and isinstance(comps.get("total"), dict):
                tot = comps["total"]
                lines.append(
                    f"  - 來源拆解（USDC）：「標的下跌→內在價增加」=`{_money(tot.get('base_move_usdc'))}`，"
                    f"「滑價」=`{_money(tot.get('slippage_usdc'))}`"
                )
            if s40.get("p05") is not None:
                lines.append(
                    f"- **黑天鵝（-40% + 滑價）p05（偏差情境）**：`{_money(s40.get('p05'))}` USDC（約 `{_pct(s40.get('p05'), base=capital)}`）"
                )
            lines.append(
                f"- **黑天鵝（-40% + 滑價）中位數（p50）**：`{_money(s40.get('p50'))}` USDC（約 `{_pct(s40.get('p50'), base=capital)}`）"
            )
    lines.append("")

    lines.append("## 怎麼解讀（超短版）")
    lines.append("- **historical replay**：用公開資料回放策略「會不會進場/出場」與粗略 PnL（近似）。")
    lines.append(
        "- **stress overlay**：假設你剛好有持倉時立刻遇到暴跌，估算當下可能虧多少（偏保守，且**每本帳損失上限 = 該本帳 equity**，代表最慘被打到歸零）。"
    )
    lines.append(f"- **策略黑天鵝重點**：{strategy_analysis.get('summary')}")
    lines.append(
        "- 你要的「黑天鵝會損失多少」主要看 **stress overlay 的 worst / p95**；你要的「收益/風險平衡」看 conservative vs baseline 的 tail loss 是否下降。"
    )
    lines.append("")

    lines.append("## 重要假設（請務必閱讀）")
    lines.append(
        "- **資料來源限制**：Deribit 公開 API 不一定提供「歷史期權價格/greeks/完整 order book」。本回測使用 index 與 DVOL 公開資料。"
    )
    lines.append(
        "- **期權價格近似**：使用 **DVOL 當作波動率 proxy**，以 Black‑Scholes（\\(r=0\\)）估算權利金與 delta；因此結果是「策略邏輯+風控門檻」的近似回放，不是交易所撮合級回放。"
    )
    lines.append("- **流動性近似**：黑天鵝情境額外加上滑價（slippage）做保守估算。")
    lines.append("")

    lines.append("## 基準回測結果（historical replay）")
    lines.append(f"- **開倉筆數**：`{params.get('open_trade_count', 0)}`")
    lines.append(
        f"- **總損益（USDC）**：`{_money(params.get('total_pnl_usdc'))}`（約 `{_pct(params.get('total_pnl_usdc'), base=capital)}`）"
    )
    lines.append(f"- **期末資金（USDC）**：`{_money(params.get('final_equity_usdc'))}`")
    lines.append(f"- **最大回撤（peak-to-trough）**：`{_pct(params.get('max_drawdown_pct'), base=Decimal('1'))}`")
    lines.append("")

    lines.append("## 黑天鵝損失估算（stress overlay）")
    lines.append(f"- **觀察方式**：{strategy_analysis.get('focus')}")
    lines.append(f"- **模型備註**：{strategy_analysis.get('model_note')}")
    if not stress:
        lines.append("- 無壓力測試資料。")
    else:
        lines.append("- 指標為「**在持倉日內立刻發生 shock**」的保守估算（不含動態管理）。")
        for name, s in stress.items():
            if not isinstance(s, dict) or not s.get("count"):
                continue
            lines.append(
                f"- **{name}**：樣本數={s.get('count')}｜"
                f"worst=`{_money(s.get('worst'))}`（{_pct(s.get('worst'), base=capital)}）｜"
                f"p05=`{_money(s.get('p05'))}`（{_pct(s.get('p05'), base=capital)}）｜"
                f"p50=`{_money(s.get('p50'))}`（{_pct(s.get('p50'), base=capital)}）｜"
                f"p95=`{_money(s.get('p95'))}`（{_pct(s.get('p95'), base=capital)}）"
            )
    lines.append("")

    lines.append("## Risk / Profit balance 改進（參數掃描）")
    if not scan:
        lines.append("- 本次未執行參數掃描。")
    else:
        lines.append(
            "- 下列比較以同一套回測 + stress overlay 產出；挑選方向是「**降低尾端損失**，在可接受的收益犧牲下」："
        )
        for row in scan:
            variant = row.get("variant")
            p = row.get("params") or {}
            st = row.get("stress") or {}
            worst_40 = None
            p05_40 = None
            worst_date_40 = None
            if isinstance(st, dict):
                s40 = st.get("spot_-40_slip_20%") or {}
                if isinstance(s40, dict):
                    worst_40 = s40.get("worst")
                    p05_40 = s40.get("p05")
                    worst_date_40 = s40.get("worst_date")
            lines.append(
                f"- **{variant}**：pnl={_fmt(row.get('total_pnl_usdc'))} mdd={_fmt(row.get('max_drawdown_pct'))} "
                f"stress_p05(-40%)={_fmt(p05_40)} stress_worst(-40%)={_fmt(worst_40)} worst_date={worst_date_40} "
                f"(BOOK_IM_TARGET={p.get('book_im_target')}, BOOK_IM_HARD={p.get('book_im_hard')}, "
                f"PER_LEG_IM_CAP_PUT={p.get('per_leg_im_cap_put')}, EXPIRY_IM_CAP={p.get('expiry_im_cap_per_book')}, MIN_NET_APR={p.get('min_net_apr')})"
            )
    lines.append("")

    lines.append("## 建議（可直接落地的調整）")
    for action in strategy_analysis.get("actions") or []:
        lines.append(f"- {action}")
    lines.append("")

    if notes:
        lines.append("## 執行/資料備註")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)
