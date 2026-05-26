"""PDF export for investor fee reports (ReportLab)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .investor_cash_flow import initial_spot_deduction_usdc, native_book_amount_to_usdc
from .investor_fee_report import (
    InitialFeeReportContext,
    SettlementFeeReportContext,
    _equity_native_for_book,
    _money,
    _native,
    _signed_native,
    _ts_fmt,
)
from .utils import to_decimal

# A4 content width with default fee-report side margins (18 mm each side).
_PDF_CONTENT_WIDTH_MM = 210 - 36


def _ts_fmt_pdf(ms: int) -> str:
    """Compact UTC timestamp for PDF tables (header already says Time (UTC))."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


# --- Initial report PDF (unchanged layout) -----------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Heading1"],
            fontSize=16,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=9, leading=12),
        "small": ParagraphStyle(
            "Small",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.grey,
        ),
    }


def _bullet(story: list, text: str, style: ParagraphStyle) -> None:
    story.append(Paragraph(text.replace("\n", "<br/>"), style))


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    data = [[k, v] for k, v in rows]
    table = Table(data, colWidths=[70 * mm, 100 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1a1a1a")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _grid_table(headers: list[str], rows: list[list[str]], *, col_widths: list[float]) -> Table:
    data = [headers, *rows]
    table = Table(data, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _net_flow_breakdown_table(
    native_by_book: dict[str, Decimal],
    *,
    index_by_ccy: dict[str, Decimal],
    cumulative_net_flow_usdc: Decimal,
    initial_hwm_nav_perf: Decimal,
) -> Table:
    label_style = ParagraphStyle(
        "NetFlowLabel",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
    )
    hwm_label_style = ParagraphStyle(
        "NetFlowHwmLabel",
        parent=label_style,
        fontName="Helvetica-Bold",
    )

    def _label(text: str, *, bold: bool = False) -> Paragraph:
        return Paragraph(text, hwm_label_style if bold else label_style)

    btc_native = native_by_book.get("BTC", Decimal("0"))
    eth_native = native_by_book.get("ETH", Decimal("0"))
    btc_usdc = native_book_amount_to_usdc(btc_native, "BTC", index_by_ccy)
    eth_usdc = native_book_amount_to_usdc(eth_native, "ETH", index_by_ccy)
    usdc_native = native_by_book.get("USDC", Decimal("0"))
    data: list[list[Any]] = [
        ["Line", "Native", "USDC equivalent"],
        [
            "USDC net subscription",
            _native(usdc_native, "USDC"),
            _money(native_book_amount_to_usdc(usdc_native, "USDC", index_by_ccy)),
        ],
        [
            _label("Initial spot (BTC)"),
            _native(btc_native, "BTC"),
            f"-{_money(btc_usdc)}",
        ],
        [
            _label("Initial spot (ETH)"),
            _native(eth_native, "ETH"),
            f"-{_money(eth_usdc)}",
        ],
        ["Net subscription total", "—", _money(cumulative_net_flow_usdc)],
        [_label("Initial HWM<br/>(NAV_perf)", bold=True), "—", _money(initial_hwm_nav_perf)],
    ]
    table = Table(data, colWidths=[48 * mm, 46 * mm, 38 * mm])
    total_row = len(data) - 2
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4")),
                ("BACKGROUND", (0, total_row), (-1, total_row), colors.HexColor("#f5f8fb")),
                ("BACKGROUND", (0, total_row + 1), (-1, total_row + 1), colors.HexColor("#e8f4e8")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ]
        )
    )
    return table


def _append_flow_section_pdf(
    story: list,
    flow_lines: tuple,
    *,
    section_num: int,
    styles: dict[str, ParagraphStyle],
    period_label: str | None = None,
) -> None:

    title = f"{section_num}. Cash flow detail"
    if period_label:
        title += f" ({period_label})"
    story.append(Paragraph(title, styles["h2"]))
    included = [row for row in flow_lines if row.included_in_subscription]
    subtotal = sum((row.usdc_equiv for row in included), Decimal("0"))
    story.append(Paragraph(f"Subtotal (USDC equivalent): {_money(subtotal)}", styles["body"]))
    rows = [
        [
            _ts_fmt_pdf(line.timestamp_ms),
            line.identity_label,
            line.flow_type,
            line.book,
            _signed_native(line.amount_native, line.book),
            _money(line.usdc_equiv),
        ]
        for line in included
    ]
    if not rows:
        rows = [["—", "—", "—", "—", "—", "—"]]
    # Time column needs ~40 mm; total width must not exceed printable area.
    story.append(
        _grid_table(
            ["Time (UTC)", "Account", "Type", "CCY", "Amount", "USDC"],
            rows,
            col_widths=[
                40 * mm,
                26 * mm,
                20 * mm,
                12 * mm,
                34 * mm,
                (_PDF_CONTENT_WIDTH_MM - 40 - 26 - 20 - 12 - 34) * mm,
            ],
        )
    )
    story.append(Spacer(1, 8))


def write_initial_fee_report_pdf(ctx: InitialFeeReportContext, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    story: list[Any] = []
    story.append(
        Paragraph(
            f"Investor Initial Fee Baseline — {ctx.display_name} ({ctx.investor_id})",
            styles["title"],
        )
    )
    story.append(Paragraph(f"Generated: {_ts_fmt(ctx.generated_at_ms)}", styles["small"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("1. Current equity", styles["h2"]))
    if ctx.live_nav:
        equity_by_book = {str(k).upper(): to_decimal(v) for k, v in (ctx.live_nav.get("equity_by_book") or {}).items()}
        equity_native_by_book = {
            str(k).upper(): to_decimal(v) for k, v in (ctx.live_nav.get("equity_native_by_book") or {}).items()
        }
        rows = [["Currency", "Native", "USDC equiv."]]
        for book in ("BTC", "ETH", "USDC"):
            native = _equity_native_for_book(
                book,
                equity_native_by_book=equity_native_by_book,
                equity_by_book=equity_by_book,
                index_by_ccy=ctx.index_by_ccy,
            )
            rows.append([book, _native(native, book), _money(equity_by_book.get(book, 0))])
        rows.append(["Total", "—", _money(ctx.live_nav["total_equity_usdc"])])
        story.append(_grid_table(rows[0], rows[1:], col_widths=[28 * mm, 50 * mm, 42 * mm]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. Fee rates", styles["h2"]))
    story.append(
        _kv_table(
            [
                ("Performance fee", f"{float(ctx.fee_config['performance_fee_rate']) * 100:.1f}%"),
                (
                    "Management fee (annual)",
                    f"{float(ctx.fee_config['management_fee_annual_rate']) * 100:.2f}%",
                ),
            ]
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("3. Net subscription & initial HWM", styles["h2"]))
    if ctx.baseline is not None:
        story.append(
            _net_flow_breakdown_table(
                ctx.baseline.net_flow_native_by_book,
                index_by_ccy=ctx.index_by_ccy,
                cumulative_net_flow_usdc=ctx.baseline.cumulative_net_flow_usdc,
                initial_hwm_nav_perf=ctx.baseline.initial_hwm_nav_perf,
            )
        )
    else:
        native_by_book: dict[str, Decimal] = {}
        for line in ctx.flow_lines:
            if line.included_in_subscription:
                native_by_book[line.book] = native_by_book.get(line.book, Decimal("0")) + line.amount_native
        cumulative = sum((line.usdc_equiv for line in ctx.flow_lines if line.included_in_subscription), Decimal("0"))
        _btc, _eth, _spot, initial_hwm = initial_spot_deduction_usdc(native_by_book, index_by_ccy=ctx.index_by_ccy)
        story.append(
            _net_flow_breakdown_table(
                native_by_book,
                index_by_ccy=ctx.index_by_ccy,
                cumulative_net_flow_usdc=cumulative,
                initial_hwm_nav_perf=initial_hwm,
            )
        )
    story.append(Spacer(1, 8))

    _append_flow_section_pdf(story, ctx.flow_lines, section_num=4, styles=styles)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Initial fee baseline — {ctx.investor_id}",
    )
    doc.build(story)
    return path


# --- Period settlement PDF (investor summary) ---------------------------------


def _signed_native_pdf(amount: Decimal, book: str) -> str:
    text = _native(amount, book)
    if amount > 0 and not text.startswith("+"):
        return f"+{text}"
    return text


def _signed_money_pdf(amount: Decimal | str) -> str:
    text = _money(amount)
    value = to_decimal(amount)
    if value > 0 and not text.startswith("+"):
        return f"+{text}"
    return text


def _usdc_equiv_total_pdf(book_map: dict[str, Decimal]) -> Decimal:
    return sum(book_map.values(), Decimal("0"))


def write_settlement_fee_report_pdf(
    ctx: SettlementFeeReportContext,
    path: Path,
    *,
    repo_root: Path | str,
) -> Path:
    from .investor_fee_report_period import build_investor_period_report

    report = build_investor_period_report(ctx, repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    story: list[Any] = []

    story.append(Paragraph(f"Period Statement — {report.display_name}", styles["title"]))
    story.append(Paragraph(f"Investor: {report.investor_id}", styles["small"]))
    story.append(Paragraph(f"Period: {report.period_label}", styles["small"]))
    story.append(Paragraph(f"Day A: {report.day_a.label}", styles["small"]))
    story.append(Paragraph(f"Day B: {report.day_b.label}", styles["small"]))
    story.append(Spacer(1, 8))

    def _balance_row(label: str, native: dict[str, Decimal]) -> list[str]:
        return [
            label,
            _native(native["BTC"], "BTC"),
            _native(native["ETH"], "ETH"),
            _native(native["USDC"], "USDC"),
        ]

    story.append(Paragraph("Account balances", styles["h2"]))
    story.append(
        _grid_table(
            ["", "BTC", "ETH", "USDC"],
            [
                _balance_row("Day A", report.day_a.native),
                _balance_row("Day B", report.day_b.native),
                _balance_row("Period deposits", report.deposit_native),
                _balance_row("Period withdrawals", report.withdraw_native),
                [
                    "Earned",
                    _signed_native_pdf(report.earned_native["BTC"], "BTC"),
                    _signed_native_pdf(report.earned_native["ETH"], "ETH"),
                    _signed_native_pdf(report.earned_native["USDC"], "USDC"),
                ],
                [
                    "USDC equivalent",
                    _money(report.earned_usdc["BTC"]),
                    _money(report.earned_usdc["ETH"]),
                    _money(report.earned_usdc["USDC"]),
                ],
            ],
            col_widths=[42 * mm, 32 * mm, 32 * mm, 32 * mm],
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("USDC-equivalent balances", styles["h2"]))
    story.append(
        _grid_table(
            ["", "BTC", "ETH", "USDC", "Total"],
            [
                [
                    f"Day A ({report.day_a.label})",
                    _money(report.day_a.usdc_equiv["BTC"]),
                    _money(report.day_a.usdc_equiv["ETH"]),
                    _money(report.day_a.usdc_equiv["USDC"]),
                    _money(_usdc_equiv_total_pdf(report.day_a.usdc_equiv)),
                ],
                [
                    f"Day B ({report.day_b.label})",
                    _money(report.day_b.usdc_equiv["BTC"]),
                    _money(report.day_b.usdc_equiv["ETH"]),
                    _money(report.day_b.usdc_equiv["USDC"]),
                    _money(_usdc_equiv_total_pdf(report.day_b.usdc_equiv)),
                ],
                [
                    "Change (B − A)",
                    _signed_money_pdf(report.usdc_equiv_change["BTC"]),
                    _signed_money_pdf(report.usdc_equiv_change["ETH"]),
                    _signed_money_pdf(report.usdc_equiv_change["USDC"]),
                    _signed_money_pdf(report.total_equity_change),
                ],
            ],
            col_widths=[42 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm],
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("NAV & performance fee (USDC)", styles["h2"]))
    story.append(
        _kv_table(
            [
                ("Total equity at Day A", _money(_usdc_equiv_total_pdf(report.day_a.usdc_equiv))),
                ("Total equity at Day B", _money(_usdc_equiv_total_pdf(report.day_b.usdc_equiv))),
                ("Collateral spot deducted (Day A)", f"-{_money(report.collateral_spot_start)}"),
                ("Collateral spot deducted (Day B)", f"-{_money(report.collateral_spot_end)}"),
                ("NAV_perf at Day A", _money(report.nav_perf_start)),
                ("NAV_perf at Day B", _money(report.nav_perf_end)),
                ("NAV_perf change (B − A)", _signed_money_pdf(report.nav_perf_change)),
                ("Net subscription in period", _money(report.net_flow_usdc)),
                ("Strategy P&L (period)", _money(report.total_usdc_earned)),
                ("HWM at period start", _money(report.hwm_start)),
                ("Distributable profit (above HWM)", _money(report.distributable_profit)),
                (
                    "Performance fee",
                    f"{_money(report.performance_fee)} ({float(report.performance_fee_rate) * 100:.0f}%)",
                ),
                ("HWM after fee", _money(report.hwm_end)),
            ]
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("Fees (USDC)", styles["h2"]))
    story.append(
        _kv_table(
            [
                ("Strategy P&L (period NAV change)", _money(report.total_usdc_earned)),
                ("Distributable profit (above HWM)", _money(report.distributable_profit)),
                (
                    "Performance fee",
                    f"{_money(report.performance_fee)} ({float(report.performance_fee_rate) * 100:.0f}%)",
                ),
                ("Management fee", _money(report.management_fee)),
            ]
        )
    )
    _bullet(
        story,
        "Strategy P&L = NAV_perf change in the period. Performance fee applies to distributable profit "
        "(NAV_perf at Day B minus HWM at start minus net subscription).",
        styles["small"],
    )
    story.append(Spacer(1, 8))

    _append_flow_section_pdf(
        story,
        report.period_flow_lines,
        section_num=4,
        styles=styles,
        period_label=report.period_label,
    )

    story.append(Paragraph("5. Closed option trades", styles["h2"]))
    trade_rows = [
        [
            _ts_fmt_pdf(int(row["closed_timestamp_ms"])),
            str(row["account"]),
            str(row["strategy"]),
            str(row["short_instrument"])[:24],
            str(row["collateral"]),
            _money(row["realized_pnl_usdc"]),
        ]
        for row in report.closed_trades
    ]
    if not trade_rows:
        trade_rows = [["—", "—", "—", "—", "—", "—"]]
    story.append(
        _grid_table(
            ["Closed (UTC)", "Account", "Strategy", "Instrument", "Book", "PnL USDC"],
            trade_rows,
            col_widths=[
                36 * mm,
                22 * mm,
                22 * mm,
                40 * mm,
                12 * mm,
                (178 - 36 - 22 - 22 - 40 - 12) * mm,
            ],
        )
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Period statement — {report.investor_id}",
    )
    doc.build(story)
    return path
