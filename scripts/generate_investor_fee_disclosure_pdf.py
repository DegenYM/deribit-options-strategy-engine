#!/usr/bin/env python3
"""Generate investor fee disclosure PDFs (zh-TW + English) via reportlab."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

MANAGEMENT_FEE_ANNUAL_PCT = "1.0"
PERFORMANCE_FEE_PCT = "10"
DOC_VERSION = "1.1"
DOC_DATE = date.today().isoformat()

_PALETTE = {
    "ink": "#0f172a",
    "muted": "#64748b",
    "border": "#e2e8f0",
    "header_bg": "#f1f5f9",
}


@dataclass(frozen=True)
class FeeDisclosureContent:
    locale: str
    doc_title: str
    pdf_title_meta: str
    subtitle: str
    intro: str
    footer_page: str
    s1_title: str
    s1_table: list[list[str]]
    s2_title: str
    s2_intro: str
    s2_table: list[list[str]]
    s2_1_title: str
    s2_1_bullets: list[str]
    s2_2_title: str
    s2_2_bullets: list[str]
    s3_title: str
    s3_intro: str
    s3_table: list[list[str]]
    s3_1_title: str
    s3_formulas: str
    s3_flow_note: str
    s4_title: str
    s4_formulas: str
    s4_note: str
    s5_title: str
    s5_intro: str
    s5_1_title: str
    s5_1_items: list[str]
    s5_2_title: str
    s5_2_body: str
    s5_3_title: str
    s5_3_body: str
    s6_title: str
    s6_table: list[list[str]]
    s6_report_title: str
    s6_report_bullets: list[str]
    s7_title: str
    s7_table: list[list[str]]
    s7_note: str
    s8_title: str
    s8_bullets: list[str]
    faq_title: str
    faq_bullets: list[str]
    signature_block: str
    disclaimer: str


def _zh_content() -> FeeDisclosureContent:
    mgmt, perf = MANAGEMENT_FEE_ANNUAL_PCT, PERFORMANCE_FEE_PCT
    return FeeDisclosureContent(
        locale="zh-TW",
        doc_title="投資人分潤與計費說明書",
        pdf_title_meta="投資人分潤與計費說明",
        subtitle=f"Deribit 期權策略組合｜版本 {DOC_VERSION}｜{DOC_DATE}",
        intro=(
            "本文件說明管理費與績效費之計算、多幣種（BTC／ETH／USDC）估值與收取方式，"
            "以及 Covered Call 備兌現貨在兩種費用中之不同處理。"
            "正式權利義務以雙方簽署之投資管理協議為準。"
        ),
        footer_page="第 {n} 頁",
        s1_title="一、費用結構總覽",
        s1_table=[
            ["項目", "費率／原則", "說明"],
            ["管理費", f"年化 {mgmt}%（按季結算）", "按「管理費計費資產 AUM_mgmt」之季度平均規模計提"],
            ["績效費", f"{perf}%", "僅就超過高水位（HWM）之新創利潤抽取"],
            ["計費單元", "投資人合併", "名下所有授權子帳合併為單一投資人帳戶"],
            ["高水位 HWM", "必備", "僅綁定 NAV_perf；未創新高前不收取績效費"],
        ],
        s2_title="二、兩套計費基礎：績效 NAV 與管理費資產",
        s2_intro=(
            "因 Covered Call 需以投資人 BTC／ETH 現貨作為備兌與保證金，"
            "現貨對「策略 Alpha」與「託管規模」的意義不同，故採雙軌口徑："
        ),
        s2_table=[
            ["口徑", "是否含投資人備兌現貨", "用途"],
            ["績效 NAV（NAV_perf）", "否", "期間損益、高水位 HWM、績效費"],
            ["管理費計費資產（AUM_mgmt）", "是（限授權子帳內約定備兌現貨）", "管理費之季度平均規模"],
        ],
        s2_1_title="2.1 績效 NAV（NAV_perf）— 不含備兌現貨",
        s2_1_bullets=[
            "定義：各授權子帳 USDC 等價總權益加總後，<b>扣除</b>約定之「投資人備兌現貨」市值（結算日 Deribit 指數價換算 USDC）。",
            "計入：期權持倉、保證金、穩定幣、策略相關已實現／未實現損益（USDC 等價）。",
            "不計入：依協議列為投資人自有、僅用於備兌之 BTC／ETH 及其價格漲跌。",
            "目的：績效費只對期權策略表現抽成，不對現貨 Beta 抽成。",
        ],
        s2_2_title="2.2 管理費計費資產（AUM_mgmt）— 含備兌現貨",
        s2_2_bullets=[
            "定義：NAV_perf <b>加回</b>授權子帳內約定用於 Covered Call 備兌之 BTC／ETH 現貨市值（USDC 等價）。",
            "理由：現貨佔用保證金與託管資源，管理人須對整體風險與營運負責。",
            f"公式：季度管理費 = AUM_mgmt 季度平均 × {mgmt}% ×（當季天數／365）。",
        ],
        s3_title="三、多幣種（BTC／ETH／USDC）如何換算",
        s3_intro=(
            "Deribit 帳戶依保證金幣別分池（BTC book、ETH book、USDC book）。"
            "所有計費與對帳均以<b>結算日結算時點</b>之官方數據為準，統一換算為 USDC 等價。"
        ),
        s3_table=[
            ["資產類別", "估值方式"],
            ["USDC 及線性部位", "帳面 USDC 數額"],
            ["BTC book 權益", "帳戶 BTC 權益 × 結算日 BTC 指數價（USD）"],
            ["ETH book 權益", "帳戶 ETH 權益 × 結算日 ETH 指數價（USD）"],
            ["投資人備兌現貨", "持倉數量 × 同上指數價（與自 NAV_perf 扣除口徑一致）"],
        ],
        s3_1_title="3.1 合併公式（概念）",
        s3_formulas=(
            "NAV_perf = Σ 各子帳（USDC 等價總權益）− 投資人備兌現貨 USDC 等價市值<br/>"
            "AUM_mgmt = NAV_perf + 投資人備兌現貨 USDC 等價市值<br/>"
            "期間損益（績效用）= NAV_perf,期末 − NAV_perf,期初 − 淨申赎調整"
        ),
        s3_flow_note="淨申赎調整：入金提高期初基準、出金降低；以 Deribit 交易紀錄與雙方確認時間為準。",
        s4_title=f"四、績效費（{perf}%）與高水位",
        s4_formulas=(
            "可分配利潤 = max(0, NAV_perf,期末 − HWM − 當季淨申赎調整)<br/>"
            f"當季績效費 = 可分配利潤 × {perf}%<br/>"
            "結算後 HWM = NAV_perf,期末 − 當季績效費（扣費後高水位；管理費另計）"
        ),
        s4_note=(
            "HWM <b>僅</b>追蹤 NAV_perf，<b>不</b>含備兌現貨。因此 BTC／ETH 現貨下跌不會直接拉低 HWM，"
            "期權策略賺錢仍可依 NAV_perf 收取績效費。新增資金、部分贖回之 HWM 處理依投資管理協議或 side letter 約定。"
        ),
        s5_title="五、費用收取：Fee 專戶對帳 + 主帳提幣",
        s5_intro=(
            "管理費與績效費<b>不</b>自策略子帳直接扣款。投資人另開<b>Fee 專戶</b>子帳作對帳專戶；"
            "季結算後管理方將獲利現貨 TRADE 成 USDC/USDT，以策略子帳 API（Wallet 讀寫）劃轉至 Fee 專戶對帳；"
            "投資人自<b>主帳</b>提幣至管理方指定地址。Fee 專戶 API 僅 Account=read。"
        ),
        s5_1_title="5.1 投資人義務",
        s5_1_items=[
            "建立獨立 Fee 子帳（建議名稱 fee_acc；至少 5 字元，勿用 fee），與策略子帳分開；",
            "為 Fee 專戶建立專用 API Key（Account=read，Wallet／Trade 均 none）並安全交付管理人；",
            "策略子帳 API 開 Account=read、Trade=read_write、Wallet=read_write（供季末 API 劃轉至 Fee 專戶）；",
            "收到帳單後與管理人對帳並確認；",
            "自<b>主帳</b> Withdraw 提幣至管理方指定地址，完成付費；",
            "主帳 API 勿交付管理人；",
            "逾期未確認對帳或未付費，管理人得依協議暫停新開倉直至結清。",
        ],
        s5_2_title="5.2 與 NAV_perf 的關係",
        s5_2_body=(
            "NAV_perf／HWM 僅合併<b>策略</b>子帳權益，不含 Fee 專戶餘額。Fee 專戶僅為對帳用，實際收取由投資人自主帳提幣完成。"
        ),
        s5_3_title="5.3 管理費與績效費",
        s5_3_body=(
            f"管理費按 AUM_mgmt（含備兌現貨）計提、績效費為可分配利潤之 {perf}%；"
            "帳單均以 USDC 等價開立。管理方換幣劃轉至 Fee 專戶供對帳，投資人確認後自主帳提幣付費。"
        ),
        s6_title="六、結算週期與投資人報表",
        s6_table=[
            ["項目", "建議約定"],
            ["定期結算", "每季末（3／6／9／12 月最後一日，UTC）"],
            ["計價時點", "結算日 23:59 UTC 快照"],
            ["帳單出具", "結算日後 10 個工作天內"],
            ["費用支付", "對帳確認後 5 個工作天內，自<b>主帳</b>提至管理方指定地址"],
            ["贖回", "贖回生效日另結算應計管理費與績效費"],
        ],
        s6_report_title="每季報表至少包含：",
        s6_report_bullets=[
            "NAV_perf 與 AUM_mgmt 之季初／季末值；",
            "備兌現貨數量與 USDC 等價市值（與 NAV_perf 扣除額對照）；",
            "淨申赎、可分配利潤、HWM 變動、管理費與績效費明細；",
            "各 book 權益與結算日指數價；",
            "實際扣款幣別與換算說明（若有）。",
        ],
        s7_title="七、計算範例（簡化）",
        s7_table=[
            ["項目", "金額（USDC 等價）"],
            ["季初 HWM（NAV_perf）", "100,000"],
            ["季末 NAV_perf", "106,000"],
            ["季末備兌現貨（僅管理費）", "40,000"],
            ["季末 AUM_mgmt", "146,000"],
            ["當季淨入金", "2,000"],
            ["可分配利潤", "106,000 − 100,000 − 2,000 = 4,000"],
            [f"績效費（{perf}%）", "400"],
            [f"管理費（季均 AUM_mgmt≈140,000、年化{mgmt}%）", "約 350"],
        ],
        s7_note="上表僅供理解公式；實際以結算日官方權益與協議費率為準。",
        s8_title="八、風險與免責",
        s8_bullets=[
            "期權賣方策略具本金損失風險；本文件不構成收益保證。",
            "HWM 僅避免重複抽取績效費，不保證 NAV_perf 高於歷史高點。",
            "多幣種換算與換幣扣費可能受指數價、流動性與交易所規則影響。",
            "備兌現貨價格風險由投資人自行承擔；績效費不對現貨漲跌抽成，管理費就備兌現貨規模計提。",
            "過往績效不代表未來結果；稅務與監管事宜請自行諮詢專業顧問。",
            "爭議時以 Deribit 官方對帳為準；本文件與簽署協議衝突時，以協議為準。",
        ],
        faq_title="附錄：常見問題 — HWM 是否包含現貨？",
        faq_bullets=[
            "<b>不包含。</b>若 HWM 綁定「含現貨的總權益」，現貨大跌時即使期權賺錢也可能永遠達不到高水位。",
            "本計畫 HWM 僅追蹤 NAV_perf（已扣除備兌現貨），現貨跌不直接拉低 HWM。",
            "管理費無 HWM，按 AUM_mgmt（含現貨）計提；現貨跌會使管理費基數變小，屬託管規模變化，非績效高水位問題。",
            "協議應約定備兌現貨數量並每季對帳，確保 NAV_perf 計算一致。",
        ],
        signature_block=(
            "管理人：＿＿＿＿＿＿＿＿＿＿　　聯絡方式：＿＿＿＿＿＿＿＿＿＿<br/>"
            "投資人確認：＿＿＿＿＿＿＿＿＿＿　　日期：＿＿＿＿＿＿＿＿＿＿"
        ),
        disclaimer="本文件描述之計費邏輯與 Deribit 期權策略引擎之帳戶分池、USDC 等價報表設計一致；不構成 Deribit 官方立場。",
    )


def _en_content() -> FeeDisclosureContent:
    mgmt, perf = MANAGEMENT_FEE_ANNUAL_PCT, PERFORMANCE_FEE_PCT
    return FeeDisclosureContent(
        locale="en",
        doc_title="Investor Fee Disclosure",
        pdf_title_meta="Investor Fee Disclosure",
        subtitle=f"Deribit Options Program | Version {DOC_VERSION} | {DOC_DATE}",
        intro=(
            "This document describes management and performance fees, multi-currency (BTC / ETH / USDC) "
            "valuation and collection, and how covered-call collateral spot is treated differently for each fee. "
            "Binding terms are governed by the signed investment management agreement."
        ),
        footer_page="Page {n}",
        s1_title="1. Fee overview",
        s1_table=[
            ["Item", "Rate / rule", "Notes"],
            ["Management fee", f"{mgmt}% p.a. (quarterly)", "On quarterly average AUM_mgmt"],
            ["Performance fee", f"{perf}%", "Only on new profits above the high water mark (HWM)"],
            ["Billing unit", "Per investor (consolidated)", "All authorized sub-accounts merged"],
            ["High water mark", "Required", "Tied to NAV_perf only; no performance fee until new high"],
        ],
        s2_title="2. Two bases: performance NAV vs. management AUM",
        s2_intro=(
            "Covered calls require investor BTC/ETH spot as collateral and margin. "
            "Spot affects strategy alpha and custody scale differently, so we use two bases:"
        ),
        s2_table=[
            ["Basis", "Includes investor collateral spot?", "Used for"],
            ["Performance NAV (NAV_perf)", "No", "Period P&amp;L, HWM, performance fee"],
            [
                "Management AUM (AUM_mgmt)",
                "Yes (authorized collateral spot in sub-accounts)",
                "Management fee average AUM",
            ],
        ],
        s2_1_title="2.1 Performance NAV (NAV_perf) — excludes collateral spot",
        s2_1_bullets=[
            "Definition: Sum of USDC-equivalent equity across authorized sub-accounts, <b>minus</b> agreed investor collateral spot (valued at Deribit index on settlement date).",
            "Includes: Options, margin, stablecoins, strategy realized/unrealized P&amp;L (USDC equivalent).",
            "Excludes: Investor-owned BTC/ETH held only for covered-call collateral and its price moves.",
            "Purpose: Performance fee rewards options strategy results, not spot beta.",
        ],
        s2_2_title="2.2 Management AUM (AUM_mgmt) — includes collateral spot",
        s2_2_bullets=[
            "Definition: NAV_perf <b>plus</b> agreed collateral spot in sub-accounts (USDC equivalent).",
            "Rationale: Collateral uses margin and operational resources; the manager oversees overall risk.",
            f"Formula: Quarterly management fee = average AUM_mgmt × {mgmt}% × (days in quarter / 365).",
        ],
        s3_title="3. Multi-currency valuation (BTC / ETH / USDC)",
        s3_intro=(
            "Deribit accounts are segregated by collateral currency (BTC, ETH, USDC books). "
            "All billing uses official data at the <b>settlement snapshot</b>, converted to USDC equivalent."
        ),
        s3_table=[
            ["Asset", "Valuation"],
            ["USDC and linear legs", "USDC balance"],
            ["BTC book equity", "BTC equity × BTC index (USD) on settlement date"],
            ["ETH book equity", "ETH equity × ETH index (USD) on settlement date"],
            ["Investor collateral spot", "Quantity × same index (same basis as NAV_perf deduction)"],
        ],
        s3_1_title="3.1 Consolidated formulas (conceptual)",
        s3_formulas=(
            "NAV_perf = Σ sub-account USDC-equivalent equity − investor collateral spot (USDC equivalent)<br/>"
            "AUM_mgmt = NAV_perf + investor collateral spot (USDC equivalent)<br/>"
            "Period P&amp;L (performance) = NAV_perf,end − NAV_perf,start − net subscription adjustment"
        ),
        s3_flow_note=(
            "Net subscription adjustment: deposits increase the baseline, withdrawals decrease it; "
            "confirmed per Deribit transaction log and mutual acknowledgment."
        ),
        s4_title=f"4. Performance fee ({perf}%) and high water mark",
        s4_formulas=(
            "Distributable profit = max(0, NAV_perf,end − HWM − net subscription adjustment for the quarter)<br/>"
            f"Quarterly performance fee = distributable profit × {perf}%<br/>"
            "HWM after settlement = NAV_perf,end − quarterly performance fee (post-fee HWM; management fee separate)"
        ),
        s4_note=(
            "HWM tracks <b>NAV_perf only</b>, <b>not</b> collateral spot. A drop in BTC/ETH spot does not directly "
            "lower HWM; options profits can still trigger a performance fee when NAV_perf makes a new high. "
            "Subscriptions and partial redemptions follow the IMA or side letter."
        ),
        s5_title="5. Collection: Fee sub-account reconciliation + main-account withdrawal",
        s5_intro=(
            "Fees are <b>not</b> deducted from strategy sub-accounts. The investor opens a separate <b>Fee</b> sub-account "
            "for reconciliation. After quarter-end the manager trades spot profits to USDC/USDT and transfers from the "
            "<b>strategy</b> sub-account API (wallet read/write) to the Fee sub; "
            "after both parties confirm, the investor withdraws from the <b>main account</b>. "
            "Fee sub-account API is Account=read only."
        ),
        s5_1_title="5.1 Investor obligations",
        s5_1_items=[
            "Create a dedicated Fee sub-account (suggested name: fee_acc; at least 5 characters—do not use fee), separate from strategy subs;",
            "Create a Fee-only API key (Account=read, Wallet/Trade off) and deliver securely to the manager;",
            "Strategy sub-account API: Read + Trade + Wallet read/write (for API transfer to Fee sub);",
            "After invoice, reconcile with the manager and confirm;",
            "Withdraw from the <b>main account</b> to the manager's specified address to complete payment;",
            "Do not deliver main-account API keys to the manager;",
            "Late reconciliation or payment may trigger a pause on new entries per the IMA.",
        ],
        s5_2_title="5.2 Relation to NAV_perf",
        s5_2_body=(
            "NAV_perf and HWM consolidate <b>strategy</b> sub-accounts only; Fee sub-account balance is excluded. "
            "The Fee sub-account is for reconciliation only; actual collection is via main-account withdrawal."
        ),
        s5_3_title="5.3 Management and performance fees",
        s5_3_body=(
            f"Management fee on AUM_mgmt (including collateral spot); performance fee at {perf}% of distributable profit. "
            "Invoices are USDC-equivalent; the manager converts and transfers to the Fee sub for reconciliation; "
            "the investor pays by withdrawing from the main account after confirmation."
        ),
        s6_title="6. Settlement cycle and investor reports",
        s6_table=[
            ["Item", "Suggested terms"],
            ["Regular settlement", "Quarter-end (last day of Mar / Jun / Sep / Dec, UTC)"],
            ["Valuation time", "Settlement day 23:59 UTC snapshot"],
            ["Invoice issued", "Within 10 business days after quarter-end"],
            [
                "Payment due",
                "Within 5 business days after reconciliation, withdraw from <b>main account</b> to manager's address",
            ],
            ["Redemption", "Accrued fees settled on effective redemption date"],
        ],
        s6_report_title="Each quarterly report should include at least:",
        s6_report_bullets=[
            "NAV_perf and AUM_mgmt at quarter start and end;",
            "Collateral spot quantity and USDC-equivalent value (reconciled to NAV_perf deduction);",
            "Net subscriptions, distributable profit, HWM changes, management and performance fee detail;",
            "Per-book equity and settlement index prices;",
            "Actual collection currency and conversion notes (if any).",
        ],
        s7_title="7. Worked example (simplified)",
        s7_table=[
            ["Item", "Amount (USDC equivalent)"],
            ["HWM at quarter start (NAV_perf)", "100,000"],
            ["NAV_perf at quarter end", "106,000"],
            ["Collateral spot at quarter end (management only)", "40,000"],
            ["AUM_mgmt at quarter end", "146,000"],
            ["Net deposit in quarter", "2,000"],
            ["Distributable profit", "106,000 − 100,000 − 2,000 = 4,000"],
            [f"Performance fee ({perf}%)", "400"],
            [f"Management fee (avg AUM_mgmt ≈ 140,000, {mgmt}% p.a.)", "≈ 350"],
        ],
        s7_note="Illustrative only; actual amounts use settlement-day official balances and agreed rates.",
        s8_title="8. Risks and disclaimers",
        s8_bullets=[
            "Short-volatility option strategies carry risk of loss; this document is not a guarantee of return.",
            "HWM prevents double-charging performance fees; it does not guarantee NAV_perf exceeds prior highs.",
            "Multi-currency conversion and collection may be affected by index prices, liquidity, and exchange rules.",
            "Collateral spot price risk remains with the investor; performance fee excludes spot moves; management fee includes collateral scale.",
            "Past performance is not indicative of future results; seek tax and regulatory advice independently.",
            "Deribit official statements prevail in disputes; if this document conflicts with a signed agreement, the agreement controls.",
        ],
        faq_title="Appendix: FAQ — Does HWM include BTC/ETH spot?",
        faq_bullets=[
            "<b>No.</b> If HWM were tied to total equity including spot, a spot drawdown could block performance fees even when options are profitable.",
            "Under this program, HWM tracks NAV_perf only (collateral spot deducted). Spot declines do not directly lower HWM.",
            "Management fee has no HWM; it uses AUM_mgmt (including spot). Lower spot reduces management fee base (custody scale), not performance HWM.",
            "The agreement should fix collateral spot quantities and reconcile quarterly so NAV_perf stays consistent.",
        ],
        signature_block=(
            "Manager: _________________________   Contact: _________________________<br/>"
            "Investor acknowledgment: _____________   Date: _________________________"
        ),
        disclaimer=(
            "Fee logic described here aligns with the Deribit options strategy engine's book segregation and "
            "USDC-equivalent reporting. This document is not an official Deribit statement."
        ),
    )


def _register_cjk_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates: list[tuple[str, Path, int | None]] = [
        ("PingFang", Path("/System/Library/Fonts/PingFang.ttc"), 0),
        ("PingFang", Path("/System/Library/Fonts/Supplemental/PingFang.ttc"), 0),
        ("STHeiti", Path("/System/Library/Fonts/STHeiti Medium.ttc"), None),
        ("STHeiti", Path("/System/Library/Fonts/STHeiti Light.ttc"), None),
        (
            "NotoSansCJK",
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            0,
        ),
        (
            "NotoSansCJK",
            Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
            0,
        ),
    ]
    for name, path, sub_idx in candidates:
        if not path.exists():
            continue
        font_name = f"CJK_{name}"
        try:
            if path.suffix.lower() == ".ttc" and sub_idx is not None:
                pdfmetrics.registerFont(TTFont(font_name, str(path), subfontIndex=sub_idx))
            else:
                pdfmetrics.registerFont(TTFont(font_name, str(path)))
            return font_name
        except Exception:
            continue
    raise RuntimeError("No CJK font found. Install Noto Sans CJK or use macOS PingFang/STHeiti.")


def _p(text: str, *, allow_markup: bool = False) -> str:
    s = str(text).replace("&", "&amp;")
    if not allow_markup:
        s = s.replace("<", "&lt;").replace(">", "&gt;")
    return s


def _table(rows: list[list[str]], col_widths: list[float], font: str):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    def cell(txt: str, *, header: bool = False) -> Paragraph:
        return Paragraph(_p(txt), styles["TableHeader" if header else "TableCell"])

    data = [[cell(c, header=(i == 0)) for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_PALETTE["header_bg"])),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(_PALETTE["ink"])),
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_PALETTE["border"])),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return t


def build_pdf(out_path: Path, content: FeeDisclosureContent, *, font: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    global styles
    styles = getSampleStyleSheet()
    cjk = content.locale.startswith("zh")
    base_kw: dict = dict(fontName=font)
    if cjk:
        base_kw["wordWrap"] = "CJK"

    def add_style(name: str, parent: str, **kwargs) -> None:
        styles.add(ParagraphStyle(name=name, parent=styles[parent], **base_kw, **kwargs))

    add_style("TitleMain", "Title", fontSize=17, leading=22, spaceAfter=10, alignment=TA_CENTER)
    add_style(
        "Subtitle",
        "Normal",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor(_PALETTE["muted"]),
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    add_style("Body", "Normal", fontSize=10, leading=15, alignment=TA_JUSTIFY, spaceAfter=7)
    add_style("H2", "Heading2", fontSize=12, leading=16, spaceBefore=8, spaceAfter=5)
    add_style("H3", "Heading3", fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=4)
    add_style("BodyBullet", "Normal", fontSize=9.5, leading=14, leftIndent=14, spaceAfter=4)
    add_style("SmallMuted", "Normal", fontSize=8.5, leading=12, textColor=colors.HexColor(_PALETTE["muted"]))
    add_style("TableCell", "Normal", fontSize=9, leading=12, alignment=TA_LEFT)
    add_style("TableHeader", "Normal", fontSize=9, leading=12, alignment=TA_LEFT)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.6 * inch,
        title=content.pdf_title_meta,
    )
    c = content
    story: list = []

    story.append(Paragraph(_p(c.doc_title), styles["TitleMain"]))
    story.append(Paragraph(_p(c.subtitle), styles["Subtitle"]))
    story.append(Paragraph(_p(c.intro), styles["Body"]))

    story.append(Paragraph(_p(c.s1_title), styles["H2"]))
    story.append(_table(c.s1_table, [1.35 * inch, 1.55 * inch, 3.35 * inch], font))
    story.append(Spacer(1, 10))

    story.append(Paragraph(_p(c.s2_title), styles["H2"]))
    story.append(Paragraph(_p(c.s2_intro), styles["Body"]))
    story.append(_table(c.s2_table, [1.55 * inch, 1.45 * inch, 3.25 * inch], font))
    story.append(Spacer(1, 8))
    story.append(Paragraph(_p(c.s2_1_title, allow_markup=True), styles["H3"]))
    for line in c.s2_1_bullets:
        story.append(Paragraph(_p(f"• {line}", allow_markup=True), styles["BodyBullet"]))
    story.append(Paragraph(_p(c.s2_2_title, allow_markup=True), styles["H3"]))
    for line in c.s2_2_bullets:
        story.append(Paragraph(_p(f"• {line}", allow_markup=True), styles["BodyBullet"]))

    story.append(PageBreak())

    story.append(Paragraph(_p(c.s3_title), styles["H2"]))
    story.append(Paragraph(_p(c.s3_intro, allow_markup=True), styles["Body"]))
    story.append(_table(c.s3_table, [2.0 * inch, 4.35 * inch], font))
    story.append(Spacer(1, 8))
    story.append(Paragraph(_p(c.s3_1_title, allow_markup=True), styles["H3"]))
    story.append(Paragraph(_p(c.s3_formulas, allow_markup=True), styles["Body"]))
    story.append(Paragraph(_p(c.s3_flow_note), styles["Body"]))

    story.append(Paragraph(_p(c.s4_title), styles["H2"]))
    story.append(Paragraph(_p(c.s4_formulas, allow_markup=True), styles["Body"]))
    story.append(Paragraph(_p(c.s4_note, allow_markup=True), styles["Body"]))

    story.append(Paragraph(_p(c.s5_title), styles["H2"]))
    story.append(Paragraph(_p(c.s5_intro, allow_markup=True), styles["Body"]))
    story.append(Paragraph(_p(c.s5_1_title, allow_markup=True), styles["H3"]))
    for i, line in enumerate(c.s5_1_items, start=1):
        story.append(Paragraph(_p(f"{i}. {line}"), styles["BodyBullet"]))
    story.append(Paragraph(_p(c.s5_2_title, allow_markup=True), styles["H3"]))
    story.append(Paragraph(_p(c.s5_2_body, allow_markup=True), styles["Body"]))
    story.append(Paragraph(_p(c.s5_3_title, allow_markup=True), styles["H3"]))
    story.append(Paragraph(_p(c.s5_3_body), styles["Body"]))

    story.append(PageBreak())

    story.append(Paragraph(_p(c.s6_title), styles["H2"]))
    story.append(_table(c.s6_table, [1.5 * inch, 4.85 * inch], font))
    story.append(Spacer(1, 10))
    story.append(Paragraph(_p(c.s6_report_title), styles["H3"]))
    for line in c.s6_report_bullets:
        story.append(Paragraph(_p(f"• {line}"), styles["BodyBullet"]))

    story.append(Paragraph(_p(c.s7_title), styles["H2"]))
    story.append(_table(c.s7_table, [2.4 * inch, 3.95 * inch], font))
    story.append(Spacer(1, 6))
    story.append(Paragraph(_p(c.s7_note), styles["SmallMuted"]))

    story.append(Paragraph(_p(c.faq_title, allow_markup=True), styles["H2"]))
    for line in c.faq_bullets:
        story.append(Paragraph(_p(f"• {line}", allow_markup=True), styles["BodyBullet"]))

    story.append(Paragraph(_p(c.s8_title), styles["H2"]))
    for line in c.s8_bullets:
        story.append(Paragraph(_p(f"• {line}"), styles["BodyBullet"]))

    story.append(Spacer(1, 12))
    story.append(Paragraph(_p(c.signature_block, allow_markup=True), styles["Body"]))
    story.append(Paragraph(_p(c.disclaimer), styles["SmallMuted"]))

    footer_tpl = c.footer_page

    def _footer(canvas, doc_obj) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor(_PALETTE["muted"]))
        pw, _ = doc_obj.pagesize
        canvas.drawRightString(
            pw - doc_obj.rightMargin,
            0.42 * inch,
            footer_tpl.format(n=canvas.getPageNumber()),
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "output" / "pdf"
    zh_font = _register_cjk_font()
    zh_path = out_dir / "Investor_Fee_Disclosure_zh-TW.pdf"
    en_path = out_dir / "Investor_Fee_Disclosure_en.pdf"
    build_pdf(zh_path, _zh_content(), font=zh_font)
    build_pdf(en_path, _en_content(), font="Helvetica")
    print(f"Wrote {zh_path}")
    print(f"Wrote {en_path}")


if __name__ == "__main__":
    main()
