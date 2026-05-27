#!/usr/bin/env python3
"""Generate investor onboarding PDF (zh-TW) via reportlab — no pandoc/LaTeX required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

DOC_VERSION = "1.0"
DOC_DATE = date.today().isoformat()

_PALETTE = {
    "ink": "#0f172a",
    "muted": "#64748b",
    "border": "#e2e8f0",
    "header_bg": "#f1f5f9",
}

_IMG_DIR = Path(__file__).resolve().parents[1] / "docs" / "img" / "onboarding"

# (filename, caption) — only images that exist under docs/img/onboarding/
_IMAGES: dict[str, tuple[str, str]] = {
    "s1_register": ("01-register.png", "Deribit 註冊頁"),
    "s2_deposit": ("03-wallet-deposit-menu.png", "Wallet → Deposit"),
    "s5_api_perm": (
        "10-api-strategy-permissions.png",
        "策略子帳 API 權限（Account=read、Trade=read_write、Wallet=read_write）",
    ),
    "s5_api_done": ("11-api-key-created.png", "API Key 建立成功（請妥善保存 Secret）"),
    "s6_fee_api": ("13-api-fee-permissions.png", "Fee 專戶 API（Account=read、Wallet=none、Trade=none）"),
    "s7_dashboard": ("14-cloudflare-access-login.png", "Cloudflare Access 登入"),
    "s3_margin": ("15.margin-selection.png", "Change Margin → Segregated Portfolio Margin"),
}


@dataclass(frozen=True)
class OnboardingContent:
    doc_title: str
    pdf_title_meta: str
    subtitle: str
    intro: str
    footer_page: str


def _zh_content() -> OnboardingContent:
    return OnboardingContent(
        doc_title="投資人前置作業指南",
        pdf_title_meta="投資人前置作業指南",
        subtitle=f"Deribit 期權策略組合｜版本 {DOC_VERSION}｜{DOC_DATE}",
        intro=(
            "本文件說明投資人需自行完成的 Deribit 帳戶設定步驟。正式權利義務以雙方簽署之投資管理協議為準。"
            "Deribit 為獨立交易所；能否開戶、入金、交易期權，以你所在地與 Deribit 當下規定為準。本指南不構成投資建議。"
        ),
        footer_page="投資人前置作業指南 · 第 {n} 頁",
    )


def _register_cjk_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates: list[tuple[str, Path, int | None]] = [
        ("PingFang", Path("/System/Library/Fonts/PingFang.ttc"), 0),
        ("PingFang", Path("/System/Library/Fonts/Supplemental/PingFang.ttc"), 0),
        ("STHeiti", Path("/System/Library/Fonts/STHeiti Medium.ttc"), None),
        ("STHeiti", Path("/System/Library/Fonts/STHeiti Light.ttc"), None),
        ("NotoSansCJK", Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),
        ("NotoSansCJK", Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"), 0),
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
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def _keep(flowables: list) -> object:
    """Keep flowables on one page when possible (tables, images, short blocks)."""
    from reportlab.platypus import KeepTogether, Spacer

    flat: list = []
    for item in flowables:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    flat = [f for f in flat if f is not None]
    if not flat:
        return Spacer(1, 0)
    if len(flat) == 1:
        return flat[0]
    return KeepTogether(flat)


def _kt_table(rows: list[list[str]], col_widths: list[float], font: str) -> object:
    from reportlab.platypus import Spacer

    return _keep([_table(rows, col_widths, font), Spacer(1, 4)])


def _scaled_image_fit(
    path: Path,
    *,
    usable_w: float,
    frame_h: float,
    portrait_max_h: float | None = None,
    portrait_max_w: float | None = None,
    landscape_max_h: float | None = None,
):
    from PIL import Image as PILImage
    from reportlab.lib.units import inch
    from reportlab.platypus import Image as RLImage

    pil = PILImage.open(path)
    pw, ph = pil.size
    if pw <= 0 or ph <= 0:
        return RLImage(str(path), width=usable_w, height=frame_h * 0.5)
    portrait = ph >= pw
    if portrait:
        max_h = portrait_max_h if portrait_max_h is not None else min(frame_h * 0.88, 7.4 * inch)
        max_w = portrait_max_w if portrait_max_w is not None else min(usable_w, 4.6 * inch)
        h = float(max_h)
        w = h * float(pw) / float(ph)
        if w > max_w:
            w = float(max_w)
            h = w * float(ph) / float(pw)
    else:
        w = float(usable_w)
        h = w * float(ph) / float(pw)
        cap = landscape_max_h if landscape_max_h is not None else min(3.25 * inch, frame_h * 0.4)
        if h > cap:
            h = float(cap)
            w = h * float(pw) / float(ph)
    return RLImage(str(path), width=w, height=h)


def _remaining_img_height(frame_h: float, reserved_inch: float) -> float:
    """Portrait/landscape cap from frame height minus estimated text on the same page."""
    from reportlab.lib.units import inch

    h = float(frame_h) - float(reserved_inch)
    return max(2.0 * inch, min(h, 7.2 * inch))


def _image_flowables(
    key: str,
    *,
    usable_w: float,
    frame_h: float,
    portrait_max_h: float | None = None,
    portrait_max_w: float | None = None,
    landscape_max_h: float | None = None,
) -> list:
    from reportlab.platypus import Paragraph, Spacer

    fname, caption = _IMAGES[key]
    path = _IMG_DIR / fname
    if not path.exists():
        raise FileNotFoundError(f"Missing onboarding image: {path}")
    return [
        Spacer(1, 2),
        _scaled_image_fit(
            path,
            usable_w=usable_w,
            frame_h=frame_h,
            portrait_max_h=portrait_max_h,
            portrait_max_w=portrait_max_w,
            landscape_max_h=landscape_max_h,
        ),
        Spacer(1, 1),
        Paragraph(_p(caption), styles["ImgCaption"]),
        Spacer(1, 2),
    ]


def _image_block(
    key: str,
    *,
    usable_w: float,
    frame_h: float,
    portrait_max_h: float | None = None,
    portrait_max_w: float | None = None,
    landscape_max_h: float | None = None,
    compact: bool = False,
) -> object:
    del compact  # compact spacing handled in chapter bundles
    return _keep(
        _image_flowables(
            key,
            usable_w=usable_w,
            frame_h=frame_h,
            portrait_max_h=portrait_max_h,
            portrait_max_w=portrait_max_w,
            landscape_max_h=landscape_max_h,
        )
    )


def _append_page(story: list, *, page_break: bool = True) -> None:
    from reportlab.platypus import PageBreak

    if page_break:
        story.append(PageBreak())


def _append_chapter(story: list, flowables: list, *, page_break: bool = True) -> None:
    _append_page(story, page_break=page_break)
    story.append(_keep(flowables))


def _steps_block(title: str, lines: list[str]) -> object:
    from reportlab.platypus import Spacer

    return _keep([_h3(title), Spacer(1, 2), *_numbered(lines), Spacer(1, 3)])


def _h2(title: str) -> object:
    from reportlab.platypus import Paragraph

    return Paragraph(_p(title), styles["H2"])


def _h3(title: str) -> object:
    from reportlab.platypus import Paragraph

    return Paragraph(_p(title), styles["H3"])


def _body(text: str) -> object:
    from reportlab.platypus import Paragraph

    return Paragraph(_p(text, allow_markup=True), styles["Body"])


def _bullets(lines: list[str]) -> list:
    from reportlab.platypus import Paragraph

    return [Paragraph(_p(f"• {line}", allow_markup=True), styles["BodyBullet"]) for line in lines]


def _numbered(lines: list[str]) -> list:
    from reportlab.platypus import Paragraph

    return [Paragraph(_p(f"{i}. {line}", allow_markup=True), styles["BodyBullet"]) for i, line in enumerate(lines, 1)]


def _note(lines: list[str]) -> object:
    from reportlab.platypus import Paragraph, Spacer

    return _keep(
        [
            Paragraph(_p("<b>注意</b>", allow_markup=True), styles["H3"]),
            Spacer(1, 2),
            *_bullets(lines),
            Spacer(1, 3),
        ]
    )


def _faq(q: str, a: str) -> object:
    from reportlab.platypus import Paragraph, Spacer

    return _keep(
        [
            Paragraph(_p(f"<b>Q：{q}</b>", allow_markup=True), styles["BodyBullet"]),
            Paragraph(_p(f"A：{a}", allow_markup=True), styles["BodyTight"]),
            Spacer(1, 3),
        ]
    )


def build_pdf(out_path: Path, content: OnboardingContent, *, font: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    global styles
    styles = getSampleStyleSheet()
    base_kw: dict = dict(fontName=font, wordWrap="CJK")

    def add_style(name: str, parent: str, **kwargs) -> None:
        styles.add(ParagraphStyle(name=name, parent=styles[parent], **base_kw, **kwargs))

    add_style("TitleMain", "Title", fontSize=16, leading=20, spaceAfter=6, alignment=TA_CENTER)
    add_style(
        "Subtitle",
        "Normal",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor(_PALETTE["muted"]),
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    add_style("Body", "Normal", fontSize=9.5, leading=13, alignment=TA_JUSTIFY, spaceAfter=4)
    add_style("BodyTight", "Normal", fontSize=9.5, leading=13, alignment=TA_JUSTIFY, spaceAfter=2)
    add_style("H2", "Heading2", fontSize=11.5, leading=14, spaceBefore=5, spaceAfter=3)
    add_style("H3", "Heading3", fontSize=10, leading=13, spaceBefore=3, spaceAfter=2)
    add_style("BodyBullet", "Normal", fontSize=9, leading=12, leftIndent=12, spaceAfter=2)
    add_style(
        "ImgCaption",
        "Normal",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor(_PALETTE["muted"]),
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    add_style("TableCell", "Normal", fontSize=8.5, leading=11, alignment=TA_LEFT)
    add_style("TableHeader", "Normal", fontSize=8.5, leading=11, alignment=TA_LEFT)
    add_style("Mono", "Normal", fontSize=8, leading=10, leftIndent=8, spaceAfter=1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    margin_lr = 0.5 * inch
    margin_tb = 0.45 * inch
    usable_w = A4[0] - 2 * margin_lr
    frame_h = A4[1] - 2 * margin_tb - 0.3 * inch

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=margin_lr,
        rightMargin=margin_lr,
        topMargin=margin_tb,
        bottomMargin=margin_tb,
        title=content.pdf_title_meta,
    )
    story: list = []

    story.append(
        _keep(
            [
                Paragraph(_p(content.doc_title), styles["TitleMain"]),
                Paragraph(_p(content.subtitle), styles["Subtitle"]),
                Paragraph(_p(content.intro), styles["Body"]),
                Spacer(1, 4),
            ]
        )
    )

    # --- 開始前 ---
    story.append(_h2("開始前：先搞懂三個名詞"))
    story.append(
        _kt_table(
            [
                ["名詞", "白話說明"],
                ["主帳戶", "你註冊 Deribit 時的總帳戶。入金通常先到這裡。"],
                ["策略子帳", "主帳底下的分戶，一個策略一個（如 naked）。自動交易只用這裡的 API。"],
                [
                    "費用專戶（Fee account）",
                    "獨立子帳（建議名稱 fee）。季結算後管理方劃轉 USDC/USDT 供對帳；你確認後自主帳提幣至管理方指定地址。",
                ],
                ["API Key", "策略子帳與 Fee 專戶各用一把，權限不同（見下文）。"],
            ],
            [1.2 * inch, usable_w - 1.2 * inch],
            font,
        )
    )
    story.append(
        _keep(
            [
                _h3("建議順序"),
                Spacer(1, 2),
                *_numbered(
                    [
                        "註冊 Deribit 並完成身分驗證",
                        "入金到主帳戶（注意幣種與鏈，見第二節）",
                        "建立策略子帳 + Fee 專戶，並將各帳設為 Segregated Portfolio Margin",
                        "從主帳把資金劃到各策略子帳",
                        "在每個策略子帳建 API Key（account:read + trade:read_write + wallet:read_write）",
                        "在 Fee 專戶建 API Key（account:read；Wallet、Trade 均 none）",
                        "提供儀表板登入用 Email（Cloudflare 白名單）",
                        "填寫交接清單（第八節）交給管理方",
                    ]
                ),
                Spacer(1, 4),
            ]
        )
    )

    # --- 一、註冊（整章同一頁；圖高依同頁文字自動計算）---
    ch1_img_h = min(_remaining_img_height(frame_h, 2.7 * inch), 5.6 * inch)
    _append_chapter(
        story,
        [
            _h2("一、註冊主帳戶與安全設定"),
            _h3("1.1 註冊"),
            *_numbered(
                [
                    "開啟註冊連結（推薦碼）：https://www.deribit.com/?reg=20929.3875",
                    "點 Register，用 Email 註冊並設定強密碼",
                    "到信箱點驗證連結，完成 Email 驗證",
                ]
            ),
            *_image_flowables(
                "s1_register",
                usable_w=usable_w,
                frame_h=frame_h,
                portrait_max_h=ch1_img_h,
                portrait_max_w=min(usable_w, 4.5 * inch),
            ),
            _h3("1.2 身分驗證（KYC）"),
            *_numbered(
                [
                    "登入後完成 Verify identity / KYC",
                    "審核通過前可能無法入金或交易，請預留 1～數個工作天",
                    "審核完成後確認帳戶狀態為可交易",
                ]
            ),
            _h3("1.3 請務必開啟 2FA"),
            *_numbered(
                [
                    "進入 Account → Security",
                    "啟用 Two-Factor Authentication（2FA）",
                    "保存好 2FA 備用碼",
                ]
            ),
            Paragraph(_p("<b>注意</b>", allow_markup=True), styles["H3"]),
            *_bullets(
                [
                    "不要把登入密碼、2FA 備用碼交給任何人",
                    "不要把主帳戶的 API Key 交給策略方；後面只在子帳戶裡建 Key",
                ]
            ),
            Spacer(1, 2),
        ],
    )

    # --- 二、入金 ---
    story.append(PageBreak())
    story.append(_h2("二、入金（把資金轉進 Deribit）"))
    story.append(_body("資金會先進你的<b>主帳戶</b>。之後第三、四節才會把錢分到各子帳戶。"))
    story.append(
        _steps_block("2.1 找到入金頁面", ["登入 Deribit（確認是主帳戶）", "點 Wallet", "選 Deposit（入金／充值）"])
    )
    story.append(_image_block("s2_deposit", usable_w=usable_w, frame_h=frame_h))

    story.append(
        _keep(
            [
                _h3("2.2 選擇幣種與網路（很重要）"),
                Spacer(1, 2),
                Paragraph(
                    _p("外部錢包／交易所提現時，鏈必須與 Deribit 顯示<b>完全一致</b>。", allow_markup=True),
                    styles["Body"],
                ),
                Spacer(1, 3),
            ]
        )
    )
    story.append(
        _kt_table(
            [
                ["幣種", "Deribit 入金請選的鏈／網路"],
                ["BTC", "Bitcoin（BTC）鏈"],
                ["ETH", "Ethereum mainnet（以太坊主網）"],
                ["USDC", "Ethereum mainnet（以太坊主網，ERC-20）"],
            ],
            [1.0 * inch, usable_w - 1.0 * inch],
            font,
        )
    )
    story.append(
        _kt_table(
            [
                ["注意事項", "說明"],
                ["地址與網路要一致", "選錯鏈可能無法到帳或資產遺失"],
                ["先小額測試", "第一次建議先轉小額（例如 10～50 USDC）"],
                ["保留手續費", "從 Ethereum mainnet 提現時，帳上要留一點 ETH 當 Gas"],
            ],
            [1.35 * inch, usable_w - 1.35 * inch],
            font,
        )
    )

    story.append(
        _steps_block(
            "2.3 複製地址並從外部轉帳",
            [
                "在 Deposit 頁複製 Deribit 入金地址（或掃 QR Code）",
                "在交易所或錢包發起提現，選同一條鏈",
                "回到 Deribit → Wallet 確認餘額增加",
            ],
        )
    )
    story.append(_h3("2.4 各策略需要什麼幣？"))
    story.append(
        _kt_table(
            [
                ["策略", "子帳名稱", "你需要準備的資產"],
                ["裸賣期權（Naked short）", "naked", "僅 USDC（劃到子帳後作保證金）"],
                ["牛市看跌價差（Bull put spread）", "bull_put", "僅 USDC"],
                [
                    "備兌賣 Call（Covered call）",
                    "covered_call",
                    "僅 BTC 或 ETH 現貨作備兌與保證金（不需 USDC）；程式不會幫你買現貨",
                ],
            ],
            [2.0 * inch, 1.1 * inch, usable_w - 3.1 * inch],
            font,
        )
    )
    story.append(_note(["入金完成後，錢還在主帳；第四節才要劃到策略子帳"]))

    # --- 三、子帳 ---
    story.append(PageBreak())
    story.append(_h2("三、建立子帳戶（策略子帳 + Fee 專戶）"))
    story.append(_h3("3.1 為什麼要開子帳？"))
    story.extend(
        _bullets(
            [
                "不同策略分開資金",
                "每個子帳一把 API Key，程式只能動該子帳",
                "對帳、看績效比較清楚",
            ]
        )
    )
    story.append(
        _keep(
            [
                _h3("3.2 建立步驟"),
                Spacer(1, 2),
                *_numbered(
                    [
                        "確認登入的是主帳戶",
                        "打開 Account → Subaccounts",
                        "點 Create subaccount",
                        "依管理方清單建立子帳（見下表）",
                        "建立完成後確認子帳列表",
                    ]
                ),
                Spacer(1, 3),
            ]
        )
    )
    story.append(
        _kt_table(
            [
                ["用途", "建議子帳名稱"],
                ["備兌賣 Call", "covered_call"],
                ["裸賣期權", "naked"],
                ["牛市看跌價差", "bull_put"],
                ["費用專戶（必建）", "fee"],
            ],
            [2.2 * inch, usable_w - 2.2 * inch],
            font,
        )
    )
    story.append(
        _note(
            [
                "子帳名稱建立後往往不能隨意改名",
                "還沒劃轉資金前，子帳餘額為 0 是正常的",
            ]
        )
    )

    story.append(
        _keep(
            [
                _h3("3.3 保證金模式：一律設為 Segregated Portfolio Margin"),
                Spacer(1, 2),
                Paragraph(
                    _p(
                        "管理方策略以 <b>Segregated Portfolio Margin</b> 運作。請對主帳戶以及每一個子帳（含 fee）完成設定。",
                        allow_markup=True,
                    ),
                    styles["Body"],
                ),
                Spacer(1, 3),
            ]
        )
    )
    story.append(
        _keep(
            [
                Spacer(1, 2),
                *_numbered(
                    [
                        "切換到要設定的帳戶（主帳或某一子帳）",
                        "點選 My Account → Portfolio Margin",
                        "點 Change Margin",
                        "選擇 Segregated Portfolio Margin 並儲存",
                        "對下一個帳戶重複，直到主帳與所有子帳皆完成",
                    ]
                ),
                Spacer(1, 3),
            ]
        )
    )
    margin_img = _IMG_DIR / _IMAGES["s3_margin"][0]
    if margin_img.exists():
        story.append(_image_block("s3_margin", usable_w=usable_w, frame_h=frame_h))
    story.append(
        _note(
            [
                "若子帳仍為其他保證金模式，可能導致保證金計算與策略預期不符",
                "不確定時可在 Portfolio Margin 頁面查看，或截圖請管理方協助確認",
            ]
        )
    )

    # --- 四、劃轉 ---
    story.append(PageBreak())
    story.append(_h2("四、從主帳把資金劃到子帳"))
    story.append(_h3("4.1 找到劃轉功能"))
    story.extend(
        _numbered(
            [
                "找到 Transfer 或 Internal transfer（常見：Wallet → Transfer）",
                "From 選 Main account（主帳）",
                "To 選某一子帳，例如 naked",
            ]
        )
    )
    story.append(_h3("4.2 依策略劃轉（重複直到分完）"))
    story.extend(
        _numbered(
            [
                "選幣種（naked／bull_put：USDC；covered_call：BTC 或 ETH 現貨，不要劃 USDC）",
                "輸入金額並確認劃轉",
                "切換到該子帳檢視餘額",
            ]
        )
    )
    story.append(
        _note(
            [
                "劃轉在主帳與子帳之間通常即時、無鏈上手續費",
                "covered_call：僅劃入 BTC／ETH 現貨（現貨即保證金，不需 USDC）",
                "naked、bull_put：劃入 USDC 即可",
            ]
        )
    )

    # --- 五、策略 API（5.2 圖文不拆；5.3 另起一段）---
    api_img_h = min(_remaining_img_height(frame_h, 5.6 * inch), 3.85 * inch)
    _append_chapter(
        story,
        [
            _h2("五、策略子帳的 API Key（每個策略子帳各一把）"),
            _body("僅適用 naked / covered_call / bull_put 等策略子帳，<b>不適用</b> Fee 專戶。"),
            _h3("5.1 一定要先進入該策略子帳"),
            *_numbered(
                [
                    "切換到策略子帳（例如 naked），確認不是 Main account、不是 fee",
                    "再建立 Key",
                ]
            ),
            _h3("5.2 建立 API Key"),
            Paragraph(
                _p(
                    "路徑：Account → API → Add new key。先選 <b>Deribit-generated key</b>，再設定權限。",
                    allow_markup=True,
                ),
                styles["Body"],
            ),
            Spacer(1, 2),
            _table(
                [
                    ["欄位", "策略子帳請選"],
                    ["Block Trade / Block RFQ / Custody", "none"],
                    ["Account", "read"],
                    ["Trade", "read_write"],
                    ["Wallet", "read_write（季末 API 劃轉 USDC/USDT 至 Fee 專戶）"],
                ],
                [2.0 * inch, usable_w - 2.0 * inch],
                font,
            ),
            Spacer(1, 2),
            *_image_flowables(
                "s5_api_perm",
                usable_w=usable_w,
                frame_h=frame_h,
                portrait_max_h=api_img_h,
                portrait_max_w=min(usable_w, 4.0 * inch),
            ),
            *_image_flowables(
                "s5_api_done",
                usable_w=usable_w,
                frame_h=frame_h,
                landscape_max_h=2.15 * inch,
            ),
            Spacer(1, 2),
        ],
    )
    story.append(
        _keep(
            [
                _h3("5.3 交付給管理方（策略用）"),
                Spacer(1, 2),
                *[
                    Paragraph(_p(line), styles["Mono"])
                    for line in [
                        "類型：策略子帳",
                        "子帳名稱：naked",
                        "環境：mainnet",
                        "API Key：________________",
                        "API Secret：________________",
                    ]
                ],
                Paragraph(_p("每個策略子帳重複 5.1～5.3。"), styles["Body"]),
                Spacer(1, 4),
            ]
        )
    )

    # --- 六、Fee（獨立一頁；圖+兩張表+說明不拆頁）---
    fee_img_h = min(_remaining_img_height(frame_h, 4.9 * inch), 4.0 * inch)
    _append_chapter(
        story,
        [
            _h2("六、費用專戶（Fee account）與 API Key"),
            _body(
                "管理費、績效費<b>不會</b>從策略子帳自動扣款。請另建 fee 子帳作對帳專戶；管理方換幣劃轉後你確認，再自主帳提幣付費。"
            ),
            _h3("6.2 Fee 專戶 API Key"),
            _table(
                [
                    ["欄位", "Fee 專戶請選"],
                    ["Block Trade / Block RFQ / Custody", "none"],
                    ["Account", "read"],
                    ["Trade", "none"],
                    ["Wallet", "none"],
                ],
                [2.0 * inch, usable_w - 2.0 * inch],
                font,
            ),
            Spacer(1, 2),
            *_image_flowables(
                "s6_fee_api",
                usable_w=usable_w,
                frame_h=frame_h,
                portrait_max_h=fee_img_h,
                portrait_max_w=min(usable_w, 4.4 * inch),
            ),
            _table(
                [
                    ["欄位", "策略子帳", "Fee 專戶"],
                    ["Account", "read", "read"],
                    ["Trade", "read_write", "none"],
                    ["Wallet", "read_write", "none"],
                ],
                [1.35 * inch, 1.35 * inch, usable_w - 2.7 * inch],
                font,
            ),
            Spacer(1, 2),
            *_bullets(
                [
                    "Fee 專戶裡的穩定幣 = 已結算、供雙方對帳的應付費用",
                    "實際付費：確認對帳後自主帳提幣至管理方指定地址",
                    "勿把大額策略資金放在 Fee 專戶",
                ]
            ),
            Spacer(1, 2),
        ],
    )

    # --- 七、儀表板（獨立一頁）---
    ch7_img_h = _remaining_img_height(frame_h, 2.35 * inch)
    _append_chapter(
        story,
        [
            _h2("七、儀表板登入用 Email（Zero Trust）"),
            *_bullets(
                [
                    "提供平常使用的 Email 給管理方加入白名單",
                    "若用 Google 登入，請提供該 Google 帳號 Email",
                ]
            ),
            *_numbered(
                [
                    "用瀏覽器開啟管理方給你的 HTTPS 網址",
                    "通過 Cloudflare Access 登入",
                    "通過後查看投資人績效頁",
                ]
            ),
            *_image_flowables(
                "s7_dashboard",
                usable_w=usable_w,
                frame_h=frame_h,
                landscape_max_h=ch7_img_h,
            ),
            Paragraph(_p("<b>注意</b>", allow_markup=True), styles["H3"]),
            *_bullets(["不要把儀表板網址貼在公開場合"]),
            Spacer(1, 2),
        ],
    )

    # --- 八、交接清單 ---
    story.append(PageBreak())
    story.append(_h2("八、交接清單（填好交給管理方）"))
    checklist = [
        "【基本資料】姓名／暱稱、聯絡 Email、Deribit 註冊 Email",
        "【儀表板】白名單 Email、慣用登入方式",
        "【環境】正式環境 mainnet",
        "【保證金模式】主帳與所有子帳均已設為 Segregated Portfolio Margin",
        "【naked／bull_put】子帳名稱與已劃轉 USDC",
        "【covered_call】子帳名稱、備兌 BTC／ETH 數量（僅現貨，無 USDC）",
        "【Fee 專戶】已建立子帳、已交付 Fee API（Account=read，Wallet／Trade 均 none）",
        "【策略 API】已透過安全管道交付（含 wallet:read_write，供季末劃轉至 Fee 專戶）",
    ]
    story.extend(_bullets(checklist))

    # --- 九、請勿 ---
    story.append(_h2("九、請勿做的事（總整理）"))
    story.append(
        _kt_table(
            [
                ["請勿", "原因"],
                ["把主帳 API Key 交給管理方", "風險範圍過大"],
                ["策略子帳 API Key 外洩", "含 Wallet 權限，外洩可能被對外提幣"],
                ["把策略子帳 Key 當 Fee 專戶 Key", "權限設計不同"],
                ["在 Fee 專戶開啟 Trade", "Fee 專戶不應下單"],
                ["入金選錯鏈或填錯地址", "可能永久遺失"],
                ["子帳保證金模式未設為 Segregated Portfolio Margin", "與策略保證金計算不一致"],
                ["在 covered_call 子帳劃入 USDC", "此策略僅用 BTC／ETH 現貨作保證金"],
                ["策略子帳尚未劃資就要求實單", "無保證金可運作"],
            ],
            [2.5 * inch, usable_w - 2.5 * inch],
            font,
        )
    )

    # --- 十、FAQ ---
    story.append(_h2("十、常見問題"))
    story.append(_faq("主帳和子帳的錢可以互轉嗎？", "可以。用 Transfer 在主帳與子帳之間即時劃轉，通常不收鏈上費。"))
    story.append(
        _faq(
            "管理費／績效費怎麼付？",
            "季結算後管理方開帳單並劃轉 USDC/USDT 至 fee 子帳供對帳；你確認後自主帳提幣。不會從策略子帳自動扣。",
        )
    )
    story.append(
        _faq(
            "下拉選單怎麼選？",
            "策略子帳：Account=read、Trade=read_write、Wallet=read_write。Fee：Account=read、Wallet=none、Trade=none。",
        )
    )
    story.append(
        _faq(
            "保證金模式要怎麼設？",
            "My Account → Portfolio Margin → Change Margin → Segregated Portfolio Margin；主帳與所有子帳皆需設定。",
        )
    )
    story.append(
        _faq(
            "covered_call 子帳需要 USDC 嗎？",
            "不需要。僅劃入約定數量的 BTC 或 ETH 現貨；現貨同時作備兌與保證金。",
        )
    )

    footer_tpl = content.footer_page

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
    out_path = out_dir / "Investor_Onboarding_zh-TW.pdf"
    build_pdf(out_path, _zh_content(), font=zh_font)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
