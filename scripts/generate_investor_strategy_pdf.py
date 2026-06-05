#!/usr/bin/env python3
"""Generate English investor brief PDF for Deribit options strategies (reportlab + matplotlib)."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import matplotlib

# --- Visual theme (single source of truth) ------------------------------------
_PALETTE = {
    "ink": "#0f172a",
    "muted": "#64748b",
    "line": "#0f766e",
    "line_cc": "#5b21b6",
    "profit": "#2dd4bf",
    "loss": "#fb7185",
    "band_pos": "#99f6e4",
    "band_neg": "#ffe4e6",
    "grid": "#e2e8f0",
    "strike": "#475569",
    "card": "#f8fafc",
    "border": "#e2e8f0",
}
_STRATEGY_COLORS = ("#e11d48", "#0f766e", "#4f46e5")  # naked, bull spread, covered

# -----------------------------------------------------------------------------
# Illustrative payoff / chart inputs — edit here only (PDF figures read from this).
# -----------------------------------------------------------------------------
ILLUSTRATION: dict = {
    "short_put": {
        "strike": 100.0,
        "premium": 3.0,
        "price_min": 72.0,
        "price_max": 128.0,
        "title": "Naked short put",
        "subtitle": "Expiry payoff sketch (normalized units; not a forecast).",
        "y_label": "P/L at expiry (normalized)",
        "line_color": "#0f766e",
        "footer_template": "Scenario: strike K={strike:g}, premium +{premium:g} collected",
    },
    "bull_put_spread": {
        "k_long": 90.0,
        "k_short": 100.0,
        "credit": 3.0,
        "price_min": 72.0,
        "price_max": 128.0,
        "title": "Bull put spread (credit)",
        "subtitle": "Long put at lower strike hedges the short put (same expiry).",
        "y_label": "P/L at expiry (normalized)",
        "line_color": "#0369a1",
        "footer_template": "Scenario: long {k_long:g} | short {k_short:g} | net credit +{credit:g}",
    },
    "covered_call": {
        "call_strike": 110.0,
        "premium_btc": 0.14,
        "premium_usd": 14.0,
        "price_min": 72.0,
        "price_max": 138.0,
        "title": "Covered call (coin-denominated)",
        "subtitle": "Incremental BTC vs holding spot only; inverse-style cash settlement in coin.",
        "y_label": "Extra BTC vs spot-only hold at expiry",
        "x_label": "Underlying price at expiry (USD per coin)",
        "line_color": "#5b21b6",
        "footer_template": (
            "Scenario: short call K={call_strike:g}, premium +{premium_btc:g} BTC; "
            "ITM settlement approx. max(S-K,0)/S BTC paid from seller."
        ),
        "title_usd": "Covered call (USD / linear-style)",
        "subtitle_usd": ("Long 1 BTC + short call + premium: terminal USD value min(S,K)+P, capped at K+P when S≥K."),
        "y_label_usd": "Position value at expiry (USD)",
        "line_color_usd": "#0369a1",
        "footer_template_usd": (
            "Scenario: short linear call K={call_strike:g}, premium +{premium_usd:g} USD; "
            "= S+P when S≤K, = K+P when S≥K (classic covered-call cap)."
        ),
    },
}

ILLUSTRATION_COMPARISON: dict = {
    "title": "Relative profile (qualitative)",
    "subtitle": "Illustrative ranks for intuition only; not performance predictions.",
    "metrics": ["Tail exposure", "Framed yield (narrative)", "Loss definition"],
    "strategies": ["Naked short", "Bull put spread", "Covered call"],
    # rows = metrics, cols = strategies (same order as strategies above)
    "scores": [
        [5.0, 2.0, 3.0],
        [3.0, 2.0, 5.0],
        [1.0, 5.0, 3.0],
    ],
}


def _format_scenario_footer(cfg: dict) -> str:
    tmpl = str(cfg["footer_template"])
    fields = {k: v for k, v in cfg.items() if k != "footer_template"}
    return tmpl.format(**fields)


def _configure_matplotlib(repo_root: Path) -> None:
    mpl_dir = repo_root / ".mplconfig"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
    matplotlib.use("Agg")


def _apply_base_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": _PALETTE["card"],
            "axes.edgecolor": _PALETTE["border"],
            "axes.linewidth": 1.0,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": _PALETTE["grid"],
            "grid.linestyle": "-",
            "grid.linewidth": 0.8,
            "grid.alpha": 1.0,
            "axes.titlesize": 12,
            "axes.titleweight": "600",
            "axes.labelsize": 10,
            "axes.labelcolor": _PALETTE["ink"],
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "xtick.color": _PALETTE["muted"],
            "ytick.color": _PALETTE["muted"],
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "pdf.fonttype": 42,
            "savefig.dpi": 180,
            "savefig.facecolor": "white",
        }
    )


def _style_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_PALETTE["border"])
    ax.spines["bottom"].set_color(_PALETTE["border"])
    ax.grid(True, axis="y", color=_PALETTE["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def _fig_to_png_bytes(fig, *, full_figure: bool = True) -> BytesIO:
    """Export PNG. Use full_figure=True so titles in figure margins are not cropped by bbox tight."""
    import matplotlib.pyplot as plt

    buf = BytesIO()
    kw = dict(format="png", dpi=160, facecolor="white", edgecolor="none")
    if full_figure:
        kw["bbox_inches"] = None  # entire Figure artists; avoids tight-crop shifts / overlaps
    else:
        kw["bbox_inches"] = "tight"
        kw["pad_inches"] = 0.08
    fig.savefig(buf, **kw)
    plt.close(fig)
    buf.seek(0)
    return buf


def _rl_image_from_png(buf: BytesIO, max_width: float, max_height: float):
    """Scale PNG for ReportLab while preserving aspect ratio and capping height."""
    from PIL import Image as PILImage
    from reportlab.platypus import Image as RLImage

    raw = buf.getvalue()
    pil = PILImage.open(BytesIO(raw))
    pw, ph = pil.size
    if pw <= 0 or ph <= 0:
        return RLImage(BytesIO(raw), width=max_width, height=max_height)

    w = float(max_width)
    h = w * float(ph) / float(pw)
    if h > float(max_height):
        h = float(max_height)
        w = h * float(pw) / float(ph)

    return RLImage(BytesIO(raw), width=w, height=h)


def _payoff_panel(
    *,
    S,
    pl,
    title: str,
    subtitle: str,
    footer_line: str,
    y_label: str,
    strike_x: list[float],
    strike_styles: list[str],
    line_color: str,
    x_label: str = "Underlying price at expiry",
) -> BytesIO:
    import textwrap

    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D

    _apply_base_style()

    subtitle_wrapped = textwrap.fill(subtitle.strip(), width=88)

    fig = plt.figure(figsize=(6.55, 3.92))
    # Three rows: header | plot | caption band (matplotlib never draws into row 3, so labels cannot overlap ticks).
    gs = fig.add_gridspec(
        3,
        1,
        height_ratios=[0.26, 1.0, 0.22],
        hspace=0.30,
        left=0.10,
        right=0.99,
        top=0.96,
        bottom=0.10,
    )

    ax_head = fig.add_subplot(gs[0])
    ax_head.set_facecolor("white")
    ax_head.axis("off")
    ax_head.text(
        0.0,
        1.0,
        title,
        transform=ax_head.transAxes,
        ha="left",
        va="top",
        fontsize=11,
        fontweight="600",
        color=_PALETTE["ink"],
    )
    ax_head.text(
        0.0,
        0.22,
        subtitle_wrapped,
        transform=ax_head.transAxes,
        ha="left",
        va="top",
        fontsize=8.2,
        color=_PALETTE["muted"],
    )

    ax = fig.add_subplot(gs[1])
    ax.set_facecolor(_PALETTE["card"])
    _style_axes(ax)

    ax.axhline(0.0, color=_PALETTE["muted"], linewidth=1.0, zorder=1)
    for x, sty in zip(strike_x, strike_styles, strict=False):
        ax.axvline(x, color=_PALETTE["strike"], linestyle=sty, linewidth=1.15, zorder=2, alpha=0.85)

    ax.plot(S, pl, color=line_color, linewidth=2.35, zorder=4, solid_capstyle="round")
    ax.fill_between(S, 0.0, pl, where=(pl >= 0), color=_PALETTE["band_pos"], alpha=0.55, zorder=3, linewidth=0)
    ax.fill_between(S, 0.0, pl, where=(pl < 0), color=_PALETTE["band_neg"], alpha=0.65, zorder=3, linewidth=0)

    y_min, y_max = float(np.min(pl)), float(np.max(pl))
    pad = max(1.05, (y_max - y_min) * 0.07)
    ax.set_ylim(y_min - pad, y_max + pad)

    ax.set_xlabel("")
    ax.set_ylabel(y_label, labelpad=6, fontsize=9)
    ax.tick_params(axis="x", pad=6)
    ax.margins(x=0)

    handles = [
        Line2D([0], [0], color=line_color, lw=2.35, label="Payoff at expiry"),
        Line2D([0], [0], color=_PALETTE["strike"], linestyle="--", lw=1.2, label="Key strikes"),
    ]
    leg = ax.legend(
        handles=handles,
        loc="upper right",
        frameon=True,
        fancybox=False,
        edgecolor=_PALETTE["border"],
        facecolor="white",
        fontsize=7.6,
        borderpad=0.55,
    )
    leg.get_frame().set_linewidth(0.8)

    x_label_wrapped = textwrap.fill(x_label.strip(), width=76)
    footer_wrapped = textwrap.fill(footer_line.strip(), width=92)
    ax_cap = fig.add_subplot(gs[2])
    ax_cap.set_facecolor("white")
    ax_cap.axis("off")
    ax_cap.text(
        0.5,
        1.0,
        x_label_wrapped,
        transform=ax_cap.transAxes,
        ha="center",
        va="top",
        fontsize=9,
        color=_PALETTE["ink"],
    )
    ax_cap.text(
        0.5,
        0.45,
        footer_wrapped,
        transform=ax_cap.transAxes,
        ha="center",
        va="top",
        fontsize=7.2,
        color=_PALETTE["muted"],
        linespacing=1.35,
    )

    return _fig_to_png_bytes(fig, full_figure=True)


def payoff_short_put_png() -> BytesIO:
    import numpy as np

    cfg = ILLUSTRATION["short_put"]
    K = float(cfg["strike"])
    prem = float(cfg["premium"])
    S = np.linspace(float(cfg["price_min"]), float(cfg["price_max"]), 400)
    pl = prem - np.maximum(K - S, 0.0)
    footer = _format_scenario_footer(cfg)

    return _payoff_panel(
        S=S,
        pl=pl,
        title=str(cfg["title"]),
        subtitle=str(cfg["subtitle"]),
        footer_line=footer,
        y_label=str(cfg["y_label"]),
        strike_x=[K],
        strike_styles=["--"],
        line_color=str(cfg["line_color"]),
    )


def payoff_bull_put_spread_png() -> BytesIO:
    import numpy as np

    cfg = ILLUSTRATION["bull_put_spread"]
    k_long = float(cfg["k_long"])
    k_short = float(cfg["k_short"])
    credit = float(cfg["credit"])
    S = np.linspace(float(cfg["price_min"]), float(cfg["price_max"]), 400)
    pl = credit - np.maximum(k_short - S, 0.0) + np.maximum(k_long - S, 0.0)
    footer = _format_scenario_footer(cfg)

    return _payoff_panel(
        S=S,
        pl=pl,
        title=str(cfg["title"]),
        subtitle=str(cfg["subtitle"]),
        footer_line=footer,
        y_label=str(cfg["y_label"]),
        strike_x=[k_long, k_short],
        strike_styles=[":", "--"],
        line_color=str(cfg["line_color"]),
    )


def payoff_covered_call_png() -> BytesIO:
    import numpy as np

    cfg = ILLUSTRATION["covered_call"]
    K = float(cfg["call_strike"])
    prem_btc = float(cfg["premium_btc"])
    S = np.linspace(float(cfg["price_min"]), float(cfg["price_max"]), 400)
    # Coin/BTC-denominated view (inverse-style): incremental BTC vs holding 1 coin without the short call.
    # Short inverse call ITM settlement paid in coin ~ max(S-K,0)/S per unit BTC notional (illustrative).
    pl = prem_btc - np.maximum(S - K, 0.0) / S
    footer = _format_scenario_footer(cfg)

    return _payoff_panel(
        S=S,
        pl=pl,
        title=str(cfg["title"]),
        subtitle=str(cfg["subtitle"]),
        footer_line=footer,
        y_label=str(cfg["y_label"]),
        strike_x=[K],
        strike_styles=["--"],
        line_color=str(cfg["line_color"]),
        x_label=str(cfg.get("x_label", "Underlying price at expiry")),
    )


def payoff_covered_call_usd_png() -> BytesIO:
    """USDC-linear style: covered package value at expiry = min(S,K)+premium (caps at K+premium when S≥K)."""
    import numpy as np

    cfg = ILLUSTRATION["covered_call"]
    K = float(cfg["call_strike"])
    prem_usd = float(cfg["premium_usd"])
    S = np.linspace(float(cfg["price_min"]), float(cfg["price_max"]), 400)
    pl = np.minimum(S, K) + prem_usd
    footer = _format_scenario_footer(
        {
            "call_strike": cfg["call_strike"],
            "premium_usd": cfg["premium_usd"],
            "footer_template": cfg["footer_template_usd"],
        }
    )

    return _payoff_panel(
        S=S,
        pl=pl,
        title=str(cfg["title_usd"]),
        subtitle=str(cfg["subtitle_usd"]),
        footer_line=footer,
        y_label=str(cfg["y_label_usd"]),
        strike_x=[K],
        strike_styles=["--"],
        line_color=str(cfg["line_color_usd"]),
        x_label=str(cfg.get("x_label", "Underlying price at expiry")),
    )


def comparison_radar_png() -> BytesIO:
    """Horizontal grouped bars: one row per metric, bars = strategies."""
    import textwrap

    import matplotlib.pyplot as plt
    import numpy as np

    _apply_base_style()

    cfg = ILLUSTRATION_COMPARISON
    metrics = list(cfg["metrics"])
    strat_labels = list(cfg["strategies"])
    data = np.array(cfg["scores"], dtype=float)

    fig = plt.figure(figsize=(6.55, 4.45))
    gs = fig.add_gridspec(
        2,
        1,
        height_ratios=[0.22, 1.0],
        hspace=0.20,
        left=0.26,
        right=0.99,
        top=0.92,
        bottom=0.30,
    )

    ax_head = fig.add_subplot(gs[0])
    ax_head.set_facecolor("white")
    ax_head.axis("off")
    ax_head.text(
        0.0,
        1.0,
        str(cfg["title"]),
        transform=ax_head.transAxes,
        ha="left",
        va="top",
        fontsize=11,
        fontweight="600",
        color=_PALETTE["ink"],
    )
    ax_head.text(
        0.0,
        0.15,
        textwrap.fill(str(cfg["subtitle"]), width=88),
        transform=ax_head.transAxes,
        ha="left",
        va="top",
        fontsize=8.2,
        color=_PALETTE["muted"],
    )

    ax = fig.add_subplot(gs[1])
    ax.set_facecolor(_PALETTE["card"])
    _style_axes(ax)
    ax.grid(True, axis="x", color=_PALETTE["grid"], linewidth=0.8)
    ax.grid(False, axis="y")

    y = np.arange(len(metrics)) * 1.22
    n_strat = len(strat_labels)
    bar_h = 0.26
    offsets = (np.arange(n_strat) - (n_strat - 1) / 2.0) * (bar_h + 0.04)

    for j in range(n_strat):
        ax.barh(
            y + offsets[j],
            data[:, j],
            height=bar_h,
            label=strat_labels[j],
            color=_STRATEGY_COLORS[j],
            alpha=0.92,
            edgecolor="white",
            linewidth=0.6,
        )

    ax.set_yticks(y)
    ax.set_yticklabels(metrics, fontsize=9.5, color=_PALETTE["ink"])
    ax.set_xlabel("Score (1 = low, 5 = high)", labelpad=10, color=_PALETTE["ink"], fontsize=9)
    ax.tick_params(axis="x", pad=4)
    ax.set_xlim(0, 5.8)

    ax.invert_yaxis()
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)

    leg = ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=3,
        frameon=True,
        fancybox=False,
        edgecolor=_PALETTE["border"],
        facecolor="white",
        fontsize=8,
        borderpad=0.55,
        columnspacing=0.9,
        handletextpad=0.35,
    )
    leg.get_frame().set_linewidth(0.8)

    return _fig_to_png_bytes(fig, full_figure=True)


def build_pdf(out_path: Path, repo_root: Path) -> None:
    _configure_matplotlib(repo_root)

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.52 * inch,
        bottomMargin=0.58 * inch,
        title="Deribit Options Strategies - Investor Brief",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleMain",
            parent=styles["Title"],
            fontSize=18,
            leading=22,
            spaceAfter=12,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Subtitle",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#444444"),
            alignment=TA_CENTER,
            spaceAfter=16,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyJustify",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2",
            parent=styles["Heading2"],
            fontSize=12,
            leading=15,
            spaceBefore=6,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H3",
            parent=styles["Heading3"],
            fontSize=10.5,
            leading=13,
            spaceBefore=7,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Caption",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#475569"),
            alignment=TA_CENTER,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallMuted",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#555555"),
            alignment=TA_JUSTIFY,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyStrategy",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=11.5,
            alignment=TA_JUSTIFY,
            spaceAfter=5,
            textColor=colors.HexColor("#1e293b"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="StrategyKicker",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#64748b"),
            alignment=TA_LEFT,
            spaceBefore=0,
            spaceAfter=2,
            fontName="Helvetica",
        )
    )
    styles.add(
        ParagraphStyle(
            name="StrategyTitle",
            parent=styles["Heading2"],
            fontSize=15,
            leading=18,
            textColor=colors.HexColor("#0f172a"),
            alignment=TA_LEFT,
            spaceBefore=0,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        )
    )
    styles.add(
        ParagraphStyle(
            name="StrategySubtitle",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#64748b"),
            alignment=TA_LEFT,
            spaceAfter=12,
            fontName="Helvetica-Oblique",
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableCell",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            alignment=TA_LEFT,
            fontName="Helvetica",
            spaceBefore=0,
            spaceAfter=0,
            leftIndent=0,
            rightIndent=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableCellHeader",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            alignment=TA_LEFT,
            fontName="Helvetica-Bold",
            spaceBefore=0,
            spaceAfter=0,
            leftIndent=0,
            rightIndent=0,
            textColor=colors.black,
        )
    )

    # --- Layout: overview + naked short on page 1 | other strategies | summary ---
    payoff_img_max_w = 6.75 * inch
    payoff_img_max_h = 3.0 * inch
    payoff_img_max_h_compact = 2.52 * inch  # page 1: room for intro + chart + body
    compare_img_max_w = 6.82 * inch
    compare_img_max_h = 2.55 * inch
    pdf_total_pages = 4

    def add_payoff_figure(
        buf: BytesIO,
        caption: str,
        *,
        max_h: float | None = None,
        spacer_before: float = 12,
        spacer_after_caption: float = 14,
    ) -> None:
        cap_h = float(payoff_img_max_h if max_h is None else max_h)
        story.append(Spacer(1, spacer_before))
        story.append(_rl_image_from_png(buf, payoff_img_max_w, cap_h))
        story.append(Paragraph(caption, styles["Caption"]))
        story.append(Spacer(1, spacer_after_caption))

    def add_comparison_figure(buf: BytesIO, caption: str) -> None:
        story.append(Spacer(1, 8))
        story.append(_rl_image_from_png(buf, compare_img_max_w, compare_img_max_h))
        story.append(Paragraph(caption, styles["Caption"]))
        story.append(Spacer(1, 10))

    def append_strategy_opening(idx: int, title: str, subtitle: str) -> None:
        story.append(Paragraph(f"Strategy {idx} of 3", styles["StrategyKicker"]))
        story.append(Paragraph(title, styles["StrategyTitle"]))
        story.append(Paragraph(subtitle, styles["StrategySubtitle"]))

    story: list = []

    story.append(Paragraph("Deribit Options Strategies", styles["TitleMain"]))
    story.append(Paragraph("Investor Brief: Mechanics, Risks, and Return Profiles", styles["Subtitle"]))
    story.append(
        Paragraph(
            "<i>Automated strategy engine on BTC and ETH options (Deribit). "
            "For discussion only; not investment advice.</i>",
            styles["Subtitle"],
        )
    )

    story.append(Spacer(1, 18))

    story.append(
        Paragraph(
            "This brief summarizes three configurable strategies implemented in the Deribit Options "
            "Strategy Engine: <b>naked short</b>, <b>bull put spread</b>, and <b>covered call</b>. "
            "The engine scans linear USDC and inverse BTC/ETH-settled options, typically targeting roughly "
            "<b>10-21 days to expiration (DTE)</b>, with filters on delta, OTM, liquidity, spread ratio, "
            "APR, and margin. A regime framework (normal / elevated / crisis) pauses new risk in crisis; "
            "circuit breakers include hard stops, soft triggers (roll preferred), take-profit, and "
            "time-based exits. "
            "<b>Naked short</b> is introduced below on this page with an illustrative payoff; "
            "<b>bull put spread</b> and <b>covered call</b> follow on the next pages. "
            "A comparison chart and summary table close the brief.",
            styles["BodyJustify"],
        )
    )

    story.append(Spacer(1, 16))

    append_strategy_opening(
        1,
        "Naked short",
        "Single-leg premium collection; tail exposure is the highest of the three.",
    )
    add_payoff_figure(
        payoff_short_put_png(),
        "Figure 1. Illustrative naked short put expiry payoff (normalized units; not a forecast).",
        max_h=payoff_img_max_h_compact,
        spacer_before=10,
        spacer_after_caption=14,
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Mechanics</b>", styles["H3"]),
                Paragraph(
                    "Sell one out-of-the-money (OTM) option per leg and collect premium. Configuration can "
                    "restrict to short puts, short calls, or <b>both</b>; in <b>both</b> mode, put and call "
                    "candidates compete under the same ranking key for available slots (no reserved "
                    "allocation for calls).",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Key risks</b>", styles["H3"]),
                Paragraph(
                    "<b>Tail risk is the highest</b> among the three: a short put loses progressively as the "
                    "underlying falls through the strike; a short call loses as price rallies. Extreme moves "
                    "can stress margin and execution (spreads, slippage). Operational and model risk remain "
                    "(scan logic, rolls, exits).",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Return profile</b>", styles["H3"]),
                Paragraph(
                    "Premium income dominates when short volatility and direction are favorable. Within this "
                    "project narrative, expected yield is framed as <b>between covered call and bull put "
                    "spread</b>.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )

    story.append(PageBreak())

    append_strategy_opening(
        2,
        "Bull put spread",
        "Credit spread with defined downside versus a naked short put.",
    )
    add_payoff_figure(
        payoff_bull_put_spread_png(),
        "Figure 2. Illustrative bull put spread expiry payoff (normalized units; not a forecast).",
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Mechanics</b>", styles["H3"]),
                Paragraph(
                    "Buy a lower-strike long put (protection) and sell a higher-strike short put for a net "
                    "credit, same expiry. Maximum loss is conceptually capped at roughly the spread width "
                    "minus the net credit received (fees and slippage apply). The long leg is selected within "
                    "configured delta bounds and must sit below the short strike.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Key risks</b>", styles["H3"]),
                Paragraph(
                    "Downside is <b>bounded</b> versus a naked short put, but the portfolio can still realize "
                    "the full defined loss. Thin net credits amplify fee drag. Illiquid long legs can worsen "
                    "effective protection and exit costs.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Return profile</b>", styles["H3"]),
                Paragraph(
                    "Maximum profit per spread is typically the net credit if held to a benign outcome. In this "
                    "project framing, <b>expected return is the most conservative</b> of the three, traded "
                    "for capped loss.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )

    story.append(PageBreak())

    append_strategy_opening(
        3,
        "Covered call",
        "Spot inventory plus short call; Figure 3 shows coin-settled incremental BTC vs spot-only; "
        "Figure 4 shows linear USD terminal value of the covered package (capped at K + premium).",
    )
    add_payoff_figure(
        payoff_covered_call_png(),
        "Figure 3. Covered call in <b>coin terms</b> (inverse / coin-settled): extra BTC at expiry vs holding spot only "
        "(illustrative; not a forecast).",
        max_h=payoff_img_max_h_compact,
        spacer_before=10,
        spacer_after_caption=10,
    )
    add_payoff_figure(
        payoff_covered_call_usd_png(),
        "Figure 4. Covered call in <b>USD terms</b> (linear / USDC-settled): position value at expiry "
        "<i>min(S,K)+premium</i>, flat above K so upside is <b>capped at K + premium</b> (illustrative; not a forecast).",
        max_h=payoff_img_max_h_compact,
        spacer_after_caption=14,
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Mechanics</b>", styles["H3"]),
                Paragraph(
                    "Sell calls only when sufficient BTC or ETH inventory is already available in the account; "
                    "the engine does not auto-buy spot for cover or use perpetuals as a substitute. Optional "
                    "spot-exit flows can accompany in-the-money exits when enabled. "
                    "On Deribit, <b>inverse</b> options settle in coin (BTC or ETH): premium is collected in coin, "
                    "and when a short call expires in the money, intrinsic is settled as a coin outflow "
                    "(conceptually <i>about</i> max(S-K,0)/S coins per BTC of notional—Figure 3). "
                    "<b>Linear USDC</b> legs settle intrinsic in USDC; for one BTC covered, terminal USD value is "
                    "<i>S − max(S−K,0) + premium = min(S,K)+premium</i>, so when <i>S≥K</i> the payoff plateaus at "
                    "<b>K + premium</b> (Figure 4—not a comparison to naked spot). "
                    "The engine scans both quote styles; the two figures separate inverse vs linear accounting.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Key risks</b>", styles["H3"]),
                Paragraph(
                    "Upside participation is <b>capped</b> near the short call strike after strong rallies. "
                    "Spot inventory still bears downside price risk (premium provides partial cushion only). "
                    "Settlement mechanics may leave residual spot exposure after cash or coin settlement.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph("<b>Return profile</b>", styles["H3"]),
                Paragraph(
                    "Combines spot exposure with call premium. In this project narrative, <b>expected return "
                    "is the highest</b> among the three, alongside upside caps and inventory requirements.",
                    styles["BodyStrategy"],
                ),
            ]
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("Summary & comparison", styles["H2"]))
    story.append(Spacer(1, 3))
    add_comparison_figure(
        comparison_radar_png(),
        "Figure 5. Relative scores are illustrative ranks for intuition; they are not performance predictions.",
    )
    story.append(
        Paragraph(
            "Use Figure 5 as a <b>mental map</b>: bull put spreads emphasize bounded loss; naked shorts "
            "emphasize premium at the cost of tail exposure; covered calls blend inventory economics "
            "with call premium but cap upside.",
            styles["BodyStrategy"],
        )
    )

    story.append(Spacer(1, 6))
    story.append(Paragraph("At a glance", styles["H2"]))

    def _glance_cell(text: str, *, header: bool = False) -> Paragraph:
        # Paragraph + column width fixes occasional ReportLab string-cell overlap on grid lines.
        safe = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(safe, styles["TableCellHeader"] if header else styles["TableCell"])

    data = [
        [
            _glance_cell("Strategy", header=True),
            _glance_cell("Core idea", header=True),
            _glance_cell("Dominant risk", header=True),
            _glance_cell("Return (project framing)", header=True),
        ],
        [
            _glance_cell("Naked short"),
            _glance_cell("Single-leg OTM short put/call/both"),
            _glance_cell("Tail / extreme moves"),
            _glance_cell("Mid vs. the other two"),
        ],
        [
            _glance_cell("Bull put spread"),
            _glance_cell("Short put + lower long put"),
            _glance_cell("Defined loss to max width"),
            _glance_cell("Most conservative"),
        ],
        [
            _glance_cell("Covered call"),
            _glance_cell("Spot + short call"),
            _glance_cell("Capped upside; spot drawdowns"),
            _glance_cell("Highest expected"),
        ],
    ]
    # Slightly wider last column + extra gutter between cols 3–4 so text does not sit on the grid line.
    t = Table(
        data,
        colWidths=[1.1 * inch, 1.88 * inch, 1.62 * inch, 1.72 * inch],
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (2, 0), (2, -1), 8),
                ("LEFTPADDING", (3, 0), (3, -1), 8),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(
        Paragraph(
            "<b>Important limitations.</b> Crypto derivatives involve substantial volatility and "
            "margin risk. Exchange, operational, and smart-routing failures can occur. Past or "
            "simulated performance does not guarantee future results. Prospective participants "
            "should use test environments and independent professional advice as appropriate.",
            styles["SmallMuted"],
        )
    )
    story.append(
        Paragraph(
            "This document describes strategy mechanics aligned with the engine repository design. "
            "It is not affiliated with or endorsed by Deribit.",
            styles["SmallMuted"],
        )
    )

    def _page_footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        pw, _ph = doc.pagesize
        canvas.drawRightString(
            pw - doc.rightMargin,
            0.45 * inch,
            f"Page {canvas.getPageNumber()} / {pdf_total_pages}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out = repo_root / "output" / "pdf" / "Deribit_Strategies_Investor_Brief.pdf"
    build_pdf(out, repo_root)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
